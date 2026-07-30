from __future__ import annotations

import re
import time
from collections.abc import Mapping
from enum import Enum

from demon_lucy.lib.args.models import KnownArg
from demon_lucy.lib.args.parser import ARG_NAME_PATTERN
from demon_lucy.lib.dynamic_blocks import metadata
from demon_lucy.lib.dynamic_blocks.model import DynamicBlock
from demon_lucy.lib.text_file import normalize_newlines


_BEGIN_RE = re.compile(rf"^--- (?P<arg>{ARG_NAME_PATTERN}) begin ---[ \t]*$")
_END_RE = re.compile(rf"^--- (?P<arg>{ARG_NAME_PATTERN}) end ---[ \t]*$")
_PARAM_RE = re.compile(
    rf"^- (?P<key>{ARG_NAME_PATTERN})"
    r"(?: \[(?P<allowed_values>[^\]\r\n]+)\])?:(?P<value>.*)$"
)
_FENCE_RE = re.compile(r"^(?P<ticks>`{3,})(?P<info>[^`]*)$")


def _without_newline(line: str) -> str:
    return line.rstrip("\r\n")


def _line_offsets(lines: list[str]) -> list[int]:
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    return offsets


def _parse_header(
    lines: list[str],
    *,
    start: int,
    block_line: int,
) -> tuple[dict[str, str], tuple[str, ...], float | None, int]:
    params: dict[str, str] = {}
    raw_params: list[str] = []
    updated_timestamp: float | None = None
    index = start
    while index < len(lines):
        content = _without_newline(lines[index])
        if not content:
            index += 1
            continue

        if _END_RE.fullmatch(content) is not None:
            return params, tuple(raw_params), updated_timestamp, index

        match = _PARAM_RE.fullmatch(content)
        if match is None:
            return params, tuple(raw_params), updated_timestamp, index

        key = match.group("key")
        value = match.group("value").strip()
        if key == "updated":
            if updated_timestamp is not None:
                raise ValueError(f"line {index + 1}: duplicate updated metadata")
            updated_timestamp = metadata.parse_updated_timestamp(value)
            index += 1
            continue
        if key in params:
            raise ValueError(f"line {index + 1}: duplicate parameter '{key}'")
        params[key] = value
        raw_params.append(content)
        index += 1

    raise ValueError(f"line {block_line}: missing end marker")


def _find_body(
    text: str,
    lines: list[str],
    offsets: list[int],
    *,
    start: int,
    arg: str,
    block_line: int,
) -> tuple[str, int, int, int]:
    index = start
    closing_fence: re.Pattern[str] | None = None
    while index < len(lines):
        content = _without_newline(lines[index])
        end_match = _END_RE.fullmatch(content)
        if end_match is not None and (
            closing_fence is None or end_match.group("arg") == arg
        ):
            if end_match.group("arg") != arg:
                raise ValueError(
                    f"line {index + 1}: end marker '{end_match.group('arg')}' "
                    f"does not match '{arg}'"
                )
            break

        if closing_fence is not None:
            if closing_fence.fullmatch(content):
                closing_fence = None
            index += 1
            continue

        opening_fence = _FENCE_RE.fullmatch(content)
        if opening_fence is not None:
            tick_count = len(opening_fence.group("ticks"))
            closing_fence = re.compile(rf"^`{{{tick_count},}}[ \t]*$")
            index += 1
            continue

        nested = _BEGIN_RE.fullmatch(content)
        if nested is not None:
            raise ValueError(
                f"line {index + 1}: nested block '{nested.group('arg')}' is not allowed"
            )
        index += 1

    if index >= len(lines):
        if closing_fence is not None:
            raise ValueError(f"line {block_line}: unclosed code fence")
        raise ValueError(f"line {block_line}: missing end marker")
    body_end_index = index
    while body_end_index > start and not _without_newline(lines[body_end_index - 1]):
        body_end_index -= 1
    body_start = offsets[start]
    body_end = offsets[body_end_index]
    return text[body_start:body_end], body_start, body_end, index


