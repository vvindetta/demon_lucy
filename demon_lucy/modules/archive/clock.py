from __future__ import annotations

import os
import subprocess
import time
from datetime import date, datetime
from typing import Optional

from demon_lucy.lib.path import find_parent_with
from demon_lucy.modules.abstract_module import Context


def git_last_activity_timestamp(src_path: str) -> Optional[float]:
    repo_root = find_parent_with(src_path, ".git")
    if not repo_root:
        return None

    rel_path = os.path.relpath(src_path, repo_root)
    try:
        status_result = subprocess.run(
            ["git", "status", "--porcelain", "--", rel_path],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if status_result.returncode != 0:
        return None

    # If file has uncommitted changes, mtime is the fresher signal.
    if (status_result.stdout or "").strip():
        return None

    try:
        log_result = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", rel_path],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if log_result.returncode != 0:
        return None

    timestamp_raw = (log_result.stdout or "").strip()
    if not timestamp_raw:
        return None

    try:
        return float(timestamp_raw)
    except ValueError:
        return None


def git_last_commit_timestamp(src_path: str) -> Optional[float]:
    repo_root = find_parent_with(src_path, ".git")
    if not repo_root:
        return None

    rel_path = os.path.relpath(src_path, repo_root)
    try:
        log_result = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", rel_path],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if log_result.returncode != 0:
        return None

    timestamp_raw = (log_result.stdout or "").strip()
    if not timestamp_raw:
        return None

    try:
        return float(timestamp_raw)
    except ValueError:
        return None


def last_activity_timestamp(ctx: Context, src_path: str) -> Optional[float]:
    if not ctx.config["archive_force_filesystem_mtime"]:
        git_timestamp = git_last_activity_timestamp(src_path)
        if git_timestamp is not None:
            return git_timestamp

    try:
        return os.path.getmtime(src_path)
    except OSError:
        return None


def is_stale(ctx: Context, src_path: str, idle_hours: float) -> bool:
    last_activity = last_activity_timestamp(ctx, src_path)
    if last_activity is None:
        return False
    age_seconds = time.time() - float(last_activity)
    return age_seconds >= max(0.0, float(idle_hours)) * 3600.0


def archive_entry_timestamp(ctx: Context, src_path: str) -> Optional[float]:
    if ctx.config["archive_force_filesystem_mtime"]:
        return None
    return git_last_commit_timestamp(src_path)


def archive_entry_date(timestamp_value: Optional[float]) -> date:
    if timestamp_value is None:
        return datetime.now().date()
    from datetime import datetime as real_datetime

    return real_datetime.fromtimestamp(timestamp_value).date()
