from __future__ import annotations

import logging
import os
import shlex
import tempfile
from dataclasses import dataclass
from typing import Optional

from demon_lucy.lib.args.parser import (
    is_valid_flag_token,
    parse_template_item,
)
from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.notifications import safe_notify
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)

logger = logging.getLogger(__name__)

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


class Alias(AbstractModule):
    name: str = "alias"
    priority: int = 0

    template = [
        (
            "--alias-rule",
            str,
            [],
            "Experimental alias rule for note flags. Format: name=expansion. "
            "Example: --alias-rule 'b=--banner {args}'. System flags (--sys-*) and --cmd are not rewritten.",
            False,
        ),
        (
            "--alias-dry-run",
            bool,
            False,
            "Log alias rewrites without changing files.",
            False,
        ),
    ]

    @staticmethod
    def _flag_head(token: str) -> str:
        return token.split("=", 1)[0]

    @staticmethod
    def _normalize_alias_name(value: str) -> str:
        return value.strip().lstrip("-").strip().lower()

    @staticmethod
    def _alias_flag(alias_name: str) -> str:
        return "--" + alias_name

    def _known_flags(self, system: System) -> set[str]:
        flags: set[str] = set()
        for item in system.global_template:
            flag, _typ, _default, _desc, _required = parse_template_item(item)
            flags.add(flag)
        return flags

    def _parse_rule(
        self,
        raw_rule: str,
        *,
        known_flags: set[str],
    ) -> AliasRule | RuleError:
        raw = str(raw_rule).strip()
        if not raw:
            return RuleError(raw=raw_rule, reason="empty_rule")

        if "=" not in raw:
            return RuleError(raw=raw, reason="missing_equals")

        raw_name, raw_expansion = raw.split("=", 1)
        alias_name = self._normalize_alias_name(raw_name)
        alias_flag = self._alias_flag(alias_name)
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
        if alias_flag in known_flags:
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
            flag = self._flag_head(token)
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
            if flag not in known_flags:
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

    def _parse_rules(
        self,
        ctx: Context,
        system: System,
    ) -> dict[str, AliasRule]:
        known_flags = self._known_flags(system)
        rules: dict[str, AliasRule] = {}

        for raw_rule in ctx.config["alias_rule"]:
            parsed = self._parse_rule(str(raw_rule), known_flags=known_flags)
            if isinstance(parsed, RuleError):
                self._log_rule_error(ctx, system, parsed)
                continue
            rules[parsed.alias_flag] = parsed

        return rules

    @staticmethod
    def _notification_key(error: RuleError) -> str:
        alias = error.alias or "-"
        return f"alias-rule:{alias}:{error.reason}"

    def _log_rule_error(
        self,
        ctx: Context,
        system: System,
        error: RuleError,
    ) -> None:
        logger.error(
            log_record(
                "alias.rule_invalid",
                id=system.event_id,
                reason=error.reason,
                alias=error.alias,
                target=error.detail,
                raw=error.raw,
            )
        )
        safe_notify(
            self._notification_key(error),
            f"Invalid alias rule: {error.reason} {error.detail}".strip(),
            config=ctx.config,
            use_rare_mode=True,
        )

    def _expand_line(
        self,
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

            alias_flag = self._flag_head(token).lower()
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

    def _rewrite_lines(
        self,
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

            expanded_tokens, rewrites = self._expand_line(
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

    @staticmethod
    def _write_lines_atomic(path: str, lines: list[str]) -> None:
        directory = os.path.dirname(path) or "."
        fd, temp_path = tempfile.mkstemp(
            prefix="." + os.path.basename(path) + ".",
            suffix=".tmp",
            dir=directory,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.writelines(lines)
            os.replace(temp_path, path)
        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise

    def _apply(self, *, ctx: Context, system: System) -> Optional[IgnoreMap]:
        if not ctx.config["alias_rule"]:
            return None

        rules = self._parse_rules(ctx, system)
        if not rules:
            logger.info(
                log_record(
                    "alias.skip",
                    id=system.event_id,
                    path=ctx.path,
                    reason="no_valid_rules",
                )
            )
            return None

        try:
            with open(ctx.path, "r", encoding="utf-8") as file_handle:
                lines = file_handle.readlines()
        except FileNotFoundError:
            return None
        except (UnicodeDecodeError, OSError) as exc:
            logger.warning(
                log_record(
                    "alias.skip",
                    id=system.event_id,
                    path=ctx.path,
                    reason="file_unreadable",
                    error=exc,
                )
            )
            return None

        rewritten_lines, changed_lines, alias_count = self._rewrite_lines(
            lines=lines,
            rules=rules,
            system=system,
        )

        if alias_count == 0:
            logger.info(
                log_record(
                    "alias.skip",
                    id=system.event_id,
                    path=ctx.path,
                    reason="no_aliases",
                )
            )
            return None

        if ctx.config["alias_dry_run"]:
            logger.info(
                log_record(
                    "alias.rewrite_done",
                    id=system.event_id,
                    path=ctx.path,
                    changed_lines=changed_lines,
                    aliases=alias_count,
                    dry_run=True,
                )
            )
            return None

        try:
            self._write_lines_atomic(ctx.path, rewritten_lines)
        except OSError as exc:
            logger.error(
                log_record(
                    "alias.write_failed",
                    id=system.event_id,
                    path=ctx.path,
                    error=exc,
                )
            )
            safe_notify(
                f"alias-write:{ctx.path}",
                f"Alias rewrite failed for {ctx.path}: {exc}",
                config=ctx.config,
                use_rare_mode=True,
            )
            return None

        logger.info(
            log_record(
                "alias.rewrite_done",
                id=system.event_id,
                path=ctx.path,
                changed_lines=changed_lines,
                aliases=alias_count,
            )
        )
        return {ctx.path: 1}

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)
