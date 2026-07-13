from __future__ import annotations

import logging
from typing import Optional, cast

from demon_lucy.lib.dynamic_blocks.parser import (
    format_dynamic_block,
    parse_dynamic_blocks,
)
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
    include_arg_template,
    include_block_params,
    include_params_from_line,
)
from demon_lucy.modules.include.render import (
    render_include,
    render_include_dynamic_block,
)

logger = logging.getLogger(__name__)


class Include(AbstractModule):
    name = "include"
    priority = 25
    template = INCLUDE_TEMPLATE
    dynamic_block_renderers = {
        "include": render_include_dynamic_block,
        "include-find": render_include_dynamic_block,
    }

    def _apply(self, *, ctx: Context, system: System) -> Optional[IgnoreMap]:
        raw_candidate_lines = {
            int(value)
            for key in ("include", "include_find")
            for value in (ctx.arg_lines.get(key) or [])
        }
        if not raw_candidate_lines:
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

        try:
            dynamic_block_lines = {
                line_number
                for block in parse_dynamic_blocks(original_text)
                for line_number in range(block.line, block.end_line + 1)
            }
        except ValueError:
            dynamic_block_lines = set()
        candidate_lines = sorted(
            raw_candidate_lines - dynamic_block_lines,
            reverse=True,
        )
        if not candidate_lines:
            return None

        lines = original_text.splitlines(keepends=True)
        newline = detect_newline(original_text)
        rendered_items = 0
        include_depth = cast(int, ctx.config["include_depth"])
        if include_depth < 1:
            logger.warning(
                log_record(
                    "include.skip",
                    id=system.event_id,
                    path=ctx.path,
                    reason="invalid_depth",
                    depth=include_depth,
                )
            )
            return None
        for line_number in candidate_lines:
            index = line_number - 1
            if index < 0 or index >= len(lines):
                continue
            try:
                params_list = include_params_from_line(lines[index])
                blocks = [
                    format_dynamic_block(
                        arg=params.arg,
                        params=include_block_params(params),
                        body=render_include(
                            params,
                            target_path=ctx.path,
                            depth=include_depth,
                        ),
                        arg_template=include_arg_template(params.arg),
                        show_allowed_values=not ctx.config[
                            "sys_dynamic_block_hide_allowed_values"
                        ],
                        newline=newline,
                    )
                    for params in params_list
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
            rendered_items += len(blocks)

        if rendered_items == 0:
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
                items=rendered_items,
            )
        )
        return {ctx.path: 1}

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)
