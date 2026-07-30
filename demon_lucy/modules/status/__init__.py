from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Optional

from demon_lucy.lib.args.models import KnownArg, ParsedArgs, Template
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.lib.git_state import (
    read_sync_success_timestamp,
    repo_process_lock_is_active,
)
from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.path import find_parent_with
from demon_lucy.lib.operating_system import OperatingSystem
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    ModuleResult,
    System,
)
from demon_lucy.modules.status.files import StatusFileMixin
from demon_lucy.modules.status.parsing import StatusParsingMixin
from demon_lucy.modules.status.rendering import StatusRenderingMixin

logger = logging.getLogger(__name__)


@dataclass
class _StatusTarget:
    parts: list[str] = field(default_factory=list)
    banner: tuple[str, int, int] | None = None
    status_prefix: str = ""
    banner_offset: int = 0
    banner_last_slot: int | None = None
    animation: tuple[list[str], int] | None = None
    animation_frame_index: int = 0
    animation_last_switch_seconds: float = 0.0
    animation_cycle_finished: bool = False
    git_sync_prefix_frame_index: int = 0
    git_sync_prefix_last_switch_seconds: float = 0.0
    git_sync_prefix_pause_until_seconds: float = 0.0


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
        KnownArg(
            name="status",
            value_type=str,
            default=[],
            description="Filename status tokens. Examples: --status date OR --status time date OR --status time-with-seconds OR --status git OR --status git update",
        ),
        KnownArg(
            name="status-banner",
            value_type=str,
            default="",
            description='Animated filename banner text. Example: --status-banner "Work sentence"',
        ),
        KnownArg(
            name="status-banner-speed-milliseconds",
            value_type=int,
            default=500,
            description="Animated banner speed in milliseconds per step. Default: 500",
        ),
        KnownArg(
            name="status-banner-max-characters",
            value_type=int,
            default=0,
            description="Max visible banner width. 0 = unlimited. Default: 0",
        ),
        KnownArg(
            name="status-prefix",
            value_type=str,
            default="",
            description="Prefix text inserted at the very beginning of the filename status. Example: --status-prefix 'Inbox: '",
        ),
        KnownArg(
            name="status-animation",
            value_type=str,
            default=[],
            description='Animation frames for filename status. Example: --status-animation "loading" "loading." "loading.."',
        ),
        KnownArg(
            name="status-animation-speed-milliseconds",
            value_type=int,
            default=500,
            description="Animation frame switch speed in milliseconds. Default: 500",
        ),
        KnownArg(
            name="status-tick-interval-seconds",
            value_type=float,
            default=60.0,
            description="Base ticker interval for status updates in seconds. Default: 60.0",
        ),
        KnownArg(
            name="status-git-fast-tick-interval-seconds",
            value_type=float,
            default=0.5,
            description="Fast ticker interval for --status git update in seconds. Default: 0.5",
        ),
        KnownArg(
            name="status-git-fast-tick-window-seconds",
            value_type=float,
            default=120.0,
            description="Duration of fast ticker mode after git update activity in seconds. Default: 120.0",
        ),
        KnownArg(
            name="status-git-sync-prefix-cycle-pause-seconds",
            value_type=float,
            default=1.0,
            description="Pause between git-sync prefix animation cycles in seconds. Default: 1.0",
        ),
        KnownArg(
            name="status-opened-events",
            value_type=bool,
            default=False,
            description="Enable status updates for opened events.",
        ),
    ]

    def __init__(self) -> None:
        super().__init__()
        defaults = parse_args(args=[], template=self.template)
        self._default_banner_speed_ms: int = defaults.require(
            "status-banner-speed-milliseconds"
        ).value
        self._default_banner_max_chars: int = defaults.require(
            "status-banner-max-characters"
        ).value
        self._default_animation_speed_ms: int = defaults.require(
            "status-animation-speed-milliseconds"
        ).value
        self._tick_interval_seconds: float = defaults.require(
            "status-tick-interval-seconds"
        ).value
        self._git_fast_tick_interval_seconds: float = defaults.require(
            "status-git-fast-tick-interval-seconds"
        ).value
        self._git_fast_tick_window_seconds: float = defaults.require(
            "status-git-fast-tick-window-seconds"
        ).value
        self._git_sync_prefix_cycle_pause_seconds: float = defaults.require(
            "status-git-sync-prefix-cycle-pause-seconds"
        ).value
        self._git_repo_lock_wait_timeout_seconds: float | None = None
        self._git_repo_lock_stale_seconds: float | None = None
        self._operating_system: OperatingSystem | None = None
        self._targets: dict[str, _StatusTarget] = {}
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

    def _git_sync_is_active(self, path: str) -> bool:
        repo_root = find_parent_with(path, ".git")
        if not repo_root:
            return False
        if (
            self._git_repo_lock_wait_timeout_seconds is None
            or self._git_repo_lock_stale_seconds is None
            or self._operating_system is None
        ):
            return False
        return repo_process_lock_is_active(
            repo_root,
            wait_timeout_seconds=self._git_repo_lock_wait_timeout_seconds,
            stale_seconds=self._git_repo_lock_stale_seconds,
            operating_system=self._operating_system,
        )

    def _update_git_repo_lock_settings(
        self,
        args: ParsedArgs,
        operating_system: OperatingSystem,
    ) -> None:
        self._git_repo_lock_wait_timeout_seconds = max(
            0.0,
            args.require("sys-git-repo-lock-wait-timeout-seconds").value,
        )
        self._git_repo_lock_stale_seconds = max(
            0.0,
            args.require("sys-git-repo-lock-stale-seconds").value,
        )
        self._operating_system = operating_system

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

        if status_prefix and tokens:
            tokens[0] = f"{status_prefix}{tokens[0]}"

        return tokens

    def _animated_git_sync_prefix(
        self,
        path: str,
        *,
        status_prefix: str,
        fast_mode: bool,
    ) -> str:
        def _letters_to_lower(text: str) -> str:
            out: list[str] = []
            for ch in text:
                if ch.isalpha():
                    out.append(ch.lower())
                else:
                    out.append(ch)
            return "".join(out)

        base_prefix = status_prefix or "Sync "

        if not fast_mode:
            with self._track_lock:
                target = self._targets.get(path)
                if target is not None:
                    target.git_sync_prefix_frame_index = 0
                    target.git_sync_prefix_last_switch_seconds = 0.0
                    target.git_sync_prefix_pause_until_seconds = 0.0
            return base_prefix

        letter_positions = [idx for idx, ch in enumerate(base_prefix) if ch.isalpha()]
        if not letter_positions:
            return base_prefix

        render_pause_lowercase = False
        with self._track_lock:
            target = self._targets.setdefault(path, _StatusTarget())
            current_index = target.git_sync_prefix_frame_index
            if current_index < 0:
                current_index = 0
            if current_index >= len(letter_positions):
                current_index = 0

            now_seconds = time.time()
            pause_until_seconds = target.git_sync_prefix_pause_until_seconds
            if pause_until_seconds > 0.0:
                if now_seconds < pause_until_seconds:
                    return _letters_to_lower(base_prefix)
                target.git_sync_prefix_pause_until_seconds = 0.0
                current_index = 0
                target.git_sync_prefix_frame_index = current_index
                target.git_sync_prefix_last_switch_seconds = now_seconds

            last_switch_seconds = target.git_sync_prefix_last_switch_seconds
            if last_switch_seconds <= 0.0:
                target.git_sync_prefix_last_switch_seconds = now_seconds
            else:
                speed_seconds = max(0.1, float(self._git_fast_tick_interval_seconds))
                if now_seconds - last_switch_seconds >= speed_seconds:
                    next_index = current_index + 1
                    if next_index >= len(letter_positions):
                        target.git_sync_prefix_pause_until_seconds = (
                            now_seconds + self._git_sync_prefix_cycle_pause_seconds
                        )
                        target.git_sync_prefix_last_switch_seconds = now_seconds
                        render_pause_lowercase = True
                    else:
                        current_index = next_index
                        target.git_sync_prefix_frame_index = current_index
                        target.git_sync_prefix_last_switch_seconds = now_seconds

        if render_pause_lowercase:
            return _letters_to_lower(base_prefix)

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
            target = self._targets.setdefault(path, _StatusTarget())
            current_index = target.animation_frame_index
            if current_index < 0:
                current_index = 0
            if current_index >= len(ascii_frames):
                current_index = len(ascii_frames) - 1
            now_seconds = time.time()
            if advance_frame:
                if target.animation_cycle_finished:
                    return ascii_frames[current_index]
                last_switch_seconds = target.animation_last_switch_seconds
                if last_switch_seconds <= 0.0:
                    target.animation_last_switch_seconds = now_seconds
                else:
                    speed_seconds = max(1, ascii_speed_ms) / 1000.0
                    if now_seconds - last_switch_seconds >= speed_seconds:
                        if current_index < len(ascii_frames) - 1:
                            current_index += 1
                            target.animation_frame_index = current_index
                        else:
                            current_index = 0
                            target.animation_frame_index = current_index
                            target.animation_cycle_finished = True
                        target.animation_last_switch_seconds = now_seconds
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
        animation_frames = (
            list(ascii_animation_frames) if ascii_animation_frames is not None else []
        )
        animation_speed_ms = max(1, ascii_animation_speed_ms)
        needs_background_updates = self._needs_background_updates(parts, banner_text)
        with self._track_lock:
            if not needs_background_updates and not animation_frames:
                self._targets.pop(abs_path, None)
                return

            target = self._targets.setdefault(abs_path, _StatusTarget())
            target.status_prefix = status_prefix

            if animation_frames:
                previous_animation = target.animation
                next_animation = (animation_frames, animation_speed_ms)
                if previous_animation != next_animation:
                    target.animation_frame_index = 0
                    target.animation_last_switch_seconds = 0.0
                    target.animation_cycle_finished = False
                target.animation = next_animation
            else:
                target.animation = None
                target.animation_frame_index = 0
                target.animation_last_switch_seconds = 0.0
                target.animation_cycle_finished = False
                target.git_sync_prefix_frame_index = 0
                target.git_sync_prefix_last_switch_seconds = 0.0
                target.git_sync_prefix_pause_until_seconds = 0.0

            if needs_background_updates:
                target.parts = list(parts)
                if banner_text:
                    safe_speed_ms = max(1, banner_speed_ms)
                    safe_max_chars = max(0, banner_max_chars)
                    previous_banner = target.banner
                    next_banner = (banner_text, safe_speed_ms, safe_max_chars)
                    if previous_banner != next_banner:
                        target.banner_offset = 0
                        target.banner_last_slot = None
                    target.banner = next_banner
                else:
                    target.banner = None
                    target.banner_offset = 0
                    target.banner_last_slot = None
                if "git_update" in parts:
                    self._git_fast_tick_until = max(
                        self._git_fast_tick_until,
                        time.time() + self._git_fast_tick_window_seconds,
                    )
                self._ensure_ticker_started()
                return

            if animation_frames:
                self._ensure_ticker_started()
                target.parts = []
                target.banner = None
                target.banner_offset = 0
                target.banner_last_slot = None
                return

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
            target = self._targets.pop(old_abs, None)
            if target is not None:
                self._targets[new_abs] = target

    def _restart_tracked_animation_cycles(self, trigger_path: str) -> None:
        trigger_abs = os.path.abspath(trigger_path)
        now_seconds = time.time()
        with self._track_lock:
            for path, target in self._targets.items():
                if target.animation is None:
                    continue
                if path == trigger_abs:
                    continue
                target.animation_frame_index = 0
                target.animation_last_switch_seconds = now_seconds
                target.animation_cycle_finished = False

    def _tick_once(self) -> None:
        now_ts = time.time()
        with self._track_lock:
            tracked_items = [
                (path, list(target.parts), target.animation)
                for path, target in self._targets.items()
            ]

        for path, parts, ascii_animation_state in tracked_items:
            if not os.path.exists(path):
                with self._track_lock:
                    self._targets.pop(path, None)
                continue

            banner_text: str | None = None
            banner_offset = 0
            banner_max_chars = self._default_banner_max_chars
            status_prefix = ""
            ascii_frames: list[str] = []
            ascii_speed_ms = self._default_animation_speed_ms
            with self._track_lock:
                target = self._targets.get(path)
                if target is not None:
                    banner_state = target.banner
                    if banner_state:
                        banner_text, banner_speed_ms, banner_max_chars = banner_state
                        speed_seconds = max(1, banner_speed_ms) / 1000.0
                        current_slot = int(now_ts // speed_seconds)
                        last_slot = target.banner_last_slot
                        if last_slot is None:
                            target.banner_last_slot = current_slot
                        elif current_slot != last_slot:
                            step_count = max(1, current_slot - last_slot)
                            target.banner_last_slot = current_slot
                            target.banner_offset += step_count
                        banner_offset = target.banner_offset
                    status_prefix = target.status_prefix
                    if ascii_animation_state is None:
                        ascii_animation_state = target.animation
                if ascii_animation_state is not None:
                    ascii_frames = list(ascii_animation_state[0])
                    ascii_speed_ms = int(ascii_animation_state[1])

            operating_system = self._operating_system
            if operating_system is None:
                continue

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
                operating_system=operating_system,
            )

    def _ticker_interval_seconds(self) -> float:
        now_ts = time.time()
        with self._track_lock:
            tracked_items = [
                (path, list(target.parts)) for path, target in self._targets.items()
            ]
            targets = list(self._targets.values())
            tracked_parts = [parts for _path, parts in tracked_items]
            tracked_banners = [
                target.banner for target in targets if target.banner is not None
            ]
            tracked_ascii_animations = [
                target.animation for target in targets if target.animation is not None
            ]
            has_seconds = any("time_with_seconds" in parts for parts in tracked_parts)
            has_git_update = any("git_update" in parts for parts in tracked_parts)
            fast_until = self._git_fast_tick_until

        interval = self._tick_interval_seconds
        if has_seconds:
            interval = min(interval, self._SECONDS_TICK_INTERVAL_SECONDS)
        git_sync_active = any(
            "git_update" in parts and self._git_sync_is_active(path)
            for path, parts in tracked_items
        )
        if has_git_update and (now_ts < fast_until or git_sync_active):
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
                logger.warning(log_record("status.ticker_error", error=exc))
                self._last_tick_key = None
                self._ticker_stop.wait(1.0)

    def _bootstrap_from_root_status_directories(
        self,
        watch_paths: list[str],
        operating_system: OperatingSystem,
    ) -> dict[str, int] | None:
        root_status_directories = self._discover_root_status_directories(watch_paths)
        if not root_status_directories:
            return None

        merged: dict[str, int] | None = None
        for status_dir in root_status_directories:
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
                        target = self._targets.get(file_path)
                        banner_offset = (
                            target.banner_offset if target is not None else 0
                        )
                    apply_result = self._apply(
                        path=file_path,
                        parts=parts,
                        banner_text=banner_text,
                        banner_offset=banner_offset,
                        banner_max_chars=banner_max_chars,
                        status_prefix=status_prefix,
                        ascii_animation_frames=ascii_animation_frames,
                        ascii_animation_speed_ms=ascii_animation_speed_ms,
                        advance_ascii_frame=True,
                        operating_system=operating_system,
                    )
                    changed = apply_result[1] if apply_result is not None else None
                    merged = self._merge_changes(merged, changed)
        return merged

    def _bootstrap_once(
        self,
        watch_paths: list[str],
        operating_system: OperatingSystem,
    ) -> dict[str, int] | None:
        if self._bootstrap_done:
            return None
        with self._bootstrap_lock:
            if self._bootstrap_done:
                return None
            # Status state is in-memory; after daemon restart, existing root
            # .status files need one lazy scan to revive ticker/animations.
            changed = self._bootstrap_from_root_status_directories(
                watch_paths,
                operating_system,
            )
            self._bootstrap_done = True
            return changed

    def _apply(
        self,
        path: str,
        parts: list[str],
        *,
        operating_system: OperatingSystem,
        banner_text: str | None = None,
        banner_offset: int = 0,
        banner_max_chars: int | None = None,
        status_prefix: str | None = None,
        ascii_animation_frames: list[str] | None = None,
        ascii_animation_speed_ms: int | None = None,
        advance_ascii_frame: bool = False,
    ) -> tuple[str, dict[str, int]] | None:
        with self._rename_lock:
            if banner_max_chars is None:
                banner_max_chars = self._default_banner_max_chars
            if ascii_animation_speed_ms is None:
                ascii_animation_speed_ms = self._default_animation_speed_ms
            old_path = os.path.abspath(path)
            if os.path.isdir(old_path) or not os.path.exists(old_path):
                return None

            if status_prefix is None:
                with self._track_lock:
                    target = self._targets.get(old_path)
                    status_prefix = target.status_prefix if target is not None else ""

            base_name = os.path.basename(old_path)
            stem, _ext = os.path.splitext(base_name)
            existing_tokens, _clean_stem = self._split_status_prefix(
                stem=stem,
                status_prefix=status_prefix,
            )
            if banner_text is None:
                with self._track_lock:
                    target = self._targets.get(old_path)
                    if target is not None:
                        banner_state = target.banner
                        if banner_state:
                            banner_text = banner_state[0]
                            banner_max_chars = banner_state[2]
                            banner_offset = target.banner_offset
            if ascii_animation_frames is None:
                with self._track_lock:
                    target = self._targets.get(old_path)
                    tracked_ascii_animation = (
                        target.animation if target is not None else None
                    )
                if tracked_ascii_animation:
                    ascii_animation_frames = list(tracked_ascii_animation[0])
                    ascii_animation_speed_ms = tracked_ascii_animation[1]

            fast_mode = False
            if "git_update" in parts:
                current_git_age_label = self._git_age_label(old_path)
                with self._track_lock:
                    fast_mode = time.time() < self._git_fast_tick_until
                if self._git_sync_is_active(old_path):
                    fast_mode = True
                if current_git_age_label == "0m":
                    fast_mode = False
                status_prefix = self._animated_git_sync_prefix(
                    old_path,
                    status_prefix=status_prefix,
                    fast_mode=fast_mode,
                )

            ascii_frame_text = self._pick_animation_frame(
                path=old_path,
                ascii_frames=ascii_animation_frames or [],
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
                status_prefix=status_prefix,
            )
            if not tokens:
                return None

            # Keep plain spaces in filenames for readability.
            new_name = " ".join(tokens)
            if not new_name.strip():
                new_name = " - "
            dir_path = os.path.dirname(old_path)
            safe_new_name = self._make_filename_candidate(
                dir_path,
                new_name,
                operating_system=operating_system,
            )
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
            return new_path, {old_path: 1, new_path: 1}

    def _handle_event(self, ctx: Context, system: System) -> ModuleResult | None:
        self._update_git_repo_lock_settings(ctx.args, system.operating_system)
        self._tick_interval_seconds = max(
            0.1,
            ctx.args.require("status-tick-interval-seconds").value,
        )
        self._git_fast_tick_interval_seconds = max(
            0.1,
            ctx.args.require("status-git-fast-tick-interval-seconds").value,
        )
        self._git_fast_tick_window_seconds = max(
            0.1,
            ctx.args.require("status-git-fast-tick-window-seconds").value,
        )
        self._git_sync_prefix_cycle_pause_seconds = max(
            0.0,
            ctx.args.require("status-git-sync-prefix-cycle-pause-seconds").value,
        )
        bootstrap_changed = self._bootstrap_once(
            ctx.args.require("sys-watch-paths").value,
            system.operating_system,
        )
        self._restart_tracked_animation_cycles(ctx.path)
        parts = self._parse_status_parts(ctx.args.require("status").value)
        banner_text, banner_speed_ms, banner_max_chars = (
            self._normalize_banner_settings(
                ctx.args.require("status-banner").value,
                ctx.args.require("status-banner-speed-milliseconds").value,
                ctx.args.require("status-banner-max-characters").value,
            )
        )
        status_prefix = ctx.args.require("status-prefix").value
        ascii_animation_frames, ascii_animation_speed_ms = (
            self._normalize_animation_settings(
                ctx.args.require("status-animation").value,
                ctx.args.require("status-animation-speed-milliseconds").value,
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
            target = self._targets.get(os.path.abspath(ctx.path))
            banner_offset = target.banner_offset if target is not None else 0
        current_result = self._apply(
            path=ctx.path,
            parts=parts,
            banner_text=banner_text,
            banner_offset=banner_offset,
            banner_max_chars=banner_max_chars,
            status_prefix=status_prefix,
            ascii_animation_frames=ascii_animation_frames,
            ascii_animation_speed_ms=ascii_animation_speed_ms,
            advance_ascii_frame=True,
            operating_system=system.operating_system,
        )
        if current_result is None:
            current_path = ctx.path
            current_changed = None
        else:
            current_path, current_changed = current_result
        changed = self._merge_changes(bootstrap_changed, current_changed)
        if not changed:
            return None
        return ModuleResult(
            context=replace(ctx, path=current_path),
            changed=changed,
        )

    def created(self, ctx: Context, system: System) -> ModuleResult | None:
        return self._handle_event(ctx, system)

    def modified(self, ctx: Context, system: System) -> ModuleResult | None:
        return self._handle_event(ctx, system)

    def moved(self, ctx: Context, system: System) -> ModuleResult | None:
        return self._handle_event(ctx, system)

    def opened(self, ctx: Context, system: System) -> ModuleResult | None:
        parts = self._parse_status_parts(ctx.args.require("status").value)
        if (
            not ctx.args.require("status-opened-events").value
            and "git_update" not in parts
        ):
            return None
        return self._handle_event(ctx, system)
