from __future__ import annotations

import time
from datetime import date, datetime
from demon_lucy.lib.file_time import (
    content_change_timestamp,
    git_last_commit_timestamp,
)
from demon_lucy.modules.abstract_module import Context


def is_stale(ctx: Context, src_path: str, idle_hours: float) -> bool:
    last_activity = content_change_timestamp(
        src_path,
        force_filesystem=ctx.args.require("archive-force-filesystem-mtime").value,
    )
    if last_activity is None:
        return False
    age_seconds = time.time() - last_activity
    return age_seconds >= max(0.0, idle_hours) * 3600.0


def archive_entry_timestamp(ctx: Context, src_path: str) -> float | None:
    if ctx.args.require("archive-force-filesystem-mtime").value:
        return None
    return git_last_commit_timestamp(src_path)


def archive_entry_date(timestamp_value: float | None) -> date:
    if timestamp_value is None:
        return datetime.now().date()
    from datetime import datetime as real_datetime

    return real_datetime.fromtimestamp(timestamp_value).date()
