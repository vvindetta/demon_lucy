from __future__ import annotations

import logging

from demon_lucy.lib.args.models import KnownArg
from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.notifications import safe_notify
from demon_lucy.lib.text_file import write_text_atomic
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    ModuleResult,
    System,
)
from demon_lucy.modules.alias.rewrite import rewrite_lines
from demon_lucy.modules.alias.rules import (
    CommandAliasRule,
    RuleError,
    TextAliasRule,
    known_flags,
    parse_command_rule,
    parse_text_rule,
)
from demon_lucy.modules.alias.text import rewrite_text_lines

logger = logging.getLogger(__name__)


class Alias(AbstractModule):
    name: str = "alias"
    priority: int = 0

    template = [
        KnownArg(
            name="alias-command",
            value_type=str,
            default=[],
            literal_value_count=1,
            description="Define a command shortcut: name=flags. Invoke it as --name; {args} forwards arguments. Repeat for more rules. System flags and --cmd cannot be targets. Example: --alias-command 'date=--banner-date'.",
        ),
        KnownArg(
            name="alias-text",
            value_type=str,
            default=[],
            literal_value_count=1,
            description="Replace literal source=replacement text inside notes. Matching is case-sensitive; spaces and punctuation are preserved. Repeat for more rules. Command lines, fenced code and generated blocks are skipped. Example: --alias-text ' -- = — '.",
        ),
        KnownArg(
            name="alias-dry-run",
            value_type=bool,
            default=False,
            description="Log alias rewrites without changing files.",
        ),
    ]

    def _parse_rules(
        self,
        ctx: Context,
        system: System,
    ) -> tuple[dict[str, CommandAliasRule], dict[str, TextAliasRule]]:
        known_flag_values = known_flags(system)
        command_rules: dict[str, CommandAliasRule] = {}
        text_rules: dict[str, TextAliasRule] = {}

        for raw_rule in ctx.args.require("alias-command").value:
            parsed = parse_command_rule(raw_rule, known_flag_values=known_flag_values)
            if isinstance(parsed, RuleError):
                self._log_rule_error(ctx, parsed)
                continue
            command_rules[parsed.alias_flag] = parsed

        for raw_rule in ctx.args.require("alias-text").value:
            parsed_text = parse_text_rule(raw_rule)
            if isinstance(parsed_text, RuleError):
                self._log_rule_error(ctx, parsed_text)
                continue
            text_rules[parsed_text.source] = parsed_text

        return command_rules, text_rules

    @staticmethod
    def _notification_key(error: RuleError) -> str:
        alias = error.alias or "-"
        return f"alias:{alias}:{error.reason}"

    def _log_rule_error(
        self,
        ctx: Context,
        error: RuleError,
    ) -> None:
        logger.error(
            log_record(
                "alias.rule_invalid",
                id=ctx.event_id,
                reason=error.reason,
                alias=error.alias,
                target=error.detail,
                raw=error.raw,
            )
        )
        safe_notify(
            self._notification_key(error),
            f"Invalid alias: {error.reason} {error.detail}".strip(),
            args=ctx.args,
            use_rare_mode=True,
        )

    def _apply(self, *, ctx: Context, system: System) -> ModuleResult | None:
        if not (
            ctx.args.require("alias-command").value
            or ctx.args.require("alias-text").value
        ):
            return None

        command_rules, text_rules = self._parse_rules(ctx, system)
        if not command_rules and not text_rules:
            logger.info(
                log_record(
                    "alias.skip",
                    id=ctx.event_id,
                    path=ctx.path,
                    reason="no_valid_rules",
                )
            )
            return None

        try:
            with open(ctx.path, "r", encoding="utf-8", newline="") as file_handle:
                lines = file_handle.readlines()
        except FileNotFoundError:
            return None
        except (UnicodeDecodeError, OSError) as exc:
            logger.warning(
                log_record(
                    "alias.skip",
                    id=ctx.event_id,
                    path=ctx.path,
                    reason="file_unreadable",
                    error=exc,
                )
            )
            return None

        rewritten_lines, _, alias_count = rewrite_lines(
            lines=lines,
            rules=command_rules,
            template=system.global_template,
            event_id=ctx.event_id,
        )
        rewritten_lines, text_count = rewrite_text_lines(
            lines=rewritten_lines,
            rules=text_rules,
            command_flags=known_flags(system) | set(command_rules),
            event_id=ctx.event_id,
        )
        alias_count += text_count
        changed_lines = sum(
            before != after for before, after in zip(lines, rewritten_lines)
        )

        if not changed_lines:
            logger.info(
                log_record(
                    "alias.skip",
                    id=ctx.event_id,
                    path=ctx.path,
                    reason="no_aliases",
                )
            )
            return None

        if ctx.args.require("alias-dry-run").value:
            logger.info(
                log_record(
                    "alias.rewrite_done",
                    id=ctx.event_id,
                    path=ctx.path,
                    changed_lines=changed_lines,
                    aliases=alias_count,
                    dry_run=True,
                )
            )
            return None

        try:
            write_text_atomic(
                ctx.path, "".join(rewritten_lines), expected_text="".join(lines)
            )
        except OSError as exc:
            logger.error(
                log_record(
                    "alias.write_failed",
                    id=ctx.event_id,
                    path=ctx.path,
                    error=exc,
                )
            )
            safe_notify(
                f"alias-write:{ctx.path}",
                f"Alias rewrite failed for {ctx.path}: {exc}",
                args=ctx.args,
                use_rare_mode=True,
            )
            return None

        logger.info(
            log_record(
                "alias.rewrite_done",
                id=ctx.event_id,
                path=ctx.path,
                changed_lines=changed_lines,
                aliases=alias_count,
            )
        )
        return ModuleResult(context=ctx, changed={ctx.path: 1})

    def created(self, ctx: Context, system: System) -> ModuleResult | None:
        return self._apply(ctx=ctx, system=system)

    def modified(self, ctx: Context, system: System) -> ModuleResult | None:
        return self._apply(ctx=ctx, system=system)

    def moved(self, ctx: Context, system: System) -> ModuleResult | None:
        return self._apply(ctx=ctx, system=system)
