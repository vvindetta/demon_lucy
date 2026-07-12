from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime

from demon_lucy.lib.path import find_parent_git_repo


def git_last_commit_timestamp(path: str) -> float | None:
    repo_root = find_parent_git_repo(path)
    if not repo_root:
        return None

    relative_path = os.path.relpath(path, repo_root)
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", relative_path],
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

    try:
        return float((result.stdout or "").strip())
    except ValueError:
        return None


def git_last_activity_timestamp(path: str) -> float | None:
    repo_root = find_parent_git_repo(path)
    if not repo_root:
        return None

    relative_path = os.path.relpath(path, repo_root)
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", relative_path],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or (result.stdout or "").strip():
        return None
    return git_last_commit_timestamp(path)


def content_change_timestamp(
    path: str,
    *,
    force_filesystem: bool = False,
) -> float | None:
    if not force_filesystem:
        git_timestamp = git_last_activity_timestamp(path)
        if git_timestamp is not None:
            return git_timestamp
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def format_local_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).astimezone().strftime("%Y-%m-%d %H:%M")


def format_timestamp_age(
    timestamp: float,
    *,
    now_timestamp: float | None = None,
) -> str:
    now = time.time() if now_timestamp is None else now_timestamp
    seconds = max(0, int(now - timestamp))
    if seconds < 60:
        return "less than a minute ago"

    minutes = seconds // 60
    if minutes < 60:
        unit = "minute" if minutes == 1 else "minutes"
        return f"{minutes} {unit} ago"

    hours = seconds // 3600
    if hours < 24:
        unit = "hour" if hours == 1 else "hours"
        return f"{hours} {unit} ago"

    days = seconds // 86400
    unit = "day" if days == 1 else "days"
    return f"{days} {unit} ago"
