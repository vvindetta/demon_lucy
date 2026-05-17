from __future__ import annotations

import os
import re
import shlex
import subprocess
import threading
import time
from datetime import datetime
from typing import Optional

from lucy_notes_manager.lib.args import Template
from lucy_notes_manager.lib.path import find_parent_with
from lucy_notes_manager.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)

_OLD_STATUS_TOKEN_RE = re.compile(r"^\[(d|t|g):([^\]]+)\]\s*")
_DATE_TOKEN_RE = re.compile(r"^\d{2}-\d{2}$")
_TIME_TOKEN_RE = re.compile(r"^\d{2}:\d{2}$")
_TIME_SECONDS_TOKEN_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")
_GIT_STATIC_RE = re.compile(r"^(?:Last Git Sync|Git):\s*(\d+)$")
_GIT_UPDATE_RE = re.compile(r"^(?:From last Git sync|From git):\s*(\d+)$")


class Status(AbstractModule):
    name: str = "status"
    priority: int = 21

    template: Template = [
        (
            "--status",
            str,
            [],
            "Filename status tokens. Examples: --status date OR --status time date OR --status time-with-seconds OR --status git OR --status git update",
            False,
        ),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._tracked_paths: dict[str, list[str]] = {}
        self._track_lock = threading.Lock()
        self._rename_lock = threading.Lock()
        self._bootstrap_lock = threading.Lock()
        self._bootstrap_done = False
        self._ticker_stop = threading.Event()
        self._last_tick_minute: tuple[int, int, int, int, int] | None = None
        self._ticker_thread: threading.Thread | None = None

    @staticmethod
    def _split_status_prefix(stem: str) -> tuple[dict[str, str], str]:
        text = stem.strip()
        parts = text.split(" | ")
        tokens: dict[str, str] = {}

        index = 0
        while index < len(parts):
            part = parts[index].strip()
            if _DATE_TOKEN_RE.fullmatch(part):
                tokens["d"] = part
                index += 1
                continue

            if _TIME_TOKEN_RE.fullmatch(part):
                tokens["t"] = part
                index += 1
                continue

            if _TIME_SECONDS_TOKEN_RE.fullmatch(part):
                tokens["ts"] = part
                index += 1
                continue

            static_match = _GIT_STATIC_RE.fullmatch(part)
            if static_match:
                tokens["g_sync"] = static_match.group(1)
                index += 1
                continue

            update_match = _GIT_UPDATE_RE.fullmatch(part)
            if update_match:
                tokens["g_update"] = update_match.group(1)
                index += 1
                continue

            break

        if index > 0:
            clean = " | ".join(parts[index:]).strip() or stem.strip()
            return tokens, clean

        # Backward compatibility: migrate old [d:..] [t:..] [g:..] names.
        while True:
            matched = _OLD_STATUS_TOKEN_RE.match(text)
            if not matched:
                break
            key = matched.group(1)
            value = matched.group(2).strip()
            if key == "d":
                tokens["d"] = value
            elif key == "t":
                tokens["t"] = value
            elif key == "g" and value.isdigit():
                tokens["g_sync"] = value
            text = text[matched.end() :].lstrip()

        return tokens, (text.strip() or stem.strip())

    @staticmethod
    def _normalize_status_token(raw: str) -> str:
        return str(raw).strip().lower().replace("_", "-")

    def _parse_status_parts(self, values: list[str]) -> list[str]:
        parts: list[str] = []
        seen: set[str] = set()

        def _append(part: str) -> None:
            if part in seen:
                return
            seen.add(part)
            parts.append(part)

        i = 0
        while i < len(values):
            token = self._normalize_status_token(values[i])
            nxt = self._normalize_status_token(values[i + 1]) if i + 1 < len(values) else ""

            if token in ("date", "d"):
                _append("date")
                i += 1
                continue

            if token in ("time", "t"):
                _append("time")
                i += 1
                continue

            if token in (
                "time-with-seconds",
                "time-seconds",
                "time-sec",
                "timesec",
                "ts",
            ):
                _append("time_with_seconds")
                i += 1
                continue

            if token in ("git", "g"):
                if nxt in ("update", "u"):
                    _append("git_update")
                    i += 2
                    continue
                _append("git_static")
                i += 1
                continue

            if token in ("git-update", "from-git", "update-git"):
                _append("git_update")
                i += 1
                continue

            i += 1

        return parts

    def _git_last_commit_timestamp(self, path: str) -> Optional[float]:
        repo_root = find_parent_with(path, ".git")
        if not repo_root:
            return None

        try:
            result = subprocess.run(
                ["git", "log", "-1", "--format=%ct"],
                cwd=repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=2.0,
            )
        except (OSError, subprocess.SubprocessError):
            return None

        if result.returncode != 0:
            return None

        raw_timestamp = (result.stdout or "").strip()
        if not raw_timestamp:
            return None

        try:
            return float(raw_timestamp)
        except ValueError:
            return None

    def _git_age_minutes_label(self, path: str) -> str:
        last_commit_ts = self._git_last_commit_timestamp(path)
        if last_commit_ts is None:
            return "0"
        age_minutes = int(max(0.0, time.time() - last_commit_ts) // 60.0)
        return str(age_minutes)

    def _git_sync_time_label(self, path: str) -> str:
        last_commit_ts = self._git_last_commit_timestamp(path)
        if last_commit_ts is None:
            return "0"
        return str(int(last_commit_ts))

    def _build_tokens(
        self,
        path: str,
        parts: list[str],
        existing_git_sync_token: str | None,
    ) -> list[str]:
        if not parts:
            return []

        now = datetime.now()
        tokens: list[str] = []

        for part in parts:
            if part == "date":
                tokens.append(now.strftime("%d-%m"))
                continue
            if part == "time":
                tokens.append(now.strftime("%H:%M"))
                continue
            if part == "time_with_seconds":
                tokens.append(now.strftime("%H:%M:%S"))
                continue
            if part == "git_update":
                tokens.append(f"From last Git sync: {self._git_age_minutes_label(path)}")
                continue
            if part == "git_static":
                if existing_git_sync_token:
                    tokens.append(f"Last Git Sync: {existing_git_sync_token}")
                else:
                    tokens.append(f"Last Git Sync: {self._git_sync_time_label(path)}")

        return tokens

    @staticmethod
    def _needs_background_updates(parts: list[str]) -> bool:
        return any(
            part in ("date", "time", "time_with_seconds", "git_update")
            for part in parts
        )

    def _set_tracked_parts(self, path: str, parts: list[str]) -> None:
        abs_path = os.path.abspath(path)
        with self._track_lock:
            if self._needs_background_updates(parts):
                self._tracked_paths[abs_path] = list(parts)
                self._ensure_ticker_started()
                return
            self._tracked_paths.pop(abs_path, None)

    def _ensure_ticker_started(self) -> None:
        if self._ticker_thread is not None:
            return
        self._ticker_thread = threading.Thread(
            target=self._ticker_loop,
            daemon=True,
        )
        self._ticker_thread.start()

    def _move_tracked_path(self, old_path: str, new_path: str) -> None:
        old_abs = os.path.abspath(old_path)
        new_abs = os.path.abspath(new_path)
        with self._track_lock:
            parts = self._tracked_paths.pop(old_abs, None)
            if parts:
                self._tracked_paths[new_abs] = list(parts)

    def _tick_once(self) -> None:
        with self._track_lock:
            tracked_items = list(self._tracked_paths.items())

        for path, parts in tracked_items:
            if not os.path.exists(path):
                with self._track_lock:
                    self._tracked_paths.pop(path, None)
                continue
            self._apply(path=path, parts=parts)

    def _ticker_loop(self) -> None:
        while not self._ticker_stop.is_set():
            now = datetime.now()
            minute_key = (now.year, now.month, now.day, now.hour, now.minute)
            if minute_key != self._last_tick_minute:
                self._last_tick_minute = minute_key
                self._tick_once()
            self._ticker_stop.wait(1.0)

    @staticmethod
    def _merge_ignore_maps(
        left: Optional[IgnoreMap],
        right: Optional[IgnoreMap],
    ) -> Optional[IgnoreMap]:
        if not left and not right:
            return None
        merged: IgnoreMap = {}
        for source in (left or {}, right or {}):
            for path, times in source.items():
                if not times:
                    continue
                merged[path] = merged.get(path, 0) + int(times)
        return merged or None

    @staticmethod
    def _discover_status_dirs_from_path(path: str) -> list[str]:
        result: list[str] = []
        current = os.path.abspath(path)
        if not os.path.isdir(current):
            current = os.path.dirname(current)

        while True:
            candidate = os.path.join(current, "_status")
            if os.path.isdir(candidate):
                result.append(os.path.abspath(candidate))
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent

        return result

    def _status_parts_from_file(self, path: str) -> list[str]:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except (OSError, UnicodeDecodeError):
            return []

        values: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "--status" not in stripped:
                continue
            try:
                tokens = shlex.split(stripped, comments=False, posix=True)
            except ValueError:
                continue

            i = 0
            while i < len(tokens):
                if tokens[i] != "--status":
                    i += 1
                    continue
                j = i + 1
                while j < len(tokens):
                    token = tokens[j]
                    if token.startswith("--"):
                        break
                    values.append(token)
                    j += 1
                i = j

        return self._parse_status_parts(values)

    def _bootstrap_from_status_dirs(self, event_path: str) -> Optional[IgnoreMap]:
        status_dirs = self._discover_status_dirs_from_path(event_path)
        if not status_dirs:
            return None

        merged: Optional[IgnoreMap] = None
        for status_dir in status_dirs:
            for root, _dirs, files in os.walk(status_dir):
                for file_name in files:
                    file_path = os.path.abspath(os.path.join(root, file_name))
                    parts = self._status_parts_from_file(file_path)
                    if not parts:
                        self._set_tracked_parts(path=file_path, parts=[])
                        continue
                    self._set_tracked_parts(path=file_path, parts=parts)
                    changed = self._apply(path=file_path, parts=parts)
                    merged = self._merge_ignore_maps(merged, changed)
        return merged

    def _bootstrap_once(self, event_path: str) -> Optional[IgnoreMap]:
        if self._bootstrap_done:
            return None
        with self._bootstrap_lock:
            if self._bootstrap_done:
                return None
            changed = self._bootstrap_from_status_dirs(event_path)
            self._bootstrap_done = True
            return changed

    def _apply(self, path: str, parts: list[str]) -> Optional[IgnoreMap]:
        with self._rename_lock:
            old_path = os.path.abspath(path)
            if os.path.isdir(old_path) or not os.path.exists(old_path):
                return None

            base_name = os.path.basename(old_path)
            stem, _ext = os.path.splitext(base_name)
            existing_tokens, _clean_stem = self._split_status_prefix(stem)

            tokens = self._build_tokens(
                path=old_path,
                parts=parts,
                existing_git_sync_token=existing_tokens.get("g_sync"),
            )
            if not tokens:
                return None

            new_name = " | ".join(tokens).strip()
            new_path = os.path.abspath(os.path.join(os.path.dirname(old_path), new_name))

            if new_path == old_path:
                return None

            if os.path.exists(new_path):
                return None

            try:
                os.rename(old_path, new_path)
            except (FileNotFoundError, OSError):
                return None

            self._move_tracked_path(old_path=old_path, new_path=new_path)
            return {old_path: 1, new_path: 1}

    def _handle_event(self, ctx: Context) -> Optional[IgnoreMap]:
        bootstrap_changed = self._bootstrap_once(ctx.path)
        parts = self._parse_status_parts(list(ctx.config.get("status", [])))
        self._set_tracked_parts(path=ctx.path, parts=parts)
        current_changed = self._apply(path=ctx.path, parts=parts)
        return self._merge_ignore_maps(bootstrap_changed, current_changed)

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        _ = system
        return self._handle_event(ctx)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        _ = system
        return self._handle_event(ctx)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        _ = system
        return self._handle_event(ctx)

    def opened(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        _ = system
        return self._handle_event(ctx)
