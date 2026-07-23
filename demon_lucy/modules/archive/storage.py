from __future__ import annotations

import os

from demon_lucy.lib.args.line_edit import delete_args_from_string
from demon_lucy.lib.date_sections import parse_exact_date_section_header
from demon_lucy.lib.file_open import open_file_no_follow
from demon_lucy.lib.runtime_system import RuntimeSystem

from demon_lucy.modules.archive.constants import STRIP_FLAGS


def strip_archive_command_lines(lines: list[str]) -> list[str]:
    result: list[str] = []
    for line in lines:
        cleaned = delete_args_from_string(line + "\n", STRIP_FLAGS).rstrip("\n")
        if cleaned.strip() or not line.strip():
            result.append(cleaned)
    return result


def normalize_archive_body(text: str, max_blank_lines: int = 3) -> str:
    lines = strip_archive_command_lines(text.splitlines())
    if not lines:
        return ""

    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1

    end = len(lines) - 1
    while end >= start and not lines[end].strip():
        end -= 1

    if start > end:
        return ""

    result: list[str] = []
    blank_run = 0
    for line in lines[start : end + 1]:
        if line.strip():
            blank_run = 0
            result.append(line)
            continue

        blank_run += 1
        if blank_run <= max_blank_lines:
            result.append("")

    return "\n".join(result)


def _line_without_newline(line: str) -> str:
    return line.rstrip("\r\n")


def _archive_body_exists_in_block(block_text: str, body: str) -> bool:
    haystack = block_text if block_text.endswith("\n") else f"{block_text}\n"
    body_text = body.rstrip("\n")
    needle = f"{body_text}\n"
    return f"\n{needle}" in f"\n{haystack}"


def _append_body_separator(existing_body: str) -> str:
    if not existing_body:
        return ""
    if not existing_body.endswith("\n"):
        return "\n\n"
    if not existing_body.endswith("\n\n"):
        return "\n"
    return ""


def text_archive_content_with_entry(
    *,
    old_content: str,
    header_line: str,
    body: str,
    prefix: str,
    suffix: str,
) -> tuple[str, bool]:
    body = body.rstrip("\n")
    entry = f"{header_line}\n{body}\n"
    if not old_content:
        return entry, True

    lines = old_content.splitlines(keepends=True)
    header_indexes = [
        index
        for index, line in enumerate(lines)
        if _line_without_newline(line) == header_line
    ]

    if not header_indexes:
        sep = _append_body_separator(old_content)
        return f"{old_content}{sep}{entry}", True

    start = header_indexes[-1]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if (
            parse_exact_date_section_header(
                _line_without_newline(lines[index]),
                prefix=prefix,
                suffix=suffix,
            )
            is not None
        ):
            end = index
            break

    existing_body = "".join(lines[start + 1 : end])
    if _archive_body_exists_in_block(existing_body, body):
        return old_content, False

    insert = _append_body_separator(existing_body) + body + "\n"
    if end < len(lines) and not insert.endswith("\n\n"):
        insert += "\n"

    new_lines = [*lines[:end], insert, *lines[end:]]
    return "".join(new_lines), True


def has_multiple_hard_links(path_value: str) -> bool:
    try:
        return os.stat(path_value, follow_symlinks=False).st_nlink > 1
    except OSError:
        return False


def read_text_no_follow(
    path_value: str,
    *,
    runtime_system: RuntimeSystem,
) -> str | None:
    file_descriptor: int | None = None
    try:
        file_descriptor = open_file_no_follow(
            path_value,
            os.O_RDONLY,
            runtime_system=runtime_system,
        )
        if os.fstat(file_descriptor).st_nlink > 1:
            os.close(file_descriptor)
            file_descriptor = None
            return None
        with os.fdopen(file_descriptor, "r", encoding="utf-8") as src_handle:
            file_descriptor = None
            return src_handle.read()
    except (OSError, UnicodeDecodeError):
        if file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
        return None


def write_text_no_follow(
    path_value: str,
    content: str,
    *,
    runtime_system: RuntimeSystem,
) -> bool:
    file_descriptor: int | None = None
    try:
        file_descriptor = open_file_no_follow(
            path_value,
            os.O_WRONLY | os.O_CREAT,
            runtime_system=runtime_system,
        )
        if os.fstat(file_descriptor).st_nlink > 1:
            os.close(file_descriptor)
            file_descriptor = None
            return False
        os.ftruncate(file_descriptor, 0)
        with os.fdopen(file_descriptor, "wb") as file_handle:
            file_descriptor = None
            file_handle.write(content.encode("utf-8"))
    except OSError:
        if file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
        return False
    return True


def write_text_archive_entry(
    *,
    dest_path: str,
    header_line: str,
    body: str,
    prefix: str,
    suffix: str,
    runtime_system: RuntimeSystem,
) -> tuple[bool, bool]:
    old_content = ""
    if os.path.exists(dest_path):
        old_content_value = read_text_no_follow(
            dest_path,
            runtime_system=runtime_system,
        )
        if old_content_value is None:
            return False, False
        old_content = old_content_value

    new_content, changed = text_archive_content_with_entry(
        old_content=old_content,
        header_line=header_line,
        body=body,
        prefix=prefix,
        suffix=suffix,
    )
    if not changed:
        return True, False

    if not write_text_no_follow(
        dest_path,
        new_content,
        runtime_system=runtime_system,
    ):
        return False, False
    return True, True


def safe_archive_stem(src_path: str) -> str:
    stem = os.path.splitext(os.path.basename(src_path))[0].strip()
    cleaned = "".join(
        char if char.isalnum() or char in ("-", "_", ".") else "-" for char in stem
    ).strip(".-")
    return cleaned or "archive"


def unique_file_archive_path(
    *,
    dest_dir: str,
    src_path: str,
    date_label: str,
) -> str | None:
    stem = safe_archive_stem(src_path)
    for index in range(1, 1000):
        suffix = "" if index == 1 else f"-{index}"
        candidate = os.path.join(dest_dir, f"{date_label}---{stem}{suffix}.md")
        if not os.path.lexists(candidate):
            return candidate
    return None


def write_new_archive_file(
    dest_path: str,
    body: str,
    *,
    runtime_system: RuntimeSystem,
) -> bool:
    if os.path.lexists(dest_path):
        return False

    file_descriptor: int | None = None
    try:
        file_descriptor = open_file_no_follow(
            dest_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            runtime_system=runtime_system,
        )
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as file_handle:
            file_descriptor = None
            file_handle.write(body.rstrip() + "\n")
    except OSError:
        if file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
        return False
    return True
