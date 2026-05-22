from __future__ import annotations

import codecs
from typing import Optional

from lucy_notes_manager.modules.git.types import PathLike

_DEFAULT_NETWORK_ERROR_MARKERS = (
    "could not resolve host",
    "temporary failure in name resolution",
    "name or service not known",
    "network is unreachable",
    "no route to host",
    "connection timed out",
    "operation timed out",
    "failed to connect",
    "connection refused",
    "connection reset by peer",
    "connection reset",
    "kex_exchange_identification",
)


def to_str(path_value: PathLike) -> str:
    if isinstance(path_value, bytes):
        return path_value.decode(errors="surrogateescape")
    return path_value


def parse_porcelain_paths(porcelain_text: str) -> list[str]:
    result_paths: list[str] = []
    for line_text in (porcelain_text or "").splitlines():
        trimmed_line = line_text.rstrip("\n")
        if len(trimmed_line) < 4:
            continue
        path_part = trimmed_line[3:]
        if " -> " in path_part:
            path_part = path_part.split(" -> ", 1)[1]
        result_paths.append(path_part)
    return result_paths


def format_path_for_commit_message(path_text: str) -> str:
    raw_value = str(path_text or "").strip()

    def _strip_conflict_arrow_prefix(value: str) -> str:
        if value.startswith("→ "):
            return value[2:].strip()
        if value.startswith("-> "):
            return value[3:].strip()
        return value

    if len(raw_value) >= 2 and raw_value[0] == '"' and raw_value[-1] == '"':
        inner_value = raw_value[1:-1]
        try:
            decoded_value = codecs.decode(inner_value, "unicode_escape")
            return _strip_conflict_arrow_prefix(
                decoded_value.encode("latin-1", errors="surrogateescape")
                .decode("utf-8", errors="surrogateescape")
                .strip()
            )
        except Exception:
            return _strip_conflict_arrow_prefix(inner_value)
    return _strip_conflict_arrow_prefix(raw_value)


def push_rejected_needs_pull(output_text: str) -> bool:
    output_lower = (output_text or "").lower()
    indicators = [
        "non-fast-forward",
        "fetch first",
        "failed to push some refs",
        "remote contains work",
        "updates were rejected",
        "rejected",
    ]
    return any(indicator in output_lower for indicator in indicators)


def failure_looks_like_network_issue(
    output_text: str,
    error_markers: list[str] | tuple[str, ...] | None = None,
) -> bool:
    output_lower = (output_text or "").lower()
    markers: list[str] = list(_DEFAULT_NETWORK_ERROR_MARKERS)
    for marker in error_markers or ():
        marker_text = str(marker).strip().lower()
        if marker_text:
            markers.append(marker_text)
    return any(marker in output_lower for marker in markers)


def union_resolve_text(file_content: str) -> Optional[str]:
    lines = file_content.splitlines(keepends=True)
    resolved_lines: list[str] = []
    line_index = 0
    saw_markers = False

    while line_index < len(lines):
        current_line = lines[line_index]
        if current_line.startswith("<<<<<<< "):
            saw_markers = True
            line_index += 1

            ours_lines: list[str] = []
            while line_index < len(lines) and not lines[line_index].startswith(
                "======="
            ):
                ours_lines.append(lines[line_index])
                line_index += 1
            if line_index >= len(lines) or not lines[line_index].startswith("======="):
                return None
            line_index += 1

            theirs_lines: list[str] = []
            while line_index < len(lines) and not lines[line_index].startswith(
                ">>>>>>> "
            ):
                theirs_lines.append(lines[line_index])
                line_index += 1
            if line_index >= len(lines) or not lines[line_index].startswith(">>>>>>> "):
                return None
            line_index += 1

            resolved_lines.extend(ours_lines)
            if (
                ours_lines
                and theirs_lines
                and (not ours_lines[-1].endswith("\n"))
                and (not theirs_lines[0].startswith("\n"))
            ):
                resolved_lines.append("\n")
            resolved_lines.extend(theirs_lines)
            continue

        resolved_lines.append(current_line)
        line_index += 1

    if not saw_markers:
        return None
    return "".join(resolved_lines)
