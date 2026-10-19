from __future__ import annotations

from dataclasses import dataclass

from demon_lucy.lib.args.parser import (
    is_valid_flag_token,
    split_arg_line,
)
from demon_lucy.modules.abstract_module import System

ARG_PLACEHOLDER = "{args}"


@dataclass(frozen=True)
class CommandAliasRule:
    alias: str
    alias_flag: str
    expansion_tokens: list[str]
    consumes_args: bool
    raw: str


@dataclass(frozen=True)
class TextAliasRule:
    source: str
    replacement: str
    raw: str


@dataclass(frozen=True)
class RuleError:
    raw: str
    reason: str
    detail: str = ""
    alias: str = ""


def flag_head(token: str) -> str:
    return token.split("=", 1)[0]


def known_flags(system: System) -> set[str]:
    return {f"--{item.name}" for item in system.global_template}


def _normalize_alias_name(value: str) -> str:
    return value.strip().lstrip("-").strip().lower()


def _alias_flag(alias_name: str) -> str:
    return "--" + alias_name


def parse_command_rule(
    raw_rule: str,
    *,
    known_flag_values: set[str],
) -> CommandAliasRule | RuleError:
    raw = raw_rule.strip()
    if not raw:
        return RuleError(raw=raw_rule, reason="empty_rule")

    if "=" not in raw:
        return RuleError(raw=raw, reason="missing_equals")

    raw_name, raw_expansion = raw.split("=", 1)
    alias_name = _normalize_alias_name(raw_name)
    alias_flag = _alias_flag(alias_name)
    expansion = raw_expansion.strip()

    if not alias_name:
        return RuleError(raw=raw, reason="empty_alias")
    if not is_valid_flag_token(alias_flag):
        return RuleError(
            raw=raw,
            reason="invalid_alias",
            alias=alias_flag,
        )
    if alias_name.startswith("sys-"):
        return RuleError(
            raw=raw,
            reason="system_alias_forbidden",
            alias=alias_flag,
        )
    if alias_flag in known_flag_values:
        return RuleError(
            raw=raw,
            reason="canonical_flag_shadowed",
            alias=alias_flag,
        )
    if not expansion:
        return RuleError(
            raw=raw,
            reason="empty_expansion",
            alias=alias_flag,
        )

    try:
        expansion_tokens = split_arg_line(expansion)
    except ValueError as exc:
        return RuleError(
            raw=raw,
            reason="invalid_expansion",
            detail=str(exc),
            alias=alias_flag,
        )

    if not expansion_tokens:
        return RuleError(
            raw=raw,
            reason="empty_expansion",
            alias=alias_flag,
        )

    if not is_valid_flag_token(expansion_tokens[0]):
        return RuleError(raw=raw, reason="command_target_required", alias=alias_flag)

    for token in expansion_tokens:
        if token == ARG_PLACEHOLDER:
            continue
        if not is_valid_flag_token(token):
            continue
        flag = flag_head(token)
        if flag.startswith("--sys-"):
            return RuleError(
                raw=raw,
                reason="system_target_forbidden",
                detail=flag,
                alias=alias_flag,
            )
        if flag == "--cmd":
            return RuleError(
                raw=raw,
                reason="unsafe_target_forbidden",
                detail=flag,
                alias=alias_flag,
            )
        if flag not in known_flag_values:
            return RuleError(
                raw=raw,
                reason="unknown_target_flag",
                detail=flag,
                alias=alias_flag,
            )

    return CommandAliasRule(
        alias=alias_name,
        alias_flag=alias_flag,
        expansion_tokens=expansion_tokens,
        consumes_args=ARG_PLACEHOLDER in expansion_tokens,
        raw=raw,
    )


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
