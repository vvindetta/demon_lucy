from __future__ import annotations

from datetime import datetime

from demon_lucy.lib.file_time import format_local_timestamp, format_timestamp_age


def test_format_local_timestamp_uses_minute_precision() -> None:
    timestamp = datetime(2026, 7, 12, 14, 35, 59).timestamp()

    assert format_local_timestamp(timestamp) == "2026-07-12 14:35"


def test_format_timestamp_age_uses_largest_complete_unit() -> None:
    now = 1_000_000.0

    assert format_timestamp_age(now - 30, now_timestamp=now) == (
        "less than a minute ago"
    )
    assert format_timestamp_age(now - 60, now_timestamp=now) == "1 minute ago"
    assert format_timestamp_age(now - 3599, now_timestamp=now) == "59 minutes ago"
    assert format_timestamp_age(now - 3600, now_timestamp=now) == "1 hour ago"
    assert format_timestamp_age(now - 86399, now_timestamp=now) == "23 hours ago"
    assert format_timestamp_age(now - 86400, now_timestamp=now) == "1 day ago"
    assert format_timestamp_age(now - 172800, now_timestamp=now) == "2 days ago"
    assert format_timestamp_age(now + 60, now_timestamp=now) == (
        "less than a minute ago"
    )
