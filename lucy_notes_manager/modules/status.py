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
_GIT_UPDATE_RE = re.compile(
    r"^(?:From last sync|From last Git sync|From git):\s*(\d+(?:[mh])?)$"
)
_DATE_PREFIX_RE = re.compile(r"^(\d{2}-\d{2})(?:\s+|$)")
_TIME_SECONDS_PREFIX_RE = re.compile(r"^(\d{2}:\d{2}:\d{2})(?:\s+|$)")
_TIME_PREFIX_RE = re.compile(r"^(\d{2}:\d{2})(?:\s+|$)")
_GIT_STATIC_PREFIX_RE = re.compile(r"^(?:Last Git Sync|Git):\s*(\d+)(?:\s+|$)")
_GIT_UPDATE_PREFIX_RE = re.compile(
    r"^(?:From last sync|From last Git sync|From git):\s*(\d+(?:[mh])?)(?:\s+|$)"
)
_SECONDS_TICK_INTERVAL = 1.0
_GIT_FAST_TICK_INTERVAL = 2.0
_DEFAULT_TICK_INTERVAL = 60.0
_GIT_FAST_TICK_WINDOW_SECONDS = 120.0
_DEFAULT_BANNER_SPEED_SECONDS = 1


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
        (
            "--status-banner",
            str,
            [],
            "Animated filename banner. Syntax: --status-banner \"Work sentence\" 2 (speed in seconds).",
            False,
        ),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._tracked_paths: dict[str, list[str]] = {}
        self._tracked_banners: dict[str, tuple[str, int]] = {}
        self._banner_offsets: dict[str, int] = {}
        self._banner_last_slots: dict[str, int] = {}
        self._track_lock = threading.Lock()
        self._rename_lock = threading.Lock()
        self._bootstrap_lock = threading.Lock()
        self._bootstrap_done = False
        self._ticker_stop = threading.Event()
        self._last_tick_key: tuple[float, int] | None = None
        self._git_fast_tick_until = 0.0
        self._ticker_thread: threading.Thread | None = None

    @staticmethod
    def _split_status_prefix(stem: str) -> tuple[dict[str, str], str]:
        tokens: dict[str, str] = {}
        text = stem.strip().replace(" | ", " ")
        matched_count = 0

        while text:
            date_match = _DATE_PREFIX_RE.match(text)
            if date_match:
                tokens["d"] = date_match.group(1)
                matched_count += 1
                text = text[date_match.end() :].lstrip()
                continue

            time_seconds_match = _TIME_SECONDS_PREFIX_RE.match(text)
            if time_seconds_match:
                tokens["ts"] = time_seconds_match.group(1)
                matched_count += 1
                text = text[time_seconds_match.end() :].lstrip()
                continue

            time_match = _TIME_PREFIX_RE.match(text)
            if time_match:
                tokens["t"] = time_match.group(1)
                matched_count += 1
                text = text[time_match.end() :].lstrip()
                continue

            static_match = _GIT_STATIC_PREFIX_RE.match(text)
            if static_match:
                tokens["g_sync"] = static_match.group(1)
                matched_count += 1
                text = text[static_match.end() :].lstrip()
                continue

            update_match = _GIT_UPDATE_PREFIX_RE.match(text)
            if update_match:
                tokens["g_update"] = update_match.group(1)
                matched_count += 1
                text = text[update_match.end() :].lstrip()
                continue

            break

        if matched_count > 0:
            clean = text.strip() or stem.strip()
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

    def _parse_status_banner(self, values: list[str]) -> tuple[str | None, int]:
        cleaned = [str(value).strip() for value in values if str(value).strip()]
        if not cleaned:
            return None, _DEFAULT_BANNER_SPEED_SECONDS

        speed = _DEFAULT_BANNER_SPEED_SECONDS
        text_tokens = list(cleaned)
        maybe_speed = cleaned[-1]
        if maybe_speed.isdigit():
            parsed_speed = int(maybe_speed)
            if parsed_speed > 0:
                speed = parsed_speed
                if len(cleaned) > 1:
                    text_tokens = cleaned[:-1]

        banner_text = " ".join(text_tokens).strip()
        if not banner_text:
            return None, speed
        return banner_text, speed

    @staticmethod
    def _rotate_banner_text(text: str, offset: int) -> str:
        if not text:
            return ""
        if len(text) == 1:
            return text
        safe_offset = offset % len(text)
        if safe_offset == 0:
            return text
        return text[safe_offset:] + text[:safe_offset]

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

    def _git_age_label(self, path: str) -> str:
        last_commit_ts = self._git_last_commit_timestamp(path)
        if last_commit_ts is None:
            return "0m"
        age_minutes = int(max(0.0, time.time() - last_commit_ts) // 60.0)
        if age_minutes >= 60:
            return f"{age_minutes // 60}h"
        return f"{age_minutes}m"

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
        banner_text: str | None,
        banner_offset: int,
    ) -> list[str]:
        if not parts and not banner_text:
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
                tokens.append(f"From last sync: {self._git_age_label(path)}")
                continue
            if part == "git_static":
                if existing_git_sync_token:
                    tokens.append(f"Last Git Sync: {existing_git_sync_token}")
                else:
                    tokens.append(f"Last Git Sync: {self._git_sync_time_label(path)}")

        if banner_text:
            tokens.append(self._rotate_banner_text(banner_text, banner_offset))

        return tokens

    @staticmethod
    def _needs_background_updates(parts: list[str], banner_text: str | None) -> bool:
        if banner_text:
            return True
        return any(
            part in ("date", "time", "time_with_seconds", "git_update")
            for part in parts
        )

    def _set_tracked_parts(
        self,
        path: str,
        parts: list[str],
        banner_text: str | None = None,
        banner_speed: int = _DEFAULT_BANNER_SPEED_SECONDS,
    ) -> None:
        abs_path = os.path.abspath(path)
        with self._track_lock:
            if self._needs_background_updates(parts, banner_text):
                self._tracked_paths[abs_path] = list(parts)
                if banner_text:
                    safe_speed = max(_DEFAULT_BANNER_SPEED_SECONDS, int(banner_speed))
                    previous_banner = self._tracked_banners.get(abs_path)
                    next_banner = (banner_text, safe_speed)
                    if previous_banner != next_banner:
                        self._banner_offsets[abs_path] = 0
                        self._banner_last_slots.pop(abs_path, None)
                    self._tracked_banners[abs_path] = next_banner
                else:
                    self._tracked_banners.pop(abs_path, None)
                    self._banner_offsets.pop(abs_path, None)
                    self._banner_last_slots.pop(abs_path, None)
                if "git_update" in parts:
                    self._git_fast_tick_until = max(
                        self._git_fast_tick_until,
                        time.time() + _GIT_FAST_TICK_WINDOW_SECONDS,
                    )
                self._ensure_ticker_started()
                return
            self._tracked_paths.pop(abs_path, None)
            self._tracked_banners.pop(abs_path, None)
            self._banner_offsets.pop(abs_path, None)
            self._banner_last_slots.pop(abs_path, None)

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
            if parts is not None:
                self._tracked_paths[new_abs] = list(parts)
            banner = self._tracked_banners.pop(old_abs, None)
            if banner:
                self._tracked_banners[new_abs] = banner
            offset = self._banner_offsets.pop(old_abs, None)
            if offset is not None:
                self._banner_offsets[new_abs] = offset
            last_slot = self._banner_last_slots.pop(old_abs, None)
            if last_slot is not None:
                self._banner_last_slots[new_abs] = last_slot

    def _tick_once(self) -> None:
        now_ts = time.time()
        with self._track_lock:
            tracked_items = list(self._tracked_paths.items())

        for path, parts in tracked_items:
            if not os.path.exists(path):
                with self._track_lock:
                    self._tracked_paths.pop(path, None)
                    self._tracked_banners.pop(path, None)
                    self._banner_offsets.pop(path, None)
                    self._banner_last_slots.pop(path, None)
                continue

            banner_text: str | None = None
            banner_offset = 0
            with self._track_lock:
                banner_state = self._tracked_banners.get(path)
                if banner_state:
                    banner_text, banner_speed = banner_state
                    current_slot = int(now_ts // max(_DEFAULT_BANNER_SPEED_SECONDS, banner_speed))
                    last_slot = self._banner_last_slots.get(path)
                    if last_slot is None:
                        self._banner_last_slots[path] = current_slot
                    elif current_slot != last_slot:
                        self._banner_last_slots[path] = current_slot
                        next_offset = self._banner_offsets.get(path, 0) + 1
                        self._banner_offsets[path] = next_offset
                    banner_offset = self._banner_offsets.get(path, 0)

            self._apply(
                path=path,
                parts=parts,
                banner_text=banner_text,
                banner_offset=banner_offset,
            )

    def _ticker_interval_seconds(self) -> float:
        now_ts = time.time()
        with self._track_lock:
            tracked_parts = list(self._tracked_paths.values())
            tracked_banners = list(self._tracked_banners.values())
            has_seconds = any("time_with_seconds" in parts for parts in tracked_parts)
            has_git_update = any("git_update" in parts for parts in tracked_parts)
            fast_until = self._git_fast_tick_until

        interval = _DEFAULT_TICK_INTERVAL
        if has_seconds:
            interval = min(interval, _SECONDS_TICK_INTERVAL)
        if has_git_update and now_ts < fast_until:
            interval = min(interval, _GIT_FAST_TICK_INTERVAL)
        if tracked_banners:
            min_banner_speed = min(
                max(_DEFAULT_BANNER_SPEED_SECONDS, speed)
                for _text, speed in tracked_banners
            )
            interval = min(interval, float(min_banner_speed))
        return interval

    def _ticker_loop(self) -> None:
        while not self._ticker_stop.is_set():
            interval_seconds = self._ticker_interval_seconds()
            current_slot = int(time.time() // interval_seconds)
            tick_key = (interval_seconds, current_slot)
            if tick_key != self._last_tick_key:
                self._last_tick_key = tick_key
                self._tick_once()
            wait_seconds = 0.25 if interval_seconds <= _GIT_FAST_TICK_INTERVAL else 1.0
            self._ticker_stop.wait(wait_seconds)

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
            candidate = os.path.join(current, ".status")
            if os.path.isdir(candidate):
                result.append(os.path.abspath(candidate))
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent

        return result

    def _status_from_file(self, path: str) -> tuple[list[str], str | None, int]:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except (OSError, UnicodeDecodeError):
            return [], None, _DEFAULT_BANNER_SPEED_SECONDS

        status_values: list[str] = []
        banner_values: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "--status" not in stripped and "--status-banner" not in stripped:
                continue
            try:
                tokens = shlex.split(stripped, comments=False, posix=True)
            except ValueError:
                continue

            i = 0
            while i < len(tokens):
                token_head = tokens[i]
                if token_head not in ("--status", "--status-banner"):
                    i += 1
                    continue

                j = i + 1
                while j < len(tokens):
                    token = tokens[j]
                    if token.startswith("--"):
                        break
                    if token_head == "--status":
                        status_values.append(token)
                    else:
                        banner_values.append(token)
                    j += 1
                i = j

        parts = self._parse_status_parts(status_values)
        banner_text, banner_speed = self._parse_status_banner(banner_values)
        return parts, banner_text, banner_speed

    def _bootstrap_from_status_dirs(self, event_path: str) -> Optional[IgnoreMap]:
        status_dirs = self._discover_status_dirs_from_path(event_path)
        if not status_dirs:
            return None

        merged: Optional[IgnoreMap] = None
        for status_dir in status_dirs:
            for root, _dirs, files in os.walk(status_dir):
                for file_name in files:
                    file_path = os.path.abspath(os.path.join(root, file_name))
                    parts, banner_text, banner_speed = self._status_from_file(file_path)
                    if not parts and not banner_text:
                        self._set_tracked_parts(path=file_path, parts=[], banner_text=None)
                        continue
                    self._set_tracked_parts(
                        path=file_path,
                        parts=parts,
                        banner_text=banner_text,
                        banner_speed=banner_speed,
                    )
                    with self._track_lock:
                        banner_offset = self._banner_offsets.get(file_path, 0)
                    changed = self._apply(
                        path=file_path,
                        parts=parts,
                        banner_text=banner_text,
                        banner_offset=banner_offset,
                    )
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

    def _apply(
        self,
        path: str,
        parts: list[str],
        banner_text: str | None = None,
        banner_offset: int = 0,
    ) -> Optional[IgnoreMap]:
        with self._rename_lock:
            old_path = os.path.abspath(path)
            if os.path.isdir(old_path) or not os.path.exists(old_path):
                return None

            base_name = os.path.basename(old_path)
            stem, _ext = os.path.splitext(base_name)
            existing_tokens, _clean_stem = self._split_status_prefix(stem)
            if banner_text is None:
                with self._track_lock:
                    banner_state = self._tracked_banners.get(old_path)
                    if banner_state:
                        banner_text = banner_state[0]
                        banner_offset = self._banner_offsets.get(old_path, 0)

            tokens = self._build_tokens(
                path=old_path,
                parts=parts,
                existing_git_sync_token=existing_tokens.get("g_sync"),
                banner_text=banner_text,
                banner_offset=banner_offset,
            )
            if not tokens:
                return None

            new_name = " ".join(tokens).strip()
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
        banner_text, banner_speed = self._parse_status_banner(
            list(ctx.config.get("status_banner", []))
        )
        self._set_tracked_parts(
            path=ctx.path,
            parts=parts,
            banner_text=banner_text,
            banner_speed=banner_speed,
        )
        with self._track_lock:
            banner_offset = self._banner_offsets.get(os.path.abspath(ctx.path), 0)
        current_changed = self._apply(
            path=ctx.path,
            parts=parts,
            banner_text=banner_text,
            banner_offset=banner_offset,
        )
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
