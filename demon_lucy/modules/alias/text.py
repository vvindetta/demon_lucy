from __future__ import annotations

import logging
import re

from demon_lucy.lib.args.parser import is_valid_flag_token, split_arg_line
from demon_lucy.lib.command_blocks import find_command_blocks
from demon_lucy.lib.dynamic_blocks.parser import parse_dynamic_blocks
from demon_lucy.lib.logfmt import log_record
from demon_lucy.modules.alias.rules import TextAliasRule, flag_head

logger = logging.getLogger(__name__)


def _flags_in_text(line: str) -> set[str]:
    try:
        tokens = split_arg_line(line)
    except ValueError:
        # Ordinary prose can contain unmatched quotation marks or apostrophes.
        tokens = line.split()
    return {flag_head(token) for token in tokens if is_valid_flag_token(token)}


def rewrite_text_lines(
    *,
    lines: list[str],
    rules: dict[str, TextAliasRule],
    command_flags: set[str],
    event_id: str,
) -> tuple[list[str], int]:
    if not rules:
        return lines, 0
    try:
        blocks = parse_dynamic_blocks("".join(lines))
    except ValueError as exc:
        logger.warning(
            log_record(
                "alias.skip",
                id=event_id,
                reason="invalid_dynamic_blocks",
                error=exc,
            )
        )
        return lines, 0
    protected_lines = {
        number for block in blocks for number in range(block.line, block.end_line + 1)
    }
    if "--new" in command_flags:
        protected_lines.update(
            number
            for block in find_command_blocks(lines, opening_flag="--new")
            for number in range(block.start + 1, block.end + 1)
        )
    pattern = re.compile(
        "|".join(re.escape(key) for key in sorted(rules, key=len, reverse=True))
    )
    rewritten_lines: list[str] = []
    replacements = 0
    fence_character = ""
    fence_length = 0
    for number, line in enumerate(lines, start=1):
        if number in protected_lines:
            rewritten_lines.append(line)
            continue
        stripped = line.lstrip()
        fence = re.match(r"(`{3,}|~{3,})(.*)$", stripped.rstrip("\r\n"))
        if fence_character:
            if (
                fence
                and fence[1][0] == fence_character
                and len(fence[1]) >= fence_length
                and not fence[2].strip()
            ):
                fence_character = ""
            rewritten_lines.append(line)
            continue
        if fence:
            fence_character = fence[1][0]
            fence_length = len(fence[1])
            rewritten_lines.append(line)
            continue
        original_flags = _flags_in_text(line)
        if original_flags & command_flags:
            rewritten_lines.append(line)
            continue
        updated, count = pattern.subn(lambda match: rules[match[0]].replacement, line)
        if updated == line:
            rewritten_lines.append(line)
            continue
        # A plain-text rule must not turn prose into executable Lucy flags.
        if _flags_in_text(updated) - original_flags:
            logger.warning(
                log_record(
                    "alias.expand_failed",
                    id=event_id,
                    line=number,
                    reason="text_would_create_command",
                )
            )
            rewritten_lines.append(line)
            continue
        rewritten_lines.append(updated)
        replacements += count
    return rewritten_lines, replacements
