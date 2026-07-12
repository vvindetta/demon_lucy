from __future__ import annotations

import logging
import time
from collections.abc import Mapping

from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.dynamic_blocks.model import DynamicBlock, DynamicBlockRenderer
from demon_lucy.lib.dynamic_blocks.parser import (
    format_dynamic_block_section,
    parse_dynamic_blocks,
)
from demon_lucy.lib.text_file import detect_newline, normalize_newlines

logger = logging.getLogger(__name__)


def _normalize_body(body: str, newline: str) -> str:
    normalized = normalize_newlines(body, newline).rstrip("\r\n")
    return normalized + newline if normalized else ""


def _format_content(
    block: DynamicBlock,
    body: str,
    *,
    updated_timestamp: float,
    newline: str,
) -> str:
    return format_dynamic_block_section(
        raw_params=block.raw_params,
        body=body,
        updated_timestamp=updated_timestamp,
        newline=newline,
    )


def refresh_dynamic_blocks(
    *,
    text: str,
    target_path: str,
    renderers: Mapping[str, DynamicBlockRenderer],
    event_id: str = "",
) -> tuple[str, int]:
    if not renderers:
        return text, 0

    blocks = parse_dynamic_blocks(text)
    newline = detect_newline(text)
    now_timestamp = time.time()
    replacements: list[tuple[int, int, str]] = []

    for block in blocks:
        renderer = renderers.get(block.arg)
        if renderer is None:
            logger.info(
                log_record(
                    "dynamic_block.renderer_missing",
                    id=event_id,
                    path=target_path,
                    arg=block.arg,
                    line=block.line,
                    reason="renderer_missing",
                )
            )
            continue

        try:
            rendered = renderer(block, target_path)
        except (OSError, ValueError) as exc:
            logger.warning(
                log_record(
                    "dynamic_block.render_failed",
                    id=event_id,
                    path=target_path,
                    arg=block.arg,
                    line=block.line,
                    reason="render_failed",
                    error=exc,
                )
            )
            continue
        except Exception as exc:
            logger.exception(
                log_record(
                    "dynamic_block.render_failed",
                    id=event_id,
                    path=target_path,
                    arg=block.arg,
                    line=block.line,
                    reason="renderer_exception",
                    error=exc,
                )
            )
            continue

        normalized_body = _normalize_body(rendered, newline)
        existing_body = _normalize_body(block.body, newline)
        content_changed = normalized_body != existing_body
        updated_timestamp = (
            now_timestamp
            if content_changed or block.updated_timestamp is None
            else block.updated_timestamp
        )
        replacement = _format_content(
            block,
            normalized_body,
            updated_timestamp=updated_timestamp,
            newline=newline,
        )
        if replacement == text[block.content_start : block.content_end]:
            continue
        replacements.append((block.content_start, block.content_end, replacement))

    refreshed = text
    for start, end, replacement in reversed(replacements):
        refreshed = refreshed[:start] + replacement + refreshed[end:]
    return refreshed, len(replacements)
