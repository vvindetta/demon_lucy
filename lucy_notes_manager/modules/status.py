from __future__ import annotations

import os
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

_SECONDS_TICK_INTERVAL = 1.0
_GIT_FAST_TICK_INTERVAL = 2.0
_DEFAULT_TICK_INTERVAL = 60.0
_GIT_FAST_TICK_WINDOW_SECONDS = 120.0
_DEFAULT_BANNER_SPEED_MS = 500
_DEFAULT_BANNER_MAX_CHARS = 0


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
            "",
            "Animated filename banner text. Example: --status-banner \"Work sentence\"",
            False,
        ),
        (
            "--status-banner-speed-ms",
            int,
            _DEFAULT_BANNER_SPEED_MS,
            "Animated banner speed in milliseconds per step. Default: 500",
            False,
        ),
        (
            "--status-banner-max-chars",
            int,
            _DEFAULT_BANNER_MAX_CHARS,
            "Max visible banner width. 0 = unlimited. Default: 0",
            False,
        ),
        (
            "--status-prefix",
            str,
            "",
            "Prefix text inserted at the very beginning of the filename status. Example: --status-prefix 'Inbox: '",
            False,
        ),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._tracked_paths: dict[str, list[str]] = {}
        self._tracked_banners: dict[str, tuple[str, int, int]] = {}
        self._tracked_prefixes: dict[str, str] = {}
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
    def _is_date_token(token: str) -> bool:
        return (
            len(token) == 5
            and token[2] == "-"
            and token[:2].isdigit()
            and token[3:].isdigit()
        )

    @staticmethod
    def _is_time_token(token: str) -> bool:
        return (
            len(token) == 5
            and token[2] == ":"
            and token[:2].isdigit()
            and token[3:].isdigit()
        )

    @staticmethod
    def _is_time_seconds_token(token: str) -> bool:
        return (
            len(token) == 8
            and token[2] == ":"
            and token[5] == ":"
            and token[:2].isdigit()
            and token[3:5].isdigit()
            and token[6:].isdigit()
        )

    @staticmethod
    def _is_git_update_token(token: str) -> bool:
        if len(token) < 2:
            return False
        unit = token[-1].lower()
        return unit in ("m", "h") and token[:-1].isdigit()

    @staticmethod
    def _split_status_prefix(
        stem: str,
        status_prefix: str = "",
    ) -> tuple[dict[str, str], str]:
        tokens: dict[str, str] = {}
        text = stem.replace("\u200b", " ").strip().replace(" | ", " ")
        normalized_prefix = str(status_prefix or "")
        if normalized_prefix and text.startswith(normalized_prefix):
            text = text[len(normalized_prefix) :].lstrip()
        parts = text.split()
        consumed = 0

        for part in parts:
            if Status._is_date_token(part):
                tokens.setdefault("d", part)
                consumed += 1
                continue
            if Status._is_time_seconds_token(part):
                tokens.setdefault("ts", part)
                consumed += 1
                continue
            if Status._is_time_token(part):
                tokens.setdefault("t", part)
                consumed += 1
                continue
            if Status._is_git_update_token(part):
                tokens.setdefault("g_update", part)
                consumed += 1
                continue
            if part.isdigit():
                tokens.setdefault("g_sync", part)
                consumed += 1
                continue
            break

        clean_parts = parts[consumed:]
        clean = " ".join(clean_parts).strip() if clean_parts else ""
        return tokens, (clean or stem.strip())

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

    def _normalize_banner_settings(
        self,
        text_value: object,
        speed_ms_value: object,
        max_chars_value: object,
    ) -> tuple[str | None, int, int]:
        banner_text = str(text_value or "").strip()
        try:
            speed_ms = int(speed_ms_value)
        except (TypeError, ValueError):
            speed_ms = _DEFAULT_BANNER_SPEED_MS
        try:
            max_chars = int(max_chars_value)
        except (TypeError, ValueError):
            max_chars = _DEFAULT_BANNER_MAX_CHARS

        safe_speed_ms = max(1, speed_ms)
        safe_max_chars = max(0, max_chars)
        if not banner_text:
            return None, safe_speed_ms, safe_max_chars
        return banner_text, safe_speed_ms, safe_max_chars

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

    @staticmethod
    def _render_banner_frame(text: str, offset: int, max_chars: int) -> str:
        if not text:
            return ""
        if max_chars <= 0:
            return Status._rotate_banner_text(text, offset)

        width = max(1, int(max_chars))
        # Scroll left until text fully disappears into spaces, then restart.
        # Example (text=Working, width=4): Work -> orki -> rkin -> king -> ing  -> ng   -> g    -> "    " -> ...
        stream = text + (" " * width)
        cycle_len = len(stream)
        step = offset % cycle_len
        return stream[step : step + width].ljust(width)

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
        banner_max_chars: int,
        status_prefix: str,
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
                tokens.append(self._git_age_label(path))
                continue
            if part == "git_static":
                if existing_git_sync_token:
                    tokens.append(existing_git_sync_token)
                else:
                    tokens.append(self._git_sync_time_label(path))

        if banner_text:
            banner_frame = self._render_banner_frame(
                text=banner_text,
                offset=banner_offset,
                max_chars=banner_max_chars,
            )
            if banner_frame:
                tokens.append(banner_frame)

        prefix_text = str(status_prefix or "")
        if prefix_text and tokens:
            tokens[0] = f"{prefix_text}{tokens[0]}"

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
        banner_speed_ms: int = _DEFAULT_BANNER_SPEED_MS,
        banner_max_chars: int = _DEFAULT_BANNER_MAX_CHARS,
        status_prefix: str = "",
    ) -> None:
        abs_path = os.path.abspath(path)
        with self._track_lock:
            if self._needs_background_updates(parts, banner_text):
                self._tracked_paths[abs_path] = list(parts)
                self._tracked_prefixes[abs_path] = str(status_prefix or "")
                if banner_text:
                    safe_speed_ms = max(1, int(banner_speed_ms))
                    safe_max_chars = max(0, int(banner_max_chars))
                    previous_banner = self._tracked_banners.get(abs_path)
                    next_banner = (banner_text, safe_speed_ms, safe_max_chars)
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
            self._tracked_prefixes.pop(abs_path, None)
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
            prefix_text = self._tracked_prefixes.pop(old_abs, None)
            if prefix_text is not None:
                self._tracked_prefixes[new_abs] = prefix_text
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
                    self._tracked_prefixes.pop(path, None)
                    self._banner_offsets.pop(path, None)
                    self._banner_last_slots.pop(path, None)
                continue

            banner_text: str | None = None
            banner_offset = 0
            banner_max_chars = _DEFAULT_BANNER_MAX_CHARS
            status_prefix = ""
            with self._track_lock:
                banner_state = self._tracked_banners.get(path)
                if banner_state:
                    banner_text, banner_speed_ms, banner_max_chars = banner_state
                    speed_seconds = max(1, banner_speed_ms) / 1000.0
                    current_slot = int(now_ts // speed_seconds)
                    last_slot = self._banner_last_slots.get(path)
                    if last_slot is None:
                        self._banner_last_slots[path] = current_slot
                    elif current_slot != last_slot:
                        step_count = max(1, current_slot - last_slot)
                        self._banner_last_slots[path] = current_slot
                        next_offset = self._banner_offsets.get(path, 0) + step_count
                        self._banner_offsets[path] = next_offset
                    banner_offset = self._banner_offsets.get(path, 0)
                status_prefix = self._tracked_prefixes.get(path, "")

            self._apply(
                path=path,
                parts=parts,
                banner_text=banner_text,
                banner_offset=banner_offset,
                banner_max_chars=banner_max_chars,
                status_prefix=status_prefix,
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
                max(1, speed_ms) / 1000.0 for _text, speed_ms, _max_chars in tracked_banners
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
            for status_dir_name in (".status", ". status"):
                candidate = os.path.join(current, status_dir_name)
                if os.path.isdir(candidate):
                    result.append(os.path.abspath(candidate))
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent

        return result

    def _status_from_file(self, path: str) -> tuple[list[str], str | None, int, int, str]:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except (OSError, UnicodeDecodeError):
            return [], None, _DEFAULT_BANNER_SPEED_MS, _DEFAULT_BANNER_MAX_CHARS, ""

        status_values: list[str] = []
        banner_text_value: object = ""
        banner_speed_value: object = _DEFAULT_BANNER_SPEED_MS
        banner_max_chars_value: object = _DEFAULT_BANNER_MAX_CHARS
        status_prefix = ""
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if (
                "--status" not in stripped
                and "--status-banner" not in stripped
                and "--status-banner-speed-ms" not in stripped
                and "--status-banner-max-chars" not in stripped
                and "--status-prefix" not in stripped
            ):
                continue
            try:
                tokens = shlex.split(stripped, comments=False, posix=True)
            except ValueError:
                continue

            i = 0
            while i < len(tokens):
                token_head = tokens[i]
                if token_head not in (
                    "--status",
                    "--status-banner",
                    "--status-banner-speed-ms",
                    "--status-banner-max-chars",
                    "--status-prefix",
                ):
                    i += 1
                    continue

                if token_head == "--status-prefix":
                    if i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
                        status_prefix = tokens[i + 1]
                        i += 2
                    else:
                        i += 1
                    continue

                if token_head == "--status-banner":
                    if i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
                        banner_text_value = tokens[i + 1]
                        i += 2
                    else:
                        i += 1
                    continue

                if token_head == "--status-banner-speed-ms":
                    if i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
                        banner_speed_value = tokens[i + 1]
                        i += 2
                    else:
                        i += 1
                    continue

                if token_head == "--status-banner-max-chars":
                    if i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
                        banner_max_chars_value = tokens[i + 1]
                        i += 2
                    else:
                        i += 1
                    continue

                j = i + 1
                while j < len(tokens):
                    token = tokens[j]
                    if token.startswith("--"):
                        break
                    status_values.append(token)
                    j += 1
                i = j

        parts = self._parse_status_parts(status_values)
        banner_text, banner_speed_ms, banner_max_chars = self._normalize_banner_settings(
            banner_text_value,
            banner_speed_value,
            banner_max_chars_value,
        )
        return parts, banner_text, banner_speed_ms, banner_max_chars, status_prefix

    def _bootstrap_from_status_dirs(self, event_path: str) -> Optional[IgnoreMap]:
        status_dirs = self._discover_status_dirs_from_path(event_path)
        if not status_dirs:
            return None

        merged: Optional[IgnoreMap] = None
        for status_dir in status_dirs:
            for root, _dirs, files in os.walk(status_dir):
                for file_name in files:
                    file_path = os.path.abspath(os.path.join(root, file_name))
                    (
                        parts,
                        banner_text,
                        banner_speed_ms,
                        banner_max_chars,
                        status_prefix,
                    ) = self._status_from_file(file_path)
                    if not parts and not banner_text and not status_prefix:
                        self._set_tracked_parts(path=file_path, parts=[], banner_text=None)
                        continue
                    self._set_tracked_parts(
                        path=file_path,
                        parts=parts,
                        banner_text=banner_text,
                        banner_speed_ms=banner_speed_ms,
                        banner_max_chars=banner_max_chars,
                        status_prefix=status_prefix,
                    )
                    with self._track_lock:
                        banner_offset = self._banner_offsets.get(file_path, 0)
                    changed = self._apply(
                        path=file_path,
                        parts=parts,
                        banner_text=banner_text,
                        banner_offset=banner_offset,
                        banner_max_chars=banner_max_chars,
                        status_prefix=status_prefix,
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
        banner_max_chars: int = _DEFAULT_BANNER_MAX_CHARS,
        status_prefix: str | None = None,
    ) -> Optional[IgnoreMap]:
        with self._rename_lock:
            old_path = os.path.abspath(path)
            if os.path.isdir(old_path) or not os.path.exists(old_path):
                return None

            base_name = os.path.basename(old_path)
            stem, _ext = os.path.splitext(base_name)
            existing_tokens, _clean_stem = self._split_status_prefix(
                stem=stem,
                status_prefix=str(status_prefix or ""),
            )
            if banner_text is None:
                with self._track_lock:
                    banner_state = self._tracked_banners.get(old_path)
                    if banner_state:
                        banner_text = banner_state[0]
                        banner_max_chars = banner_state[2]
                        banner_offset = self._banner_offsets.get(old_path, 0)
            if status_prefix is None:
                with self._track_lock:
                    status_prefix = self._tracked_prefixes.get(old_path, "")

            tokens = self._build_tokens(
                path=old_path,
                parts=parts,
                existing_git_sync_token=existing_tokens.get("g_sync"),
                banner_text=banner_text,
                banner_offset=banner_offset,
                banner_max_chars=banner_max_chars,
                status_prefix=str(status_prefix or ""),
            )
            if not tokens:
                return None

            # Keep plain spaces in filenames for readability.
            new_name = " ".join(tokens)
            if not new_name.strip():
                new_name = " - "
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
        banner_text, banner_speed_ms, banner_max_chars = self._normalize_banner_settings(
            ctx.config.get("status_banner", ""),
            ctx.config.get("status_banner_speed_ms", _DEFAULT_BANNER_SPEED_MS),
            ctx.config.get("status_banner_max_chars", _DEFAULT_BANNER_MAX_CHARS),
        )
        status_prefix = str(ctx.config.get("status_prefix", ""))
        self._set_tracked_parts(
            path=ctx.path,
            parts=parts,
            banner_text=banner_text,
            banner_speed_ms=banner_speed_ms,
            banner_max_chars=banner_max_chars,
            status_prefix=status_prefix,
        )
        with self._track_lock:
            banner_offset = self._banner_offsets.get(os.path.abspath(ctx.path), 0)
        current_changed = self._apply(
            path=ctx.path,
            parts=parts,
            banner_text=banner_text,
            banner_offset=banner_offset,
            banner_max_chars=banner_max_chars,
            status_prefix=status_prefix,
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
