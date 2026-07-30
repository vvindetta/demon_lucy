from __future__ import annotations

import os
import shlex
from collections.abc import Mapping

from demon_lucy.lib.args.models import ParsedArgs
from demon_lucy.lib.dynamic_blocks.model import DynamicBlock
from demon_lucy.lib.dynamic_blocks.parser import (
    format_dynamic_block,
    parse_dynamic_blocks,
)
from demon_lucy.lib.path import canonical_path, resolve_file_source_path
from demon_lucy.lib.text_file import detect_newline, normalize_newlines
from demon_lucy.modules.include.blocks import find_paragraphs
from demon_lucy.modules.include.params import (
    IncludeParams,
    include_arg_template,
    include_block_params,
    include_params_from_line,
    normalize_include_params,
)


def _normalize_body(body: str, newline: str) -> str:
    normalized = normalize_newlines(body, newline).rstrip("\r\n")
    return normalized + newline if normalized else ""


def _indent_text(text: str) -> str:
    return "".join(f"\t{line}" for line in text.splitlines(keepends=True))


def _line_offsets(lines: list[str]) -> list[int]:
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    return offsets


def _offset_in_ranges(offset: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in ranges)


def _command_text(params: IncludeParams) -> str:
    values = [shlex.quote(params.source)]
    if params.keyword is not None:
        values.append(shlex.quote(params.keyword))
    return f"--{params.arg} {' '.join(values)}"


def _replace_block_with_command(
    text: str,
    block: DynamicBlock,
    *,
    params: IncludeParams,
    newline: str,
) -> str:
    lines = text.splitlines(keepends=True)
    lines[block.line - 1 : block.end_line] = [f"{_command_text(params)}{newline}"]
    return "".join(lines)


def _render_existing_include_blocks(
    text: str,
    *,
    current_path: str,
    depth: int,
    newline: str,
) -> tuple[str, list[tuple[int, int]]]:
    try:
        blocks = parse_dynamic_blocks(text)
    except ValueError:
        return text, []

    ranges = [(block.content_start, block.content_end) for block in blocks]
    if depth <= 1:
        return text, ranges

    replacements: list[tuple[int, int, str]] = []
    for block in blocks:
        if block.arg not in {"include", "include-find"}:
            continue
        params = normalize_include_params(block.arg, block.params)
        source_overrides = {
            canonical_path(current_path): _replace_block_with_command(
                text,
                block,
                params=params,
                newline=newline,
            )
        }
        rendered = render_include(
            params,
            target_path=current_path,
            depth=depth - 1,
            source_overrides=source_overrides,
        )
        replacements.append(
            (
                block.body_start,
                block.body_end,
                _normalize_body(rendered, newline),
            )
        )

    rendered_text = text
    for start, end, replacement in reversed(replacements):
        rendered_text = rendered_text[:start] + replacement + rendered_text[end:]
    return rendered_text, ranges


def _render_include_commands(
    text: str,
    *,
    current_path: str,
    depth: int,
    newline: str,
    skip_ranges: list[tuple[int, int]],
) -> str:
    if depth <= 1:
        return text

    lines = text.splitlines(keepends=True)
    offsets = _line_offsets(lines)
    rendered_lines: list[str] = []
    for index, line in enumerate(lines):
        if _offset_in_ranges(offsets[index], skip_ranges):
            rendered_lines.append(line)
            continue

        params_list = include_params_from_line(line)
        if not params_list:
            rendered_lines.append(line)
            continue

        blocks: list[str] = []
        for params in params_list:
            source_path = resolve_file_source_path(
                source=params.source,
                target_path=current_path,
            )
            blocks.append(
                format_dynamic_block(
                    arg=params.arg,
                    params=include_block_params(params),
                    body=render_include(
                        params,
                        target_path=current_path,
                        depth=depth - 1,
                        source_overrides={canonical_path(current_path): text},
                    ),
                    arg_template=include_arg_template(params.arg),
                    newline=newline,
                    updated_timestamp=os.path.getmtime(source_path),
                )
            )
        rendered_lines.append(newline.join(blocks))

    return "".join(rendered_lines)


def _render_nested_includes(text: str, *, current_path: str, depth: int) -> str:
    newline = detect_newline(text)
    rendered_text, _ = _render_existing_include_blocks(
        text,
        current_path=current_path,
        depth=depth,
        newline=newline,
    )
    try:
        block_ranges = [
            (block.content_start, block.content_end)
            for block in parse_dynamic_blocks(rendered_text)
        ]
    except ValueError:
        block_ranges = []
    return _render_include_commands(
        rendered_text,
        current_path=current_path,
        depth=depth,
        newline=newline,
        skip_ranges=block_ranges,
    )


def render_file(
    source: str,
    *,
    target_path: str,
    depth: int,
    source_text: str | None = None,
) -> str:
    if depth < 1:
        raise ValueError("include depth must be at least 1")
    source_path = resolve_file_source_path(
        source=source,
        target_path=target_path,
    )

    if source_text is None:
        try:
            with open(source_path, "r", encoding="utf-8", newline="") as handle:
                text = handle.read()
        except UnicodeDecodeError as exc:
            raise ValueError("include source is not UTF-8") from exc
    else:
        text = source_text

    rendered_text = _render_nested_includes(
        text,
        current_path=source_path,
        depth=depth,
    )
    return _indent_text(rendered_text)


def render_found_paragraphs(
    source: str,
    *,
    keyword: str,
    target_path: str,
    depth: int,
    source_overrides: Mapping[str, str] | None = None,
) -> str:
    if depth < 1:
        raise ValueError("include depth must be at least 1")
    paragraphs = find_paragraphs(
        source,
        keyword=keyword,
        target_path=target_path,
        source_overrides=source_overrides,
    )
    rendered = [
        _render_nested_includes(text, current_path=path, depth=depth)
        for path, text in paragraphs
    ]
    return _indent_text("\n\n".join(rendered))


def render_include(
    params: IncludeParams,
    *,
    target_path: str,
    depth: int,
    source_overrides: Mapping[str, str] | None = None,
) -> str:
    source_path = resolve_file_source_path(
        source=params.source,
        target_path=target_path,
    )
    if params.arg == "include":
        return render_file(
            params.source,
            target_path=target_path,
            depth=depth,
            source_text=(source_overrides or {}).get(source_path),
        )
    if params.arg == "include-find" and params.keyword is not None:
        return render_found_paragraphs(
            params.source,
            keyword=params.keyword,
            target_path=target_path,
            depth=depth,
            source_overrides=source_overrides,
        )
    raise ValueError(f"unsupported include arg: {params.arg}")


def render_include_dynamic_block(
    block: DynamicBlock,
    target_path: str,
    args: ParsedArgs,
) -> str:
    params = normalize_include_params(block.arg, block.params)
    depth: int = args.require("include-depth").value
    if depth < 1:
        raise ValueError("include depth must be at least 1")

    with open(target_path, "r", encoding="utf-8", newline="") as handle:
        target_text = handle.read()
    newline = detect_newline(target_text)
    return render_include(
        params,
        target_path=target_path,
        depth=depth,
        source_overrides={
            canonical_path(target_path): _replace_block_with_command(
                target_text,
                block,
                params=params,
                newline=newline,
            )
        },
    )
