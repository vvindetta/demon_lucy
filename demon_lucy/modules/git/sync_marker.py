from __future__ import annotations

import os
import time

from demon_lucy.lib.path import git_dir_for_repo_root

_SYNC_SUCCESS_MARKER_FILE_NAME = "demon_lucy-last-sync-success.timestamp"


def sync_success_marker_path(repo_root: str) -> str:
    git_dir = git_dir_for_repo_root(repo_root)
    if not git_dir:
        return ""
    return os.path.join(git_dir, _SYNC_SUCCESS_MARKER_FILE_NAME)


def write_sync_success_timestamp(
    repo_root: str, timestamp_seconds: float | None = None
) -> bool:
    marker_path = sync_success_marker_path(repo_root)
    if not marker_path:
        return False
    marker_dir = os.path.dirname(marker_path)
    ts_value = float(time.time() if timestamp_seconds is None else timestamp_seconds)
    text_value = f"{int(ts_value)}\n"
    temp_path = f"{marker_path}.tmp"

    try:
        os.makedirs(marker_dir, exist_ok=True)
        with open(temp_path, "w", encoding="utf-8") as handle:
            handle.write(text_value)
        os.replace(temp_path, marker_path)
    except OSError:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        return False
    return True


def read_sync_success_timestamp(repo_root: str) -> float | None:
    marker_path = sync_success_marker_path(repo_root)
    if not marker_path:
        return None
    try:
        with open(marker_path, "r", encoding="utf-8") as handle:
            raw_value = handle.read().strip()
    except OSError:
        return None

    if not raw_value:
        return None

    try:
        return float(raw_value)
    except ValueError:
        return None
