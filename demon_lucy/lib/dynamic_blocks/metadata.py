from __future__ import annotations

import re
from datetime import datetime

_UPDATED_RE = re.compile(
    r"^updated: (?P<timestamp>\d{4}\.\d{2}\.\d{2} \d{2}:\d{2})(?:, .+)?$"
)
_UPDATED_TIMESTAMP_FORMAT = "%Y.%m.%d %H:%M"


def parse_updated_timestamp(line: str) -> float | None:
    if not line.startswith("updated:"):
        return None

    match = _UPDATED_RE.fullmatch(line)
    if match is None:
        raise ValueError("invalid dynamic block updated metadata")
    try:
        value = datetime.strptime(
            match.group("timestamp"),
            _UPDATED_TIMESTAMP_FORMAT,
        )
    except ValueError as exc:
        raise ValueError("invalid dynamic block updated timestamp") from exc
    return value.astimezone().timestamp()


def format_updated_line(updated_timestamp: float) -> str:
    updated_at = (
        datetime.fromtimestamp(updated_timestamp)
        .astimezone()
        .strftime(_UPDATED_TIMESTAMP_FORMAT)
    )
    return f"updated: {updated_at}"
