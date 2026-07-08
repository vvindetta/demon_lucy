from __future__ import annotations

import logging
from typing import Optional

from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.notifications import safe_notify
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)
from demon_lucy.modules.alias.rewrite import rewrite_lines, write_lines_atomic
from demon_lucy.modules.alias.rules import AliasRule, RuleError, known_flags, parse_rule

logger = logging.getLogger(__name__)


class Alias(AbstractModule):
    name: str = "alias"
    priority: int = 0

    template = [
        (
            "--alias",
            str,
            [],
            "Alias for note flags. Format: name=expansion. "
            "Example: --alias 'b=--banner {args}' 'todo=--formatter-todo' 'rn=--rename {args}'. "
            "System flags (--sys-*) and --cmd are not rewritten.",
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

    def _parse_rules(
        self,
        ctx: Context,
        system: System,
    ) -> dict[str, AliasRule]:
        known_flag_values = known_flags(system)
        rules: dict[str, AliasRule] = {}

        for raw_rule in ctx.config["alias"]:
            parsed = parse_rule(str(raw_rule), known_flag_values=known_flag_values)
            if isinstance(parsed, RuleError):
                self._log_rule_error(ctx, system, parsed)
                continue
            rules[parsed.alias_flag] = parsed

        return rules

    @staticmethod
    def _notification_key(error: RuleError) -> str:
        alias = error.alias or "-"
        return f"alias:{alias}:{error.reason}"

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
            f"Invalid alias: {error.reason} {error.detail}".strip(),
            config=ctx.config,
            use_rare_mode=True,
        )

    def _apply(self, *, ctx: Context, system: System) -> Optional[IgnoreMap]:
        if not ctx.config["alias"]:
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

        rewritten_lines, changed_lines, alias_count = rewrite_lines(
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
            write_lines_atomic(ctx.path, rewritten_lines)
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
