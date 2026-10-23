from __future__ import annotations

from dataclasses import dataclass

from demon_lucy.lib.args.aliases import (
    ARG_PLACEHOLDER,
    CommandAliasRule,
    RuleError,
    flag_head,
    parse_command_rule,
)
from demon_lucy.lib.args.parser import is_valid_flag_token, split_arg_line
from demon_lucy.modules.abstract_module import System


@dataclass(frozen=True)
class TextAliasRule:
    source: str
    replacement: str
    raw: str


def known_flags(system: System) -> set[str]:
    return {f"--{item.name}" for item in system.global_template}


def parse_text_rule(raw: str) -> TextAliasRule | RuleError:
    source, separator, replacement = raw.partition("=")
    if not separator:
        return RuleError(raw=raw, reason="missing_equals")
    if not source:
        return RuleError(raw=raw, reason="empty_text_source")
    if "\n" in raw or "\r" in raw:
        return RuleError(raw=raw, reason="multiline_text_rule", alias=source)
    try:
        tokens = split_arg_line(replacement)
    except ValueError:
        tokens = replacement.split()
    if any(is_valid_flag_token(token) for token in tokens):
        return RuleError(raw=raw, reason="text_target_contains_command", alias=source)
    return TextAliasRule(source=source, replacement=replacement, raw=raw)
