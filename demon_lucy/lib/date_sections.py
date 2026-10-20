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
    r"^\s*---\s*(?P<day>\d{1,2})"
    r"(?:[ \t]*(?:-|\.\.\.)[ \t]*(?P<end_day>\d{1,2}))?"
    r"[ \t]*$"
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


def parse_date_section_header(
    line: str,
    *,
    previous_date: date | None = None,
) -> DateSection | None:
    match = _DATE_SECTION_HEADER_RE.match(line)
    if match is None:
        partial = _PARTIAL_DATE_SECTION_RE.match(line.rstrip("\r\n"))
        if partial is None or previous_date is None:
            return None
        return _resolve_partial_date_section(partial, previous_date)

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


def _next_date_with_day(previous_date: date, day: int) -> date | None:
    year = previous_date.year
    month = previous_date.month
    if day <= previous_date.day:
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1

    try:
        candidate = date(year, month, day)
    except ValueError:
        return None
    return candidate if candidate > previous_date else None


def _resolve_partial_date_section(
    match: re.Match[str], previous_date: date
) -> DateSection | None:
    start = _next_date_with_day(previous_date, int(match.group("day")))
    if start is None:
        return None

    end = start
    end_day = match.group("end_day")
    if end_day is not None and int(end_day) != start.day:
        end = _next_date_with_day(start, int(end_day))
        if end is None:
            return None
    return DateSection(start=start, end=end)


def complete_partial_date_section_headers(
    lines: list[str],
    *,
    protected_line_numbers: set[int] | None = None,
) -> tuple[list[str], bool]:
    completed_lines: list[str] = []
    previous_date: date | None = None
    changed = False

    for line_number, original_line in enumerate(lines, start=1):
        if protected_line_numbers and line_number in protected_line_numbers:
            completed_lines.append(original_line)
            continue

        line = original_line.rstrip("\r\n")
        newline = original_line[len(line) :]

        section = parse_date_section_header(line)
        if section is not None:
            previous_date = section.end
            completed_lines.append(original_line)
            continue

        partial_match = _PARTIAL_DATE_SECTION_RE.match(line)
        if partial_match is None:
            if _FULL_DATE_SECTION_PREFIX_RE.match(line):
                previous_date = None
            completed_lines.append(original_line)
            continue

        if previous_date is None:
            completed_lines.append(original_line)
            continue

        section = _resolve_partial_date_section(partial_match, previous_date)
        if section is None:
            previous_date = None
            completed_lines.append(original_line)
            continue

        completed_line = line
        for group, value in (("end_day", section.end), ("day", section.start)):
            if partial_match.group(group) is not None:
                position = partial_match.end(group)
                completed_line = (
                    completed_line[:position]
                    + f".{value.month:02d}.{value.year:04d}"
                    + completed_line[position:]
                )
        completed_lines.append(completed_line + newline)
        previous_date = section.end
        changed = True

    return completed_lines, changed
