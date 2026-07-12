from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterator


DATE_SECTION_VALUE_PATTERN = r"\d{1,2}\.\d{1,2}\.\d{4}"

_DATE_SECTION_HEADER_RE = re.compile(
    r"^\s*---\s*"
    rf"(?P<start>{DATE_SECTION_VALUE_PATTERN})"
    r"(?:\s*(?:-|\.\.\.)\s*"
    rf"(?P<end>{DATE_SECTION_VALUE_PATTERN}))?"
    r"(?:\s+.*)?\s*$"
)
_FULL_DATE_SECTION_PREFIX_RE = re.compile(r"^\s*---\s*\d{1,2}\.")
_PARTIAL_DATE_SECTION_RE = re.compile(
    r"^(?P<prefix>\s*---\s*)(?P<day>\d{1,2})(?P<trailing>[ \t]*)$"
)


@dataclass(frozen=True)
class DateSection:
    start: date
    end: date


def parse_date_section_value(value: str) -> date | None:
    if re.fullmatch(DATE_SECTION_VALUE_PATTERN, value) is None:
        return None

    parts = value.split(".")
    try:
        day, month, year = (int(part) for part in parts)
        return date(year, month, day)
    except ValueError:
        return None


def format_date_section_value(value: date) -> str:
    return f"{value.day:02d}.{value.month:02d}.{value.year:04d}"


def format_date_section_header(
    value: date,
    *,
    prefix: str,
    suffix: str,
) -> str:
    return f"{prefix}{format_date_section_value(value)}{suffix}"


def parse_exact_date_section_header(
    line: str,
    *,
    prefix: str,
    suffix: str,
) -> date | None:
    if not line.startswith(prefix):
        return None
    if suffix and not line.endswith(suffix):
        return None

    value_end = len(line) - len(suffix) if suffix else len(line)
    return parse_date_section_value(line[len(prefix) : value_end])


def parse_date_section_header(line: str) -> DateSection | None:
    match = _DATE_SECTION_HEADER_RE.match(line)
    if match is None:
        return None

    start = parse_date_section_value(match.group("start"))
    if start is None:
        return None

    end = start
    end_value = match.group("end")
    if end_value is not None:
        end = parse_date_section_value(end_value)
        if end is None or end < start:
            return None

    return DateSection(start=start, end=end)


def iter_date_section_days(section: DateSection) -> Iterator[date]:
    current = section.start
    while current <= section.end:
        yield current
        current += timedelta(days=1)


def complete_partial_date_section_headers(
    lines: list[str],
) -> tuple[list[str], bool]:
    completed_lines: list[str] = []
    previous_date: date | None = None
    changed = False

    for original_line in lines:
        line = original_line.rstrip("\r\n")
        newline = original_line[len(line) :]

        section = parse_date_section_header(line)
        if section is not None:
            if previous_date is not None and section.start < previous_date:
                return lines, False
            previous_date = section.end
            completed_lines.append(original_line)
            continue

        if _FULL_DATE_SECTION_PREFIX_RE.match(line):
            return lines, False

        partial_match = _PARTIAL_DATE_SECTION_RE.match(line)
        if partial_match is None:
            completed_lines.append(original_line)
            continue

        if previous_date is None:
            return lines, False

        next_date = previous_date + timedelta(days=1)
        day_text = partial_match.group("day")
        if int(day_text) != next_date.day:
            return lines, False

        completed_lines.append(
            f"{partial_match.group('prefix')}{day_text}"
            f".{next_date.month:02d}.{next_date.year:04d}"
            f"{partial_match.group('trailing')}{newline}"
        )
        previous_date = next_date
        changed = True

    return completed_lines, changed
