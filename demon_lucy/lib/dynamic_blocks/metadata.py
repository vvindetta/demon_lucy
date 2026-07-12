from __future__ import annotations

import re
from datetime import datetime

_UPDATED_VALUE_RE = re.compile(
    r"^(?P<timestamp>\d{4}\.\d{2}\.\d{2} \d{2}:\d{2})(?:, .+)?$"
)
_UPDATED_TIMESTAMP_FORMAT = "%Y.%m.%d %H:%M"


def parse_updated_timestamp(value: str) -> float:
    match = _UPDATED_VALUE_RE.fullmatch(value)
    if match is None:
        raise ValueError("invalid dynamic block updated metadata")
    try:
        parsed_datetime = datetime.strptime(
            match.group("timestamp"),
            _UPDATED_TIMESTAMP_FORMAT,
        )
    except ValueError as exc:
        raise ValueError("invalid dynamic block updated timestamp") from exc
    return parsed_datetime.astimezone().timestamp()


def format_updated_value(updated_timestamp: float) -> str:
    return (
        datetime.fromtimestamp(updated_timestamp)
        .astimezone()
        .strftime(_UPDATED_TIMESTAMP_FORMAT)
    )
