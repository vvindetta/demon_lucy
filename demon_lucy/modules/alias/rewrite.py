from __future__ import annotations

import logging
import shlex

from demon_lucy.lib.args.parser import is_valid_flag_token
from demon_lucy.lib.logfmt import log_record
from demon_lucy.modules.abstract_module import System
from demon_lucy.modules.alias.rules import ARG_PLACEHOLDER, AliasRule, flag_head

logger = logging.getLogger(__name__)


def expand_line(
    *,
    tokens: list[str],
    rules: dict[str, AliasRule],
    line: int,
    system: System,
) -> tuple[list[str], int]:
    output: list[str] = []
    rewrites = 0
    i = 0

    while i < len(tokens):
        token = tokens[i]
        if not is_valid_flag_token(token):
            output.append(token)
            i += 1
            continue

        alias_flag = flag_head(token).lower()
        rule = rules.get(alias_flag)
        if rule is None:
            output.append(token)
            i += 1
            continue

        args: list[str] = []
        inline_value = token.split("=", 1)[1] if "=" in token else None
        if inline_value is not None and not rule.consumes_args:
            logger.warning(
                log_record(
                    "alias.expand_failed",
                    id=system.event_id,
                    alias=rule.alias_flag,
                    line=line,
                    reason="unexpected_inline_value",
                )
            )
            output.append(token)
            i += 1
            continue

        if rule.consumes_args:
            if inline_value is not None:
                args.append(inline_value)
            j = i + 1
            while j < len(tokens) and not is_valid_flag_token(tokens[j]):
                args.append(tokens[j])
                j += 1
            i = j
        else:
            i += 1

        for expansion_token in rule.expansion_tokens:
            if expansion_token == ARG_PLACEHOLDER:
                output.extend(args)
            else:
                output.append(expansion_token)

        rewrites += 1
        logger.info(
            log_record(
                "alias.rewrite",
                id=system.event_id,
                alias=rule.alias_flag,
                expansion=shlex.join(rule.expansion_tokens),
                line=line,
            )
        )

    return output, rewrites


def rewrite_lines(
    *,
    lines: list[str],
    rules: dict[str, AliasRule],
    system: System,
) -> tuple[list[str], int, int]:
    rewritten_lines: list[str] = []
    changed_lines = 0
    alias_count = 0

    for lineno, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            rewritten_lines.append(raw_line)
            continue

        start = stripped.split()[0]
        if not is_valid_flag_token(start):
            rewritten_lines.append(raw_line)
            continue

        try:
            tokens = shlex.split(stripped, comments=False, posix=True)
        except ValueError as exc:
            logger.warning(
                log_record(
                    "alias.expand_failed",
                    id=system.event_id,
                    line=lineno,
                    reason="invalid_line",
                    error=exc,
                )
            )
            rewritten_lines.append(raw_line)
            continue

        expanded_tokens, rewrites = expand_line(
            tokens=tokens,
            rules=rules,
            line=lineno,
            system=system,
        )
        if rewrites == 0:
            rewritten_lines.append(raw_line)
            continue

        newline = "\n" if raw_line.endswith("\n") else ""
        rewritten_lines.append(shlex.join(expanded_tokens) + newline)
        changed_lines += 1
        alias_count += rewrites

    return rewritten_lines, changed_lines, alias_count
