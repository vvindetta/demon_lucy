from __future__ import annotations

import os
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Pattern

from demon_lucy.lib.path import canonical_path, find_parent_git_repo

DATE_HEADER_RE = re.compile(
    r"^\s*---\s*(\d{2})\.(\d{2})\.(\d{4})(?:\s+.*)?\s*$"
)
GIT_COMMIT_MARKER = "__DEMON_LUCY_GRAPH_COMMIT__"


@dataclass(frozen=True)
class GraphDataResult:
    counts_by_date: dict[date, int]
    source: str
    error_reason: str = ""
    error_detail: str = ""

    @property
    def ok(self) -> bool:
        return not self.error_reason


def compile_search_pattern(pattern: str, *, regex: bool) -> Pattern[str]:
    if regex:
        return re.compile(pattern)
    return re.compile(re.escape(pattern))


def count_matches(text: str, pattern: Pattern[str]) -> int:
    return sum(1 for _match in pattern.finditer(text))


def counts_from_dated_text(text: str, pattern: Pattern[str]) -> dict[date, int] | None:
    counts: Counter[date] = Counter()
    current_date: date | None = None
    current_lines: list[str] = []
    found_date = False

    def flush_current_section() -> None:
        nonlocal current_lines
        if current_date is None:
            current_lines = []
            return
        counts.setdefault(current_date, 0)
        if current_lines:
            counts[current_date] += count_matches("".join(current_lines), pattern)
        current_lines = []

    for raw_line in text.splitlines(keepends=True):
        match = DATE_HEADER_RE.match(raw_line)
        if match:
            flush_current_section()
            day_text, month_text, year_text = match.groups()
            try:
                current_date = date(
                    int(year_text),
                    int(month_text),
                    int(day_text),
                )
            except ValueError:
                current_date = None
            found_date = True
            if current_date is not None:
                counts.setdefault(current_date, 0)
            continue

        if current_date is not None:
            current_lines.append(raw_line)

    flush_current_section()
    if not found_date:
        return None
    return dict(counts)


def counts_from_dated_file(path: str, pattern: Pattern[str]) -> GraphDataResult:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return GraphDataResult(
            counts_by_date={},
            source="file_dates",
            error_reason="file_missing",
            error_detail=path,
        )
    except (UnicodeDecodeError, OSError) as exc:
        return GraphDataResult(
            counts_by_date={},
            source="file_dates",
            error_reason="file_unreadable",
            error_detail=str(exc),
        )

    counts = counts_from_dated_text(text, pattern)
    if counts is None:
        return GraphDataResult(counts_by_date={}, source="file_dates")
    return GraphDataResult(counts_by_date=counts, source="file_dates")


def counts_from_git_history(path: str, pattern: Pattern[str]) -> GraphDataResult:
    absolute_path = canonical_path(path)
    repo_root = find_parent_git_repo(absolute_path)
    if not repo_root:
        return GraphDataResult(
            counts_by_date={},
            source="git_history",
            error_reason="git_history_unavailable",
            error_detail="not inside a git repository",
        )

    try:
        relative_path = Path(absolute_path).relative_to(repo_root)
    except ValueError:
        return GraphDataResult(
            counts_by_date={},
            source="git_history",
            error_reason="git_history_unavailable",
            error_detail="file is outside repository root",
        )

    relative_posix = relative_path.as_posix()
    command = [
        "git",
        "log",
        "--follow",
        f"--format={GIT_COMMIT_MARKER}%x00%ct",
        "-p",
        "--",
        relative_posix,
    ]
    try:
        result = subprocess.run(
            command,
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=8,
        )
    except subprocess.TimeoutExpired:
        return GraphDataResult(
            counts_by_date={},
            source="git_history",
            error_reason="git_history_timeout",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return GraphDataResult(
            counts_by_date={},
            source="git_history",
            error_reason="git_history_unavailable",
            error_detail=str(exc),
        )

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git log failed").strip()
        return GraphDataResult(
            counts_by_date={},
            source="git_history",
            error_reason="git_history_failed",
            error_detail=detail,
        )
    if not result.stdout:
        return GraphDataResult(
            counts_by_date={},
            source="git_history",
            error_reason="git_history_empty",
        )

    counts: Counter[date] = Counter()
    current_date: date | None = None
    saw_commit = False
    for raw_line in result.stdout.splitlines():
        if raw_line.startswith(GIT_COMMIT_MARKER):
            saw_commit = True
            current_date = None
            _marker, _nul, timestamp_text = raw_line.partition("\x00")
            try:
                current_date = datetime.fromtimestamp(float(timestamp_text)).date()
                counts.setdefault(current_date, 0)
            except (TypeError, ValueError, OSError):
                current_date = None
            continue

        if current_date is None:
            continue
        if not raw_line.startswith("+") or raw_line.startswith("+++"):
            continue
        counts[current_date] += count_matches(raw_line[1:], pattern)

    if not saw_commit:
        return GraphDataResult(
            counts_by_date={},
            source="git_history",
            error_reason="git_history_empty",
        )
    return GraphDataResult(counts_by_date=dict(counts), source="git_history")


def load_graph_data(path: str, pattern: Pattern[str]) -> GraphDataResult:
    dated_result = counts_from_dated_file(path, pattern)
    if dated_result.error_reason:
        return dated_result
    if dated_result.counts_by_date:
        return dated_result
    return counts_from_git_history(path, pattern)


def resolve_graph_target_path(*, command_path: str, note_path: str) -> str:
    expanded = Path(command_path).expanduser()
    if expanded.is_absolute():
        return canonical_path(str(expanded))
    return canonical_path(str(Path(os.path.dirname(note_path)) / expanded))
