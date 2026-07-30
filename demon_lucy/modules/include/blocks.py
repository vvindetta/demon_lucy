from __future__ import annotations

import os
from collections.abc import Mapping

from demon_lucy.lib.path import canonical_path, resolve_file_source_path
from demon_lucy.lib.text_file import normalize_newlines


def split_paragraphs(text: str) -> list[str]:
    paragraphs: list[str] = []
    current_lines: list[str] = []
    for line in normalize_newlines(text, "\n").splitlines():
        if line.strip():
            current_lines.append(line)
            continue
        if current_lines:
            paragraphs.append("\n".join(current_lines))
            current_lines = []
    if current_lines:
        paragraphs.append("\n".join(current_lines))
    return paragraphs


def _directory_files(directory: str) -> list[str]:
    paths: list[str] = []
    for root, directory_names, file_names in os.walk(directory):
        directory_names[:] = sorted(
            name
            for name in directory_names
            if not name.startswith(".") and not os.path.islink(os.path.join(root, name))
        )
        for file_name in sorted(file_names):
            if file_name.startswith("."):
                continue
            path = os.path.join(root, file_name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            paths.append(canonical_path(path))
    return paths


def _source_files(source_path: str) -> tuple[list[str], bool]:
    if os.path.isfile(source_path):
        return [source_path], False
    if os.path.isdir(source_path):
        return _directory_files(source_path), True
    raise FileNotFoundError(source_path)


def find_paragraphs(
    source: str,
    *,
    keyword: str,
    target_path: str,
    source_overrides: Mapping[str, str] | None = None,
) -> list[tuple[str, str]]:
    source_path = resolve_file_source_path(
        source=source,
        target_path=target_path,
    )
    source_files, source_is_directory = _source_files(source_path)
    overrides = source_overrides or {}
    matches: list[tuple[str, str]] = []
    for path in source_files:
        text = overrides.get(path)
        if text is None:
            try:
                with open(path, "r", encoding="utf-8", newline="") as handle:
                    text = handle.read()
            except UnicodeDecodeError:
                if source_is_directory:
                    continue
                raise ValueError("include source is not UTF-8") from None
        for paragraph in split_paragraphs(text):
            first_line = paragraph.partition("\n")[0]
            if first_line.startswith(keyword):
                matches.append((path, paragraph))
    return matches
