from __future__ import annotations

import shlex
from dataclasses import dataclass

from demon_lucy.lib.args.parser import (
    is_valid_flag_token,
)
from demon_lucy.modules.abstract_module import System

ARG_PLACEHOLDER = "{args}"


@dataclass(frozen=True)
class AliasRule:
    alias: str
    alias_flag: str
    expansion_tokens: list[str]
    consumes_args: bool
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
    flags: set[str] = set()
    for item in system.global_template:
        flags.add(item.name)
    return flags


def _normalize_alias_name(value: str) -> str:
    return value.strip().lstrip("-").strip().lower()


def _alias_flag(alias_name: str) -> str:
    return "--" + alias_name


def parse_rule(
    raw_rule: str,
    *,
    known_flag_values: set[str],
) -> AliasRule | RuleError:
    raw = str(raw_rule).strip()
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
        expansion_tokens = shlex.split(expansion, comments=False, posix=True)
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

    return AliasRule(
        alias=alias_name,
        alias_flag=alias_flag,
        expansion_tokens=expansion_tokens,
        consumes_args=ARG_PLACEHOLDER in expansion_tokens,
        raw=raw,
    )
