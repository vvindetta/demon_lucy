from __future__ import annotations

import time
from collections.abc import Sequence

from demon_lucy.lib.ascii_art import LUCY_EYE_DOUBLE
from demon_lucy.lib.file_time import git_last_commit_timestamp
from demon_lucy.lib.git_state import read_sync_success_timestamp
from demon_lucy.lib.path import find_parent_git_repo
from demon_lucy.lib.runtime_system import RuntimeSystem
from demon_lucy.modules.abstract_module import RunMode


_INFO_INDENT = 10
_INFO_LABEL_WIDTH = 8


def git_sync_age_text(
    path: str,
    *,
    now_timestamp: float | None = None,
) -> str:
    repo_root = find_parent_git_repo(path)
    if not repo_root:
        return "unavailable"

    timestamp = read_sync_success_timestamp(repo_root)
    if timestamp is None:
        timestamp = git_last_commit_timestamp(repo_root)
    if timestamp is None:
        return "unavailable"

    now = time.time() if now_timestamp is None else now_timestamp
    age_seconds = max(0, int(now - timestamp))
    age_minutes = age_seconds // 60
    if age_minutes < 60:
        return f"{age_minutes}m ago"
    age_hours = age_seconds // 3600
    if age_hours < 24:
        return f"{age_hours}h ago"
    return f"{age_seconds // 86400}d ago"

def _opened_events_state(
    *,
    disabled: bool,
    run_mode: RunMode,
    runtime_system: RuntimeSystem,
) -> str:
    if disabled:
        return "disabled"
    if run_mode == "oneshot" or runtime_system == "linux":
        return "enabled"
    return "unavailable"


def _path_count_text(path_count: int) -> str:
    suffix = "path" if path_count == 1 else "paths"
    return f"{path_count} {suffix}"


def _duration_text(seconds: float) -> str:
    total_minutes = max(0, int(seconds // 60))
    days, remaining_minutes = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remaining_minutes, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def neofetch_lines(
    *,
    run_mode: RunMode,
    runtime_system: RuntimeSystem,
    module_count: int,
    watch_path_count: int,
    opened_events_disabled: bool,
    git_sync_age: str,
    runtime_uptime_seconds: float = 0.0,
    eye: Sequence[str] = LUCY_EYE_DOUBLE,
) -> list[str]:
    eye_width = max(len(line) for line in eye)
    info_lines = [
        ("Mode", run_mode),
        ("Uptime", _duration_text(runtime_uptime_seconds)),
        ("Modules", str(module_count)),
        ("Watch", _path_count_text(watch_path_count)),
        (
            "Opened",
            _opened_events_state(
                disabled=opened_events_disabled,
                run_mode=run_mode,
                runtime_system=runtime_system,
            ),
        ),
        ("Git sync", git_sync_age),
    ]
    lines = [
        *eye,
        "",
        "Demon Lucy".center(eye_width).rstrip(),
        *(
            (" " * _INFO_INDENT)
            + f"{label:<{_INFO_LABEL_WIDTH}}  {value}"
            for label, value in info_lines
        ),
    ]
    return [line + "\n" for line in lines]
