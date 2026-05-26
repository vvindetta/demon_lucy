from __future__ import annotations

import logging
import os
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
from lucy_notes_manager.modules.git.sync_marker import read_sync_success_timestamp
from lucy_notes_manager.modules.status.files import StatusFileMixin
from lucy_notes_manager.modules.status.parsing import StatusParsingMixin
from lucy_notes_manager.modules.status.rendering import StatusRenderingMixin

logger = logging.getLogger(__name__)


class Status(
    StatusFileMixin,
    StatusRenderingMixin,
    StatusParsingMixin,
    AbstractModule,
):
    name: str = "status"
    priority: int = 21
    _SECONDS_TICK_INTERVAL_SECONDS = 1.0

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
            'Animated filename banner text. Example: --status-banner "Work sentence"',
            False,
        ),
        (
            "--status-banner-speed-milliseconds",
            int,
            500,
            "Animated banner speed in milliseconds per step. Default: 500",
            False,
        ),
        (
            "--status-banner-max-characters",
            int,
            0,
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
            "--status-animation",
            str,
            [],
            'Animation frames for filename status. Example: --status-animation "loading" "loading." "loading.."',
            False,
        ),
        (
            "--status-animation-speed-milliseconds",
            int,
            500,
            "Animation frame switch speed in milliseconds. Default: 500",
            False,
        ),
        (
            "--status-tick-interval-seconds",
            float,
            60.0,
            "Base ticker interval for status updates in seconds. Default: 60.0",
            False,
        ),
        (
            "--status-git-fast-tick-interval-seconds",
            float,
            0.5,
            "Fast ticker interval for --status git update in seconds. Default: 0.5",
            False,
        ),
        (
            "--status-git-fast-tick-window-seconds",
            float,
            120.0,
            "Duration of fast ticker mode after git update activity in seconds. Default: 120.0",
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
        defaults = self._template_defaults()
        self._default_banner_speed_ms = int(
            defaults["status_banner_speed_milliseconds"]
        )
        self._default_banner_max_chars = int(defaults["status_banner_max_characters"])
        self._default_animation_speed_ms = int(
            defaults["status_animation_speed_milliseconds"]
        )
        self._tick_interval_seconds = float(defaults["status_tick_interval_seconds"])
        self._git_fast_tick_interval_seconds = float(
            defaults["status_git_fast_tick_interval_seconds"]
        )
        self._git_fast_tick_window_seconds = float(
            defaults["status_git_fast_tick_window_seconds"]
        )
        self._tracked_paths: dict[str, list[str]] = {}
        self._tracked_banners: dict[str, tuple[str, int, int]] = {}
        self._tracked_prefixes: dict[str, str] = {}
        self._banner_offsets: dict[str, int] = {}
        self._banner_last_slots: dict[str, int] = {}
        self._tracked_animations: dict[str, tuple[list[str], int]] = {}
        self._animation_frame_indices: dict[str, int] = {}
        self._animation_last_switch_seconds: dict[str, float] = {}
        self._animation_cycle_finished: dict[str, bool] = {}
        self._git_sync_prefix_frame_indices: dict[str, int] = {}
        self._git_sync_prefix_last_switch_seconds: dict[str, float] = {}
        self._track_lock = threading.Lock()
        self._rename_lock = threading.Lock()
        self._bootstrap_lock = threading.Lock()
        self._bootstrap_done = False
        self._ticker_stop = threading.Event()
        self._last_tick_key: tuple[float, int] | None = None
        self._git_fast_tick_until = 0.0
        self._ticker_thread: threading.Thread | None = None

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

    def _animated_git_sync_prefix(
        self,
        path: str,
        *,
        status_prefix: str,
        fast_mode: bool,
    ) -> str:
        base_prefix = str(status_prefix or "")
        if not base_prefix:
            base_prefix = "Sync "

        if not fast_mode:
            return base_prefix

        letter_positions = [idx for idx, ch in enumerate(base_prefix) if ch.isalpha()]
        if not letter_positions:
            return base_prefix

        with self._track_lock:
            current_index = self._git_sync_prefix_frame_indices.get(path, 0)
            if current_index < 0:
                current_index = 0
            if current_index >= len(letter_positions):
                current_index = 0

            now_seconds = time.time()
            last_switch_seconds = self._git_sync_prefix_last_switch_seconds.get(
                path, 0.0
            )
            if last_switch_seconds <= 0.0:
                self._git_sync_prefix_last_switch_seconds[path] = now_seconds
            else:
                speed_seconds = max(0.1, float(self._git_fast_tick_interval_seconds))
                if now_seconds - last_switch_seconds >= speed_seconds:
                    current_index = (current_index + 1) % len(letter_positions)
                    self._git_sync_prefix_frame_indices[path] = current_index
                    self._git_sync_prefix_last_switch_seconds[path] = now_seconds

        highlighted_pos = letter_positions[current_index]
        out_chars: list[str] = []
        for idx, ch in enumerate(base_prefix):
            if ch.isalpha():
                normalized = ch.lower()
                if idx == highlighted_pos:
                    out_chars.append(normalized.upper())
                else:
                    out_chars.append(normalized)
            else:
                out_chars.append(ch)
        return "".join(out_chars)

    @staticmethod
    def _needs_background_updates(parts: list[str], banner_text: str | None) -> bool:
        if banner_text:
            return True
        return any(
            part in ("date", "time", "time_with_seconds", "git_update")
            for part in parts
        )

    def _pick_animation_frame(
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
            current_index = self._animation_frame_indices.get(path, 0)
            if current_index < 0:
                current_index = 0
            if current_index >= len(ascii_frames):
                current_index = len(ascii_frames) - 1
            now_seconds = time.time()
            if advance_frame:
                if self._animation_cycle_finished.get(path, False):
                    return ascii_frames[current_index]
                last_switch_seconds = self._animation_last_switch_seconds.get(path, 0.0)
                if last_switch_seconds <= 0.0:
                    self._animation_last_switch_seconds[path] = now_seconds
                else:
                    speed_seconds = max(1, int(ascii_speed_ms)) / 1000.0
                    if now_seconds - last_switch_seconds >= speed_seconds:
                        if current_index < len(ascii_frames) - 1:
                            current_index += 1
                            self._animation_frame_indices[path] = current_index
                        else:
                            current_index = 0
                            self._animation_frame_indices[path] = current_index
                            self._animation_cycle_finished[path] = True
                        self._animation_last_switch_seconds[path] = now_seconds
            return ascii_frames[current_index]

    def _set_tracked_parts(
        self,
        path: str,
        parts: list[str],
        banner_text: str | None = None,
        banner_speed_ms: int | None = None,
        banner_max_chars: int | None = None,
        status_prefix: str = "",
        ascii_animation_frames: list[str] | None = None,
        ascii_animation_speed_ms: int | None = None,
    ) -> None:
        abs_path = os.path.abspath(path)
        if banner_speed_ms is None:
            banner_speed_ms = self._default_banner_speed_ms
        if banner_max_chars is None:
            banner_max_chars = self._default_banner_max_chars
        if ascii_animation_speed_ms is None:
            ascii_animation_speed_ms = self._default_animation_speed_ms
        animation_frames = list(ascii_animation_frames or [])
        animation_speed_ms = max(1, int(ascii_animation_speed_ms))
        needs_background_updates = self._needs_background_updates(parts, banner_text)
        with self._track_lock:
            if animation_frames:
                previous_animation = self._tracked_animations.get(abs_path)
                next_animation = (list(animation_frames), animation_speed_ms)
                if previous_animation != next_animation:
                    self._animation_frame_indices[abs_path] = 0
                    self._animation_last_switch_seconds[abs_path] = 0.0
                    self._animation_cycle_finished[abs_path] = False
                self._tracked_animations[abs_path] = next_animation
            else:
                self._tracked_animations.pop(abs_path, None)
                self._animation_frame_indices.pop(abs_path, None)
                self._animation_last_switch_seconds.pop(abs_path, None)
                self._animation_cycle_finished.pop(abs_path, None)
                self._git_sync_prefix_frame_indices.pop(abs_path, None)
                self._git_sync_prefix_last_switch_seconds.pop(abs_path, None)

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
                        time.time() + self._git_fast_tick_window_seconds,
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
            try:
                if self._ticker_thread.is_alive():
                    return
            except Exception:
                pass

        self._last_tick_key = None
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
            animation_state = self._tracked_animations.pop(old_abs, None)
            if animation_state is not None:
                self._tracked_animations[new_abs] = (
                    list(animation_state[0]),
                    int(animation_state[1]),
                )
            frame_index = self._animation_frame_indices.pop(old_abs, None)
            if frame_index is not None:
                self._animation_frame_indices[new_abs] = int(frame_index)
            last_switch_seconds = self._animation_last_switch_seconds.pop(old_abs, None)
            if last_switch_seconds is not None:
                self._animation_last_switch_seconds[new_abs] = float(
                    last_switch_seconds
                )
            cycle_finished = self._animation_cycle_finished.pop(old_abs, None)
            if cycle_finished is not None:
                self._animation_cycle_finished[new_abs] = bool(cycle_finished)
            git_prefix_frame_index = self._git_sync_prefix_frame_indices.pop(
                old_abs, None
            )
            if git_prefix_frame_index is not None:
                self._git_sync_prefix_frame_indices[new_abs] = int(
                    git_prefix_frame_index
                )
            git_prefix_last_switch = self._git_sync_prefix_last_switch_seconds.pop(
                old_abs, None
            )
            if git_prefix_last_switch is not None:
                self._git_sync_prefix_last_switch_seconds[new_abs] = float(
                    git_prefix_last_switch
                )

    def _restart_tracked_animation_cycles(self, trigger_path: str) -> None:
        trigger_abs = os.path.abspath(trigger_path)
        now_seconds = time.time()
        with self._track_lock:
            for path in list(self._tracked_animations.keys()):
                if path == trigger_abs:
                    continue
                self._animation_frame_indices[path] = 0
                self._animation_last_switch_seconds[path] = now_seconds
                self._animation_cycle_finished[path] = False

    def _tick_once(self) -> None:
        now_ts = time.time()
        with self._track_lock:
            tracked_items = [
                (
                    path,
                    list(self._tracked_paths.get(path, [])),
                    self._tracked_animations.get(path),
                )
                for path in (
                    set(self._tracked_paths.keys())
                    | set(self._tracked_animations.keys())
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
                    self._tracked_animations.pop(path, None)
                    self._animation_frame_indices.pop(path, None)
                    self._animation_last_switch_seconds.pop(path, None)
                    self._animation_cycle_finished.pop(path, None)
                    self._git_sync_prefix_frame_indices.pop(path, None)
                    self._git_sync_prefix_last_switch_seconds.pop(path, None)
                continue

            banner_text: str | None = None
            banner_offset = 0
            banner_max_chars = self._default_banner_max_chars
            status_prefix = ""
            ascii_frames: list[str] = []
            ascii_speed_ms = self._default_animation_speed_ms
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
                    ascii_animation_state = self._tracked_animations.get(path)
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
            tracked_ascii_animations = list(self._tracked_animations.values())
            has_seconds = any("time_with_seconds" in parts for parts in tracked_parts)
            has_git_update = any("git_update" in parts for parts in tracked_parts)
            fast_until = self._git_fast_tick_until

        interval = self._tick_interval_seconds
        if has_seconds:
            interval = min(interval, self._SECONDS_TICK_INTERVAL_SECONDS)
        if has_git_update and now_ts < fast_until:
            interval = min(interval, self._git_fast_tick_interval_seconds)
        if tracked_banners:
            min_banner_speed = min(
                max(1, speed_ms) / 1000.0
                for _text, speed_ms, _max_chars in tracked_banners
            )
            interval = min(interval, float(min_banner_speed))
        if tracked_ascii_animations:
            min_ascii_speed = min(
                max(1, speed_ms) / 1000.0
                for _frames, speed_ms in tracked_ascii_animations
            )
            interval = min(interval, float(min_ascii_speed))
        return interval

    def _ticker_loop(self) -> None:
        while not self._ticker_stop.is_set():
            try:
                interval_seconds = max(0.1, float(self._ticker_interval_seconds()))
                current_slot = int(time.time() // interval_seconds)
                tick_key = (interval_seconds, current_slot)
                if tick_key != self._last_tick_key:
                    self._last_tick_key = tick_key
                    self._tick_once()
                wait_seconds = (
                    0.25
                    if interval_seconds <= self._git_fast_tick_interval_seconds
                    else 1.0
                )
                self._ticker_stop.wait(wait_seconds)
            except Exception as exc:
                logger.warning("status ticker iteration failed: %s", exc)
                self._last_tick_key = None
                self._ticker_stop.wait(1.0)

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
                        self._set_tracked_parts(
                            path=file_path, parts=[], banner_text=None
                        )
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
        banner_max_chars: int | None = None,
        status_prefix: str | None = None,
        ascii_animation_frames: list[str] | None = None,
        ascii_animation_speed_ms: int | None = None,
        advance_ascii_frame: bool = False,
    ) -> Optional[IgnoreMap]:
        with self._rename_lock:
            if banner_max_chars is None:
                banner_max_chars = self._default_banner_max_chars
            if ascii_animation_speed_ms is None:
                ascii_animation_speed_ms = self._default_animation_speed_ms
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
                    tracked_ascii_animation = self._tracked_animations.get(old_path)
                if tracked_ascii_animation:
                    ascii_animation_frames = list(tracked_ascii_animation[0])
                    ascii_animation_speed_ms = int(tracked_ascii_animation[1])

            fast_mode = False
            if "git_update" in parts:
                with self._track_lock:
                    fast_mode = time.time() < self._git_fast_tick_until
                status_prefix = self._animated_git_sync_prefix(
                    old_path,
                    status_prefix=str(status_prefix or ""),
                    fast_mode=fast_mode,
                )

            ascii_frame_text = self._pick_animation_frame(
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
        self._tick_interval_seconds = max(
            0.1, float(ctx.config["status_tick_interval_seconds"])
        )
        self._git_fast_tick_interval_seconds = max(
            0.1, float(ctx.config["status_git_fast_tick_interval_seconds"])
        )
        self._git_fast_tick_window_seconds = max(
            0.1, float(ctx.config["status_git_fast_tick_window_seconds"])
        )
        bootstrap_changed = self._bootstrap_once(ctx.path)
        self._restart_tracked_animation_cycles(ctx.path)
        parts = self._parse_status_parts(list(ctx.config["status"]))
        banner_text, banner_speed_ms, banner_max_chars = (
            self._normalize_banner_settings(
                ctx.config["status_banner"],
                ctx.config["status_banner_speed_milliseconds"],
                ctx.config["status_banner_max_characters"],
            )
        )
        status_prefix = str(ctx.config["status_prefix"])
        ascii_animation_frames, ascii_animation_speed_ms = (
            self._normalize_animation_settings(
                ctx.config["status_animation"],
                ctx.config["status_animation_speed_milliseconds"],
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
        if ctx.config["status_opened_events_disable"]:
            return None
        return self._handle_event(ctx)
