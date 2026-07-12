from __future__ import annotations

import re
import time
from collections.abc import Mapping

from demon_lucy.lib.args.parser import (
    ArgTemplate,
    enum_value_text,
    template_allowed_values,
)
from demon_lucy.lib.dynamic_blocks import metadata
from demon_lucy.lib.dynamic_blocks.model import DynamicBlock
from demon_lucy.lib.text_file import normalize_newlines


_ARG_PATTERN = r"[a-z][a-z0-9-]*"
_BEGIN_RE = re.compile(rf"^--- (?P<arg>{_ARG_PATTERN}) begin ---[ \t]*$")
_END_RE = re.compile(rf"^--- (?P<arg>{_ARG_PATTERN}) end ---[ \t]*$")
_PARAM_RE = re.compile(
    r"^- (?P<key>[a-z][a-z0-9-]*)"
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


def _parse_params(
    lines: list[str],
    *,
    start: int,
    block_line: int,
) -> tuple[dict[str, str], int]:
    params: dict[str, str] = {}
    index = start
    while index < len(lines):
        content = _without_newline(lines[index])
        if not content:
            return params, index + 1

        match = _PARAM_RE.fullmatch(content)
        if match is None:
            raise ValueError(
                f"line {index + 1}: expected '- key: value' or a blank line "
                f"for block starting at line {block_line}"
            )

        key = match.group("key")
        if key in params:
            raise ValueError(f"line {index + 1}: duplicate parameter '{key}'")
        value = match.group("value").strip()
        params[key] = value
        index += 1

    raise ValueError(f"line {block_line}: missing blank line after block parameters")


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

        end_match = _END_RE.fullmatch(content)
        if end_match is not None:
            if end_match.group("arg") != arg:
                raise ValueError(
                    f"line {index + 1}: end marker '{end_match.group('arg')}' "
                    f"does not match '{arg}'"
                )
            break
        index += 1

    if index >= len(lines):
        if closing_fence is not None:
            raise ValueError(f"line {block_line}: unclosed code fence")
        raise ValueError(f"line {block_line}: missing end marker")
    if index == start or _without_newline(lines[index - 1]):
        raise ValueError(f"line {index + 1}: expected a blank line before end marker")

    body_start = offsets[start]
    body_end = offsets[index - 1]
    return text[body_start:body_end], body_start, body_end, index


def _parse_updated_metadata(
    lines: list[str],
    *,
    start: int,
    block_line: int,
) -> tuple[float | None, int]:
    if start >= len(lines):
        return None, start

    updated_timestamp = metadata.parse_updated_timestamp(
        _without_newline(lines[start])
    )
    if updated_timestamp is None:
        return None, start
    if start + 1 >= len(lines) or _without_newline(lines[start + 1]):
        raise ValueError(
            f"line {block_line}: expected a blank line after updated metadata"
        )
    return updated_timestamp, start + 2


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
        params, body_line = _parse_params(
            lines,
            start=index + 1,
            block_line=block_line,
        )
        if body_line >= len(lines):
            raise ValueError(f"line {block_line}: missing body and end marker")

        content_start = offsets[body_line]
        updated_timestamp, body_line = _parse_updated_metadata(
            lines,
            start=body_line,
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
                body=body,
                updated_timestamp=updated_timestamp,
                content_start=content_start,
                body_start=body_start,
                body_end=body_end,
                line=block_line,
                end_line=end_index + 1,
            )
        )
        index = end_index + 1

    return blocks


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


def format_dynamic_block(
    *,
    arg: str,
    params: Mapping[str, object],
    body: str,
    arg_template: ArgTemplate | None = None,
    show_allowed_values: bool = True,
    newline: str = "\n",
    updated_timestamp: float | None = None,
) -> str:
    if re.fullmatch(_ARG_PATTERN, arg) is None:
        raise ValueError(f"invalid dynamic block arg: {arg}")
    lines = [f"--- {arg} begin ---"]
    for key, raw_value in params.items():
        if re.fullmatch(r"[a-z][a-z0-9-]*", key) is None:
            raise ValueError(f"invalid dynamic block parameter: {key}")
        value = enum_value_text(raw_value)
        if "\n" in value or "\r" in value:
            raise ValueError(f"multiline dynamic block parameter: {key}")
        label = key
        param = None
        if arg_template is not None:
            param = next(
                (item for item in arg_template.params if item.name == key),
                None,
            )
        allowed_values = template_allowed_values(param) if param is not None else ()
        if show_allowed_values and allowed_values:
            if any(
                not allowed or any(char in allowed for char in "|]\r\n")
                for allowed in allowed_values
            ):
                raise ValueError(f"invalid dynamic block allowed values: {key}")
            label += f" [{'|'.join(allowed_values)}]"
        lines.append(f"- {label}: {value}")

    now_timestamp = time.time()
    if updated_timestamp is None:
        updated_timestamp = now_timestamp
    updated_line = metadata.format_updated_line(
        updated_timestamp,
        now_timestamp=now_timestamp,
    )
    prefix = newline.join(lines) + newline * 2 + updated_line + newline * 2
    normalized_body = normalize_newlines(body, newline).rstrip("\r\n")
    body_text = normalized_body + newline if normalized_body else ""
    return prefix + body_text + newline + f"--- {arg} end ---" + newline
