from __future__ import annotations

import os
import time

_SYNC_SUCCESS_MARKER_FILE_NAME = "demon_lucy-last-sync-success.timestamp"


def sync_success_marker_path(repo_root: str) -> str:
    return os.path.join(repo_root, ".git", _SYNC_SUCCESS_MARKER_FILE_NAME)


def write_sync_success_timestamp(
    repo_root: str, timestamp_seconds: float | None = None
) -> bool:
    marker_path = sync_success_marker_path(repo_root)
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
