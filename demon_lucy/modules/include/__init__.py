from __future__ import annotations

import logging
import shlex
from typing import Optional

from demon_lucy.lib.args.parser import is_valid_flag_token
from demon_lucy.lib.dynamic_blocks.parser import format_dynamic_block
from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.notifications import safe_notify
from demon_lucy.lib.text_file import detect_newline, write_text_atomic
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)
from demon_lucy.modules.include.params import (
    INCLUDE_TEMPLATE,
    include_source_from_command,
)
from demon_lucy.modules.include.render import (
    render_file,
    render_include_dynamic_block,
)

logger = logging.getLogger(__name__)


class Include(AbstractModule):
    name = "include"
    priority = 25
    template = INCLUDE_TEMPLATE
    dynamic_block_renderers = {"include": render_include_dynamic_block}

    @staticmethod
    def _sources_from_line(line: str) -> list[str]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return []
        tokens = shlex.split(stripped, comments=False, posix=True)

        sources: list[str] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            flag, separator, inline_value = token.partition("=")
            if flag != "--include":
                index += 1
                continue

            values: list[str] = []
            if separator:
                values.append(inline_value)
            index += 1
            while index < len(tokens) and not is_valid_flag_token(tokens[index]):
                values.append(tokens[index])
                index += 1
            sources.append(include_source_from_command(values))
        return sources

    def _apply(self, *, ctx: Context, system: System) -> Optional[IgnoreMap]:
        candidate_lines = sorted(
            {int(value) for value in (ctx.arg_lines.get("include") or [])},
            reverse=True,
        )
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
                    "include.skip",
                    id=system.event_id,
                    path=ctx.path,
                    reason="file_unreadable",
                    error=exc,
                )
            )
            return None

        lines = original_text.splitlines(keepends=True)
        newline = detect_newline(original_text)
        rendered_files = 0
        for line_number in candidate_lines:
            index = line_number - 1
            if index < 0 or index >= len(lines):
                continue
            try:
                sources = self._sources_from_line(lines[index])
                blocks = [
                    format_dynamic_block(
                        arg="include",
                        params={"source": source},
                        body=render_file(source, target_path=ctx.path),
                        arg_template=INCLUDE_TEMPLATE[0],
                        show_allowed_values=not ctx.config[
                            "sys_dynamic_block_hide_allowed_values"
                        ],
                        newline=newline,
                    )
                    for source in sources
                ]
            except (OSError, ValueError) as exc:
                logger.warning(
                    log_record(
                        "include.command_failed",
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
            rendered_files += len(blocks)

        if rendered_files == 0:
            return None

        try:
            write_text_atomic(ctx.path, "".join(lines))
        except OSError as exc:
            logger.error(
                log_record(
                    "include.write_failed",
                    id=system.event_id,
                    path=ctx.path,
                    error=exc,
                )
            )
            safe_notify(
                f"include-write:{ctx.path}",
                f"Include write failed for {ctx.path}: {exc}",
                config=ctx.config,
                use_rare_mode=True,
            )
            return None

        logger.info(
            log_record(
                "include.render_done",
                id=system.event_id,
                path=ctx.path,
                files=rendered_files,
            )
        )
        return {ctx.path: 1}

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)
