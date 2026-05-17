from __future__ import annotations

import os
import re
import shlex
import subprocess
import threading
import time
from datetime import UTC, datetime
from typing import Optional

from lucy_notes_manager.lib.args import Template
from lucy_notes_manager.lib.path import find_parent_with
from lucy_notes_manager.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)

_STATUS_TOKEN_RE = re.compile(r"^\[(d|t|g):([^\]]+)\]\s*")


class Status(AbstractModule):
    name: str = "status"
    priority: int = 21

    template: Template = [
        (
            "--status-time",
            bool,
            False,
            "Prefix filename with current time (HH:MM). Updates when the minute changes.",
            False,
        ),
        (
            "--status-date",
            bool,
            False,
            "Prefix filename with current date (YYYY-MM-DD). Updates when the day changes.",
            False,
        ),
        (
            "--status-git",
            bool,
            False,
            "Prefix filename with time since last repository commit.",
            False,
        ),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._tracked_paths: dict[str, dict[str, bool]] = {}
        self._track_lock = threading.Lock()
        self._rename_lock = threading.Lock()
        self._ticker_stop = threading.Event()
        self._last_tick_minute: tuple[int, int, int, int, int] | None = None
        self._ticker_thread: threading.Thread | None = None

    @staticmethod
    def _split_status_prefix(stem: str) -> tuple[dict[str, str], str]:
        text = stem.strip()
        tokens: dict[str, str] = {}
        while True:
            matched = _STATUS_TOKEN_RE.match(text)
            if not matched:
                break
            tokens[matched.group(1)] = matched.group(2).strip()
            text = text[matched.end() :].lstrip()
        return tokens, (text.strip() or stem.strip())

    @staticmethod
    def _format_age(seconds: float) -> str:
        safe_seconds = max(0.0, float(seconds))

        if safe_seconds < 3600.0:
            minutes = int(safe_seconds // 60.0)
            return f"{minutes}m"

        if safe_seconds < 86400.0:
            hours = int(safe_seconds // 3600.0)
            return f"{hours}h"

        days = int(safe_seconds // 86400.0)
        return f"{days}d"

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
            return "na"
        return self._format_age(time.time() - last_commit_ts)

    def _git_sync_time_label(self, path: str) -> str:
        last_commit_ts = self._git_last_commit_timestamp(path)
        if last_commit_ts is None:
            return "na"
        return datetime.fromtimestamp(last_commit_ts, UTC).strftime("%Y-%m-%d_%H-%M")

    def _build_tokens(
        self,
        path: str,
        config: dict,
        existing_git_token: str | None,
    ) -> list[str]:
        if not (
            config.get("status_time")
            or config.get("status_date")
            or config.get("status_git")
        ):
            return []

        now = datetime.now()
        tokens: list[str] = []

        if config.get("status_date"):
            tokens.append(f"d:{now.strftime('%Y-%m-%d')}")

        if config.get("status_time"):
            tokens.append(f"t:{now.strftime('%H:%M')}")

        if config.get("status_git"):
            if config.get("status_git_update"):
                tokens.append(f"g:{self._git_age_label(path)}")
            elif existing_git_token:
                tokens.append(f"g:{existing_git_token}")
            else:
                tokens.append(f"g:{self._git_sync_time_label(path)}")

        return tokens

    @staticmethod
    def _status_flags(config: dict) -> dict[str, bool]:
        return {
            "status_time": bool(config.get("status_time")),
            "status_date": bool(config.get("status_date")),
            "status_git": bool(config.get("status_git")),
            "status_git_update": bool(config.get("status_git_update")),
        }

    @staticmethod
    def _has_any_status(config: dict) -> bool:
        return bool(
            config.get("status_time")
            or config.get("status_date")
            or config.get("status_git")
        )

    @staticmethod
    def _needs_background_updates(config: dict) -> bool:
        return bool(
            config.get("status_time")
            or config.get("status_date")
            or (config.get("status_git") and config.get("status_git_update"))
        )

    def _set_tracked_flags(self, path: str, config: dict) -> None:
        abs_path = os.path.abspath(path)
        with self._track_lock:
            if self._needs_background_updates(config):
                self._tracked_paths[abs_path] = self._status_flags(config)
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
            flags = self._tracked_paths.pop(old_abs, None)
            if flags:
                self._tracked_paths[new_abs] = dict(flags)

    def _tick_once(self) -> None:
        with self._track_lock:
            tracked_items = list(self._tracked_paths.items())

        for path, flags in tracked_items:
            if not os.path.exists(path):
                with self._track_lock:
                    self._tracked_paths.pop(path, None)
                continue
            self._apply(path=path, config=flags)

    def _ticker_loop(self) -> None:
        while not self._ticker_stop.is_set():
            now = datetime.now()
            minute_key = (now.year, now.month, now.day, now.hour, now.minute)
            if minute_key != self._last_tick_minute:
                self._last_tick_minute = minute_key
                self._tick_once()
            self._ticker_stop.wait(1.0)

    def _apply(self, path: str, config: dict) -> Optional[IgnoreMap]:
        with self._rename_lock:
            old_path = os.path.abspath(path)
            if os.path.isdir(old_path) or not os.path.exists(old_path):
                return None

            base_name = os.path.basename(old_path)
            stem, ext = os.path.splitext(base_name)
            existing_tokens, clean_stem = self._split_status_prefix(stem)
            if not clean_stem:
                clean_stem = stem or "note"

            tokens = self._build_tokens(
                path=old_path,
                config=config,
                existing_git_token=existing_tokens.get("g"),
            )
            if not tokens:
                return None

            prefix = " ".join(f"[{token}]" for token in tokens)
            new_stem = f"{prefix} {clean_stem}".strip()
            new_name = f"{new_stem}{ext}"

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

    def _status_git_update_enabled(self, ctx: Context) -> bool:
        if not ctx.config.get("status_git"):
            return False

        line_numbers = ctx.arg_lines.get("status_git") or []
        if not line_numbers:
            return False

        try:
            with open(ctx.path, "r", encoding="utf-8") as handle:
                file_lines = handle.readlines()
        except OSError:
            return False

        for lineno_1based in line_numbers:
            idx = int(lineno_1based) - 1
            if idx < 0 or idx >= len(file_lines):
                continue

            try:
                tokens = shlex.split(file_lines[idx], comments=False, posix=True)
            except ValueError:
                continue

            for i, token in enumerate(tokens):
                if token != "--status-git":
                    continue
                next_token = tokens[i + 1].strip().lower() if i + 1 < len(tokens) else ""
                if next_token == "update":
                    return True

        return False

    def _handle_event(self, ctx: Context) -> Optional[IgnoreMap]:
        config = dict(ctx.config)
        config["status_git_update"] = self._status_git_update_enabled(ctx)
        self._set_tracked_flags(path=ctx.path, config=config)
        return self._apply(path=ctx.path, config=config)

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