def parse_dynamic_blocks(text: str) -> list[DynamicBlock]:
    lines = text.splitlines(keepends=True)
    offsets = _line_offsets(lines)
    blocks: list[DynamicBlock] = []
    index = 0

    while index < len(lines):
        content = _without_newline(lines[index])
        unmatched_end = _END_RE.fullmatch(content)
        if unmatched_end is not None:
            raise ValueError(f"line {index + 1}: end marker without matching begin")

        begin = _BEGIN_RE.fullmatch(content)
        if begin is None:
            index += 1
            continue

        arg = begin.group("arg")
        block_line = index + 1
        content_start = offsets[index + 1]
        params, raw_params, updated_timestamp, body_line = _parse_header(
            lines,
            start=index + 1,
            block_line=block_line,
        )
        body, body_start, body_end, end_index = _find_body(
            text,
            lines,
            offsets,
            start=body_line,
            arg=arg,
            block_line=block_line,
        )

        blocks.append(
            DynamicBlock(
                arg=arg,
                params=params,
                raw_params=raw_params,
                body=body,
                updated_timestamp=updated_timestamp,
                content_start=content_start,
                content_end=offsets[end_index],
                body_start=body_start,
                body_end=body_end,
                line=block_line,
                end_line=end_index + 1,
            )
        )
        index = end_index + 1

    return blocks


def partition_dynamic_blocks(text: str) -> tuple[str, str]:
    blocks = parse_dynamic_blocks(text)
    if not blocks:
        return text, ""

    lines = text.splitlines(keepends=True)
    offsets = _line_offsets(lines)
    archive_parts: list[str] = []
    retained_parts: list[str] = []
    cursor = 0

    for block in blocks:
        start = offsets[block.line - 1]
        end = offsets[block.end_line]
        outside = text[cursor:start]
        archive_parts.append(outside)
        if not outside.strip():
            retained_parts.append(outside)
        retained_parts.append(text[start:end])
        cursor = end

    outside = text[cursor:]
    archive_parts.append(outside)
    if not outside.strip():
        retained_parts.append(outside)

    return "".join(archive_parts), "".join(retained_parts)


def _fence_ticks(body: str) -> str:
    longest = max(
        (len(match.group(0)) for match in re.finditer(r"`+", body)), default=0
    )
    return "`" * max(3, longest + 1)


def format_fenced_body(
    body: str,
    *,
    info: str,
    newline: str = "\n",
) -> str:
    if "`" in info or "\n" in info or "\r" in info:
        raise ValueError("invalid dynamic block fence info")
    normalized_body = normalize_newlines(body, newline).rstrip("\r\n")
    body_text = normalized_body + newline if normalized_body else ""
    ticks = _fence_ticks(normalized_body)
    return ticks + info + newline + body_text + ticks + newline


def format_dynamic_block_section(
    *,
    raw_params: tuple[str, ...],
    body: str,
    updated_timestamp: float,
    newline: str,
) -> str:
    header = (
        f"- updated: {metadata.format_updated_value(updated_timestamp)}",
        *raw_params,
    )
    normalized_body = normalize_newlines(body, newline).rstrip("\r\n")
    body_text = normalized_body + newline if normalized_body else ""
    return newline.join(header) + newline * 2 + body_text + newline


def format_dynamic_block(
    *,
    arg: str,
    params: Mapping[str, object],
    body: str,
    arg_template: KnownArg | None = None,
    show_allowed_values: bool = True,
    newline: str = "\n",
    updated_timestamp: float | None = None,
) -> str:
    if re.fullmatch(ARG_NAME_PATTERN, arg) is None:
        raise ValueError(f"invalid dynamic block arg: {arg}")
    raw_params: list[str] = []
    for key, raw_value in params.items():
        if re.fullmatch(ARG_NAME_PATTERN, key) is None:
            raise ValueError(f"invalid dynamic block parameter: {key}")
        if key == "updated":
            raise ValueError("dynamic block parameter 'updated' is reserved")
        value = str(
            raw_value.value
            if isinstance(raw_value, Enum)
            else raw_value
        )
        if "\n" in value or "\r" in value:
            raise ValueError(f"multiline dynamic block parameter: {key}")
        label = key
        param = None
        if arg_template is not None:
            param = next(
                (item for item in arg_template.params if item.name == key),
                None,
            )
        allowed_values = (
            tuple(str(member.value) for member in param.value_type)
            if param is not None and issubclass(param.value_type, Enum)
            else ()
        )
        if show_allowed_values and allowed_values:
            if any(
                not allowed or any(char in allowed for char in "|]\r\n")
                for allowed in allowed_values
            ):
                raise ValueError(f"invalid dynamic block allowed values: {key}")
            label += f" [{'|'.join(allowed_values)}]"
        raw_params.append(f"- {label}: {value}")

    if updated_timestamp is None:
        updated_timestamp = time.time()
    section = format_dynamic_block_section(
        raw_params=tuple(raw_params),
        body=body,
        updated_timestamp=updated_timestamp,
        newline=newline,
    )
    return (
        f"--- {arg} begin ---{newline}"
        + section
        + f"--- {arg} end ---{newline}"
    )
