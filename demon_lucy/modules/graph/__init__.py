from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass
from typing import Optional

from demon_lucy.lib.args.parser import Template, is_valid_flag_token
from demon_lucy.lib.logfmt import log_record
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)
from demon_lucy.modules.graph.data import (
    compile_search_pattern,
    load_graph_data,
    resolve_graph_target_path,
)
from demon_lucy.modules.graph.render import render_error_block, render_graph_block

logger = logging.getLogger(__name__)

GRAPH_PERIODS = {"week", "month", "year", "all"}
DEFAULT_GRAPH_PERIOD = "month"


@dataclass(frozen=True)
class GraphCommand:
    line_number: int
    flag: str
    file_path: str = ""
    pattern: str = ""
    period: str = DEFAULT_GRAPH_PERIOD
    error_reason: str = ""
    error_detail: str = ""

    @property
    def is_regex(self) -> bool:
        return self.flag == "--graph-regex"

    @property
    def ok(self) -> bool:
        return not self.error_reason


class Graph(AbstractModule):
    name: str = "graph"
    priority: int = 24

    template: Template = [
        (
            "--graph",
            str,
            [],
            "Build a text graph for a literal search in a file. Format: --graph file pattern [week|month|year|all]. Default period: month.",
            False,
        ),
        (
            "--graph-regex",
            str,
            [],
            "Build a text graph for a regular expression search in a file. Format: --graph-regex file regex [week|month|year|all]. Default period: month.",
            False,
        ),
    ]

    @staticmethod
    def _command_from_values(
        *,
        line_number: int,
        flag: str,
        values: list[str],
    ) -> GraphCommand:
        if len(values) < 2:
            return GraphCommand(
                line_number=line_number,
                flag=flag,
                error_reason="invalid_command",
                error_detail=f"{flag} requires: file pattern [period]",
            )

        period = DEFAULT_GRAPH_PERIOD
        pattern_values = values[1:]
        maybe_period = str(values[-1]).strip().lower()
        if len(values) >= 3 and maybe_period in GRAPH_PERIODS:
            period = maybe_period
            pattern_values = values[1:-1]

        pattern = " ".join(pattern_values).strip()
        if not pattern:
            return GraphCommand(
                line_number=line_number,
                flag=flag,
                file_path=values[0],
                period=period,
                error_reason="empty_pattern",
            )

        return GraphCommand(
            line_number=line_number,
            flag=flag,
            file_path=values[0],
            pattern=pattern,
            period=period,
        )

    @staticmethod
    def _commands_from_line(*, line: str, line_number: int) -> list[GraphCommand]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return []
        try:
            tokens = shlex.split(stripped, comments=False, posix=True)
        except ValueError as exc:
            if "--graph" not in stripped:
                return []
            return [
                GraphCommand(
                    line_number=line_number,
                    flag="--graph",
                    error_reason="invalid_line",
                    error_detail=str(exc),
                )
            ]

        commands: list[GraphCommand] = []
        i = 0
        while i < len(tokens):
            token = tokens[i]
            head, separator, inline_value = token.partition("=")
            if head not in ("--graph", "--graph-regex"):
                i += 1
                continue

            values: list[str] = []
            if separator:
                values.append(inline_value)
            i += 1
            while i < len(tokens) and not is_valid_flag_token(tokens[i]):
                values.append(tokens[i])
                i += 1

            commands.append(
                Graph._command_from_values(
                    line_number=line_number,
                    flag=head,
                    values=values,
                )
            )
        return commands

    @staticmethod
    def _commands_from_lines(
        *,
        lines: list[str],
        arg_lines: dict,
    ) -> list[GraphCommand]:
        candidate_lines = {
            int(value)
            for key in ("graph", "graph_regex")
            for value in (arg_lines.get(key) or [])
        }
        if not candidate_lines:
            return []

        commands: list[GraphCommand] = []
        for line_number in sorted(candidate_lines):
            if line_number < 1 or line_number > len(lines):
                continue
            commands.extend(
                Graph._commands_from_line(
                    line=lines[line_number - 1],
                    line_number=line_number,
                )
            )
        return commands

    def _render_command(
        self,
        *,
        command: GraphCommand,
        note_path: str,
        event_id: str,
    ) -> list[str]:
        if not command.ok:
            return render_error_block(
                file_label=command.file_path,
                reason=command.error_reason,
                detail=command.error_detail,
            )

        try:
            search_pattern = compile_search_pattern(
                command.pattern,
                regex=command.is_regex,
            )
        except Exception as exc:
            logger.error(
                log_record(
                    "graph.pattern_invalid",
                    id=event_id,
                    path=note_path,
                    reason="invalid_regex",
                    error=exc,
                )
            )
            return render_error_block(
                file_label=command.file_path,
                reason="invalid_regex",
                detail=str(exc),
            )

        target_path = resolve_graph_target_path(
            command_path=command.file_path,
            note_path=note_path,
        )
        data_result = load_graph_data(target_path, search_pattern)
        if not data_result.ok:
            logger.error(
                log_record(
                    "graph.data_failed",
                    id=event_id,
                    path=note_path,
                    target=target_path,
                    reason=data_result.error_reason,
                    error=data_result.error_detail,
                )
            )
            return render_error_block(
                file_label=command.file_path,
                reason=data_result.error_reason,
                detail=data_result.error_detail,
            )

        return render_graph_block(
            title_pattern=command.pattern,
            period=command.period,
            file_label=command.file_path,
            counts_by_date=data_result.counts_by_date,
        )

    def _apply(self, *, ctx: Context, system: System) -> Optional[IgnoreMap]:
        if not ctx.arg_lines.get("graph") and not ctx.arg_lines.get("graph_regex"):
            return None

        try:
            with open(ctx.path, "r", encoding="utf-8") as file_handle:
                lines = file_handle.readlines()
        except FileNotFoundError:
            return None
        except (UnicodeDecodeError, OSError) as exc:
            logger.warning(
                log_record(
                    "graph.skip",
                    id=system.event_id,
                    path=ctx.path,
                    reason="file_unreadable",
                    error=exc,
                )
            )
            return None

        commands = self._commands_from_lines(lines=lines, arg_lines=ctx.arg_lines)
        if not commands:
            return None

        changed = False
        for command in sorted(commands, key=lambda item: item.line_number, reverse=True):
            index = command.line_number - 1
            if index < 0 or index >= len(lines):
                continue
            block = self._render_command(
                command=command,
                note_path=ctx.path,
                event_id=system.event_id,
            )
            lines[index : index + 1] = block
            changed = True

        if not changed:
            return None

        with open(ctx.path, "w", encoding="utf-8") as file_handle:
            file_handle.writelines(lines)

        logger.info(
            log_record(
                "graph.render_done",
                id=system.event_id,
                path=ctx.path,
                graphs=len(commands),
            )
        )
        return {ctx.path: 1}

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)
