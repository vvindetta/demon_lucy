from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time
from datetime import datetime
from typing import Any, Optional

from lucy_notes_manager.lib.args import Template
from lucy_notes_manager.lib.path import find_parent_with
from lucy_notes_manager.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)
from lucy_notes_manager.modules.git.sync_marker import read_sync_success_timestamp

_SECONDS_TICK_INTERVAL = 1.0
_GIT_FAST_TICK_INTERVAL = 2.0
_DEFAULT_TICK_INTERVAL = 60.0
_GIT_FAST_TICK_WINDOW_SECONDS = 120.0
_DEFAULT_BANNER_SPEED_MS = 500
_DEFAULT_BANNER_MAX_CHARS = 0
_DEFAULT_ASCII_ANIMATION_SPEED_MS = 1000


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
            "--status-banner-speed-milliseconds",
            int,
            _DEFAULT_BANNER_SPEED_MS,
            "Animated banner speed in milliseconds per step. Default: 500",
            False,
        ),
        (
            "--status-banner-max-characters",
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
        (
            "--status-ascii-animation-frames",
            str,
            [],
            "ASCII animation frames for filename status. Example: --status-ascii-animation-frames \"pri\" \"prive\" \"privet\"",
            False,
        ),
        (
            "--status-ascii-animation-speed-milliseconds",
            int,
            _DEFAULT_ASCII_ANIMATION_SPEED_MS,
            "ASCII animation frame switch speed in milliseconds. Default: 1000",
            False,
        ),
        (
            "--status-opened-events-disable",
            bool,
            False,
            "Disable status updates for opened events.",
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
        self._tracked_ascii_animations: dict[str, tuple[list[str], int]] = {}
        self._ascii_frame_indices: dict[str, int] = {}
        self._ascii_last_switch_seconds: dict[str, float] = {}
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
        text_value: Any,
        speed_ms_value: Any,
        max_chars_value: Any,
    ) -> tuple[str | None, int, int]:
        banner_text = "" if text_value is None else str(text_value)
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
        if banner_text == "":
            return None, safe_speed_ms, safe_max_chars
        return banner_text, safe_speed_ms, safe_max_chars

    def _normalize_ascii_animation_settings(
        self,
        frames_value: Any,
        speed_ms_value: Any,
    ) -> tuple[list[str], int]:
        raw_frames: list[Any]
        if frames_value is None:
            raw_frames = []
        elif isinstance(frames_value, (list, tuple)):
            raw_frames = list(frames_value)
        else:
            raw_frames = [frames_value]

        frames = [str(frame) for frame in raw_frames if str(frame) != ""]

        try:
            speed_ms = int(speed_ms_value)
        except (TypeError, ValueError):
            speed_ms = _DEFAULT_ASCII_ANIMATION_SPEED_MS
        safe_speed_ms = max(1, speed_ms)
        return frames, safe_speed_ms

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

    @staticmethod
    def _sanitize_filename_text(name_text: str) -> str:
        invalid_chars = [os.sep, "\x00"]
        if os.altsep:
            invalid_chars.append(os.altsep)

        safe_name = str(name_text)
        for item in invalid_chars:
            safe_name = safe_name.replace(item, "_")
        return safe_name

    @staticmethod
    def _filename_max_bytes(dir_path: str) -> int:
        try:
            value = int(os.pathconf(dir_path, "PC_NAME_MAX"))
            return max(32, value)
        except (AttributeError, OSError, ValueError):
            return 255

    @staticmethod
    def _truncate_utf8_to_bytes(text: str, max_bytes: int) -> str:
        if max_bytes <= 0:
            return ""

        encoded = text.encode("utf-8")
        if len(encoded) <= max_bytes:
            return text

        clipped = encoded[:max_bytes]
        while clipped:
            try:
                return clipped.decode("utf-8")
            except UnicodeDecodeError as exc:
                clipped = clipped[: exc.start]
        return ""

    def _make_filename_candidate(self, dir_path: str, name_text: str) -> str:
        sanitized = self._sanitize_filename_text(name_text)
        max_bytes = self._filename_max_bytes(dir_path)
        clipped = self._truncate_utf8_to_bytes(sanitized, max_bytes)
        if clipped.strip():
            return clipped
        return " - "

    def _pick_available_new_path(
        self,
        *,
        old_path: str,
        dir_path: str,
        base_name: str,
    ) -> str | None:
        candidate_path = os.path.abspath(os.path.join(dir_path, base_name))
        if candidate_path == old_path or not os.path.exists(candidate_path):
            return candidate_path

        max_bytes = self._filename_max_bytes(dir_path)
        for index in range(2, 1000):
            suffix = f" ({index})"
            suffix_bytes = len(suffix.encode("utf-8"))
            if suffix_bytes >= max_bytes:
                return None

            prefix = self._truncate_utf8_to_bytes(base_name, max_bytes - suffix_bytes)
            if not prefix.strip():
                continue
            candidate_name = f"{prefix}{suffix}"
            candidate_path = os.path.abspath(os.path.join(dir_path, candidate_name))
            if candidate_path == old_path or not os.path.exists(candidate_path):
                return candidate_path

        return None

    def _git_last_synced_timestamp(self, path: str) -> Optional[float]:
        repo_root = find_parent_with(path, ".git")
        if not repo_root:
            return None

        sync_marker_ts = read_sync_success_timestamp(repo_root)
        if sync_marker_ts is not None:
            return sync_marker_ts

        for revision in ("@{u}", "HEAD"):
            try:
                result = subprocess.run(
                    ["git", "log", "-1", "--format=%ct", revision],
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
                continue

            raw_timestamp = (result.stdout or "").strip()
            if not raw_timestamp:
                continue

            try:
                return float(raw_timestamp)
            except ValueError:
                continue
        return None

    def _git_age_label(self, path: str) -> str:
        last_commit_ts = self._git_last_synced_timestamp(path)
        if last_commit_ts is None:
            return "0m"
        age_minutes = int(max(0.0, time.time() - last_commit_ts) // 60.0)
        if age_minutes >= 60:
            return f"{age_minutes // 60}h"
        return f"{age_minutes}m"

    def _git_sync_time_label(self, path: str) -> str:
        last_commit_ts = self._git_last_synced_timestamp(path)
        if last_commit_ts is None:
            return "0"
        return str(int(last_commit_ts))

    def _build_tokens(
        self,
        path: str,
        parts: list[str],
        existing_git_sync_token: str | None,
        ascii_frame_text: str | None,
        banner_text: str | None,
        banner_offset: int,
        banner_max_chars: int,
        status_prefix: str,
    ) -> list[str]:
        if not parts and not banner_text and ascii_frame_text is None:
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
                git_sync_token = self._git_sync_time_label(path)
                if git_sync_token == "0" and existing_git_sync_token:
                    tokens.append(existing_git_sync_token)
                else:
                    tokens.append(git_sync_token)

        if ascii_frame_text is not None:
            tokens.append(ascii_frame_text)

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

    def _pick_ascii_frame(
        self,
        path: str,
        ascii_frames: list[str],
        ascii_speed_ms: int,
        *,
        advance_frame: bool,
    ) -> str | None:
        if not ascii_frames:
            return None

        with self._track_lock:
            current_index = self._ascii_frame_indices.get(path, 0) % len(ascii_frames)
            now_seconds = time.time()
            if advance_frame:
                last_switch_seconds = self._ascii_last_switch_seconds.get(path, 0.0)
                if last_switch_seconds <= 0.0:
                    self._ascii_last_switch_seconds[path] = now_seconds
                else:
                    speed_seconds = max(1, int(ascii_speed_ms)) / 1000.0
                    if now_seconds - last_switch_seconds >= speed_seconds:
                        current_index = (current_index + 1) % len(ascii_frames)
                        self._ascii_frame_indices[path] = current_index
                        self._ascii_last_switch_seconds[path] = now_seconds
            return ascii_frames[current_index]

    def _set_tracked_parts(
        self,
        path: str,
        parts: list[str],
        banner_text: str | None = None,
        banner_speed_ms: int = _DEFAULT_BANNER_SPEED_MS,
        banner_max_chars: int = _DEFAULT_BANNER_MAX_CHARS,
        status_prefix: str = "",
        ascii_animation_frames: list[str] | None = None,
        ascii_animation_speed_ms: int = _DEFAULT_ASCII_ANIMATION_SPEED_MS,
    ) -> None:
        abs_path = os.path.abspath(path)
        animation_frames = list(ascii_animation_frames or [])
        animation_speed_ms = max(1, int(ascii_animation_speed_ms))
        needs_background_updates = self._needs_background_updates(parts, banner_text)
        with self._track_lock:
            if animation_frames:
                previous_animation = self._tracked_ascii_animations.get(abs_path)
                next_animation = (list(animation_frames), animation_speed_ms)
                if previous_animation != next_animation:
                    self._ascii_frame_indices[abs_path] = 0
                    self._ascii_last_switch_seconds[abs_path] = 0.0
                self._tracked_ascii_animations[abs_path] = next_animation
            else:
                self._tracked_ascii_animations.pop(abs_path, None)
                self._ascii_frame_indices.pop(abs_path, None)
                self._ascii_last_switch_seconds.pop(abs_path, None)

            if needs_background_updates or animation_frames:
                self._tracked_prefixes[abs_path] = str(status_prefix or "")
            else:
                self._tracked_prefixes.pop(abs_path, None)

            if needs_background_updates:
                self._tracked_paths[abs_path] = list(parts)
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

            if animation_frames:
                self._ensure_ticker_started()
                self._tracked_paths.pop(abs_path, None)
                self._tracked_banners.pop(abs_path, None)
                self._banner_offsets.pop(abs_path, None)
                self._banner_last_slots.pop(abs_path, None)
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
            prefix_text = self._tracked_prefixes.pop(old_abs, None)
            if prefix_text is not None:
                self._tracked_prefixes[new_abs] = prefix_text
            offset = self._banner_offsets.pop(old_abs, None)
            if offset is not None:
                self._banner_offsets[new_abs] = offset
            last_slot = self._banner_last_slots.pop(old_abs, None)
            if last_slot is not None:
                self._banner_last_slots[new_abs] = last_slot
            animation_state = self._tracked_ascii_animations.pop(old_abs, None)
            if animation_state is not None:
                self._tracked_ascii_animations[new_abs] = (
                    list(animation_state[0]),
                    int(animation_state[1]),
                )
            frame_index = self._ascii_frame_indices.pop(old_abs, None)
            if frame_index is not None:
                self._ascii_frame_indices[new_abs] = int(frame_index)
            last_switch_seconds = self._ascii_last_switch_seconds.pop(old_abs, None)
            if last_switch_seconds is not None:
                self._ascii_last_switch_seconds[new_abs] = float(last_switch_seconds)

    def _tick_once(self) -> None:
        now_ts = time.time()
        with self._track_lock:
            tracked_items = [
                (
                    path,
                    list(self._tracked_paths.get(path, [])),
                    self._tracked_ascii_animations.get(path),
                )
                for path in (
                    set(self._tracked_paths.keys()) | set(self._tracked_ascii_animations.keys())
                )
            ]

        for path, parts, ascii_animation_state in tracked_items:
            if not os.path.exists(path):
                with self._track_lock:
                    self._tracked_paths.pop(path, None)
                    self._tracked_banners.pop(path, None)
                    self._tracked_prefixes.pop(path, None)
                    self._banner_offsets.pop(path, None)
                    self._banner_last_slots.pop(path, None)
                    self._tracked_ascii_animations.pop(path, None)
                    self._ascii_frame_indices.pop(path, None)
                    self._ascii_last_switch_seconds.pop(path, None)
                continue

            banner_text: str | None = None
            banner_offset = 0
            banner_max_chars = _DEFAULT_BANNER_MAX_CHARS
            status_prefix = ""
            ascii_frames: list[str] = []
            ascii_speed_ms = _DEFAULT_ASCII_ANIMATION_SPEED_MS
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
                if ascii_animation_state is None:
                    ascii_animation_state = self._tracked_ascii_animations.get(path)
                if ascii_animation_state is not None:
                    ascii_frames = list(ascii_animation_state[0])
                    ascii_speed_ms = int(ascii_animation_state[1])

            self._apply(
                path=path,
                parts=parts,
                banner_text=banner_text,
                banner_offset=banner_offset,
                banner_max_chars=banner_max_chars,
                status_prefix=status_prefix,
                ascii_animation_frames=ascii_frames,
                ascii_animation_speed_ms=ascii_speed_ms,
                advance_ascii_frame=bool(ascii_frames),
            )

    def _ticker_interval_seconds(self) -> float:
        now_ts = time.time()
        with self._track_lock:
            tracked_parts = list(self._tracked_paths.values())
            tracked_banners = list(self._tracked_banners.values())
            tracked_ascii_animations = list(self._tracked_ascii_animations.values())
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
        if tracked_ascii_animations:
            min_ascii_speed = min(
                max(1, speed_ms) / 1000.0 for _frames, speed_ms in tracked_ascii_animations
            )
            interval = min(interval, float(min_ascii_speed))
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

    def _status_from_file(
        self,
        path: str,
    ) -> tuple[list[str], str | None, int, int, str, list[str], int]:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except (OSError, UnicodeDecodeError):
            return (
                [],
                None,
                _DEFAULT_BANNER_SPEED_MS,
                _DEFAULT_BANNER_MAX_CHARS,
                "",
                [],
                _DEFAULT_ASCII_ANIMATION_SPEED_MS,
            )

        status_values: list[str] = []
        banner_text_value: Any = ""
        banner_speed_value: Any = _DEFAULT_BANNER_SPEED_MS
        banner_max_chars_value: Any = _DEFAULT_BANNER_MAX_CHARS
        status_prefix = ""
        ascii_animation_frames_value: list[str] = []
        ascii_animation_speed_value: Any = _DEFAULT_ASCII_ANIMATION_SPEED_MS
        status_flags = (
            "--status",
            "--status-banner",
            "--status-banner-speed-milliseconds",
            "--status-banner-max-characters",
            "--status-prefix",
            "--status-ascii-animation-frames",
            "--status-ascii-animation-speed-milliseconds",
        )
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if (
                "--status" not in stripped
                and "--status-banner" not in stripped
                and "--status-banner-speed-milliseconds" not in stripped
                and "--status-banner-max-characters" not in stripped
                and "--status-prefix" not in stripped
                and "--status-ascii-animation-frames" not in stripped
                and "--status-ascii-animation-speed-milliseconds" not in stripped
            ):
                continue
            try:
                tokens = shlex.split(stripped, comments=False, posix=True)
            except ValueError:
                continue

            i = 0
            while i < len(tokens):
                token_head = tokens[i]
                if token_head not in status_flags:
                    i += 1
                    continue

                if token_head == "--status-prefix":
                    if i + 1 < len(tokens) and tokens[i + 1] not in status_flags:
                        status_prefix = tokens[i + 1]
                        i += 2
                    else:
                        i += 1
                    continue

                if token_head == "--status-banner":
                    if i + 1 < len(tokens) and tokens[i + 1] not in status_flags:
                        banner_text_value = tokens[i + 1]
                        i += 2
                    else:
                        i += 1
                    continue

                if token_head == "--status-banner-speed-milliseconds":
                    if i + 1 < len(tokens) and tokens[i + 1] not in status_flags:
                        banner_speed_value = tokens[i + 1]
                        i += 2
                    else:
                        i += 1
                    continue

                if token_head == "--status-banner-max-characters":
                    if i + 1 < len(tokens) and tokens[i + 1] not in status_flags:
                        banner_max_chars_value = tokens[i + 1]
                        i += 2
                    else:
                        i += 1
                    continue

                if token_head == "--status-ascii-animation-speed-milliseconds":
                    if i + 1 < len(tokens) and tokens[i + 1] not in status_flags:
                        ascii_animation_speed_value = tokens[i + 1]
                        i += 2
                    else:
                        i += 1
                    continue

                if token_head == "--status-ascii-animation-frames":
                    j = i + 1
                    while j < len(tokens):
                        token = tokens[j]
                        if token in status_flags:
                            break
                        ascii_animation_frames_value.append(token)
                        j += 1
                    i = j
                    continue

                j = i + 1
                while j < len(tokens):
                    token = tokens[j]
                    if token in status_flags:
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
        ascii_animation_frames, ascii_animation_speed_ms = (
            self._normalize_ascii_animation_settings(
                ascii_animation_frames_value,
                ascii_animation_speed_value,
            )
        )
        if banner_text and not status_prefix:
            status_prefix = "."
        return (
            parts,
            banner_text,
            banner_speed_ms,
            banner_max_chars,
            status_prefix,
            ascii_animation_frames,
            ascii_animation_speed_ms,
        )

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
                        ascii_animation_frames,
                        ascii_animation_speed_ms,
                    ) = self._status_from_file(file_path)
                    if (
                        not parts
                        and not banner_text
                        and not status_prefix
                        and not ascii_animation_frames
                    ):
                        self._set_tracked_parts(path=file_path, parts=[], banner_text=None)
                        continue
                    self._set_tracked_parts(
                        path=file_path,
                        parts=parts,
                        banner_text=banner_text,
                        banner_speed_ms=banner_speed_ms,
                        banner_max_chars=banner_max_chars,
                        status_prefix=status_prefix,
                        ascii_animation_frames=ascii_animation_frames,
                        ascii_animation_speed_ms=ascii_animation_speed_ms,
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
                        ascii_animation_frames=ascii_animation_frames,
                        ascii_animation_speed_ms=ascii_animation_speed_ms,
                        advance_ascii_frame=True,
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
        ascii_animation_frames: list[str] | None = None,
        ascii_animation_speed_ms: int = _DEFAULT_ASCII_ANIMATION_SPEED_MS,
        advance_ascii_frame: bool = False,
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
            if ascii_animation_frames is None:
                with self._track_lock:
                    tracked_ascii_animation = self._tracked_ascii_animations.get(old_path)
                if tracked_ascii_animation:
                    ascii_animation_frames = list(tracked_ascii_animation[0])
                    ascii_animation_speed_ms = int(tracked_ascii_animation[1])

            ascii_frame_text = self._pick_ascii_frame(
                path=old_path,
                ascii_frames=list(ascii_animation_frames or []),
                ascii_speed_ms=ascii_animation_speed_ms,
                advance_frame=advance_ascii_frame,
            )

            tokens = self._build_tokens(
                path=old_path,
                parts=parts,
                existing_git_sync_token=existing_tokens.get("g_sync"),
                ascii_frame_text=ascii_frame_text,
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
            dir_path = os.path.dirname(old_path)
            safe_new_name = self._make_filename_candidate(dir_path, new_name)
            new_path = self._pick_available_new_path(
                old_path=old_path,
                dir_path=dir_path,
                base_name=safe_new_name,
            )
            if new_path is None:
                return None

            if new_path == old_path:
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
            ctx.config.get("status_banner_speed_milliseconds", _DEFAULT_BANNER_SPEED_MS),
            ctx.config.get("status_banner_max_characters", _DEFAULT_BANNER_MAX_CHARS),
        )
        status_prefix = str(ctx.config.get("status_prefix", ""))
        ascii_animation_frames, ascii_animation_speed_ms = (
            self._normalize_ascii_animation_settings(
                ctx.config.get("status_ascii_animation_frames", []),
                ctx.config.get(
                    "status_ascii_animation_speed_milliseconds",
                    _DEFAULT_ASCII_ANIMATION_SPEED_MS,
                ),
            )
        )
        if banner_text and not status_prefix:
            status_prefix = "."
        self._set_tracked_parts(
            path=ctx.path,
            parts=parts,
            banner_text=banner_text,
            banner_speed_ms=banner_speed_ms,
            banner_max_chars=banner_max_chars,
            status_prefix=status_prefix,
            ascii_animation_frames=ascii_animation_frames,
            ascii_animation_speed_ms=ascii_animation_speed_ms,
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
            ascii_animation_frames=ascii_animation_frames,
            ascii_animation_speed_ms=ascii_animation_speed_ms,
            advance_ascii_frame=True,
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
        if ctx.config.get("status_opened_events_disable", False):
            return None
        return self._handle_event(ctx)
