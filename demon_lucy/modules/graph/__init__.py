from __future__ import annotations

import logging
import shlex
from typing import Optional

from demon_lucy.lib.args.parser import Template, is_valid_flag_token
from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.notifications import safe_notify
from demon_lucy.lib.text_file import detect_newline, write_text_atomic
from demon_lucy.lib.dynamic_blocks.parser import format_dynamic_block
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)
from demon_lucy.modules.graph.params import graph_params_from_command
from demon_lucy.modules.graph.dynamic_block import render_graph, render_graph_dynamic_block

logger = logging.getLogger(__name__)

GraphCommand = tuple[str, list[str]]


class Graph(AbstractModule):
    name: str = "graph"
    priority: int = 24
    dynamic_block_renderers = {
        "graph": render_graph_dynamic_block,
        "graph-regex": render_graph_dynamic_block,
    }

    template: Template = [
        (
            "--graph",
            str,
            [],
            "Build a text graph for a literal search in a file. Format: --graph file pattern [week|month|year|all]. Default period: year.",
            False,
        ),
        (
            "--graph-regex",
            str,
            [],
            "Build a text graph for a regular expression search in a file. Format: --graph-regex file regex [week|month|year|all]. Default period: year.",
            False,
        ),
    ]

    @staticmethod
    def _commands_from_line(line: str) -> list[GraphCommand]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return []
        tokens = shlex.split(stripped, comments=False, posix=True)

        commands: list[GraphCommand] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            flag, separator, inline_value = token.partition("=")
            if flag not in {"--graph", "--graph-regex"}:
                index += 1
                continue

            values: list[str] = []
            if separator:
                values.append(inline_value)
            index += 1
            while index < len(tokens) and not is_valid_flag_token(tokens[index]):
                values.append(tokens[index])
                index += 1
            commands.append((flag, values))
        return commands

    @staticmethod
    def _candidate_lines(arg_lines: dict) -> list[int]:
        return sorted(
            {
                int(value)
                for key in ("graph", "graph_regex")
                for value in (arg_lines.get(key) or [])
            },
            reverse=True,
        )

    def _blocks_from_line(
        self,
        *,
        line: str,
        target_path: str,
        newline: str,
    ) -> list[str]:
        commands = self._commands_from_line(line)
        blocks: list[str] = []
        for flag, values in commands:
            params = graph_params_from_command(flag, values)
            body = render_graph(params, target_path=target_path)
            blocks.append(
                format_dynamic_block(
                    arg=params.arg,
                    params={
                        "source": params.source,
                        "pattern": params.pattern,
                        "period": params.period,
                        "view": params.view,
                    },
                    body=body,
                    newline=newline,
                )
            )
        return blocks

    def _apply(self, *, ctx: Context, system: System) -> Optional[IgnoreMap]:
        candidate_lines = self._candidate_lines(ctx.arg_lines)
        if not candidate_lines:
            return None

        try:
            with open(ctx.path, "r", encoding="utf-8", newline="") as handle:
                original_text = handle.read()
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

        lines = original_text.splitlines(keepends=True)
        newline = detect_newline(original_text)
        rendered_commands = 0
        for line_number in candidate_lines:
            index = line_number - 1
            if index < 0 or index >= len(lines):
                continue
            try:
                blocks = self._blocks_from_line(
                    line=lines[index],
                    target_path=ctx.path,
                    newline=newline,
                )
            except (OSError, ValueError) as exc:
                logger.warning(
                    log_record(
                        "graph.command_failed",
                        id=system.event_id,
                        path=ctx.path,
                        line=line_number,
                        reason="render_failed",
                        error=exc,
                    )
                )
                continue
            if not blocks:
                continue
            lines[index : index + 1] = [newline.join(blocks)]
            rendered_commands += len(blocks)

        if rendered_commands == 0:
            return None

        try:
            write_text_atomic(ctx.path, "".join(lines))
        except OSError as exc:
            logger.error(
                log_record(
                    "graph.write_failed",
                    id=system.event_id,
                    path=ctx.path,
                    error=exc,
                )
            )
            safe_notify(
                f"graph-write:{ctx.path}",
                f"Graph write failed for {ctx.path}: {exc}",
                config=ctx.config,
                use_rare_mode=True,
            )
            return None

        logger.info(
            log_record(
                "graph.render_done",
                id=system.event_id,
                path=ctx.path,
                graphs=rendered_commands,
            )
        )
        return {ctx.path: 1}

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)
