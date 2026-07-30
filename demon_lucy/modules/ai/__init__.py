from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from typing import Optional

from demon_lucy.lib.args.line_edit import delete_args_from_string
from demon_lucy.lib.args.models import KnownArg, Template
from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.notifications import safe_notify
from demon_lucy.lib.path import canonical_path
from demon_lucy.lib.text_file import (
    detect_newline,
    normalize_newlines,
    write_text_atomic,
)
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)
from demon_lucy.modules.ai.runner import CodexRunError, run_codex

logger = logging.getLogger(__name__)


class Ai(AbstractModule):
    name: str = "ai"
    priority: int = 24
    template: Template = [
        KnownArg(
            name="ai",
            value_type=str,
            default=[],
            description="Experimental: edit the current file with local Codex.",
        ),
        KnownArg(
            name="ai-timeout-seconds",
            value_type=int,
            default=900,
            description="Maximum time for one Codex run. Default: 900 seconds.",
        ),
    ]

    @staticmethod
    def _prompts_by_line(ctx: Context) -> list[tuple[int, str]]:
        argument = ctx.args.require("ai")
        if not argument.lines:
            return []

        grouped: dict[int, list[str]] = defaultdict(list)
        for value, line_number in zip(argument.value, argument.lines):
            text = value.strip()
            if text:
                grouped[line_number].append(text)

        return [
            (line_number, " ".join(grouped[line_number]))
            for line_number in sorted(grouped)
            if grouped[line_number]
        ]

    @staticmethod
    def _without_commands(
        source_text: str,
        prompts_by_line: list[tuple[int, str]],
    ) -> str:
        lines = source_text.splitlines(keepends=True)
        for line_number, _prompt in prompts_by_line:
            index = line_number - 1
            if 0 <= index < len(lines):
                lines[index] = delete_args_from_string(lines[index], ["--ai"])
        return "".join(lines)

    @staticmethod
    def _notify_failure(ctx: Context, path: str, reason: str) -> None:
        safe_notify(
            f"ai:{path}",
            f"AI could not update {os.path.basename(path)}: {reason}",
            args=ctx.args,
            use_rare_mode=True,
        )

    def _apply(self, ctx: Context) -> Optional[IgnoreMap]:
        if getattr(ctx.event, "is_directory", False):
            return None

        prompts_by_line = self._prompts_by_line(ctx)
        if not prompts_by_line:
            return None

        path = canonical_path(ctx.path)
        try:
            with open(path, "r", encoding="utf-8", newline="") as handle:
                source_text = handle.read()
        except (OSError, UnicodeDecodeError) as exc:
            logger.error(
                log_record(
                    "ai.run_failed",
                    id=ctx.event_id,
                    path=path,
                    reason="source_unreadable",
                    error=exc,
                )
            )
            self._notify_failure(ctx, path, "source file is unreadable")
            return None

        prompt = "\n\n".join(item for _line, item in prompts_by_line)
        source_without_commands = self._without_commands(source_text, prompts_by_line)
        started_at = time.monotonic()
        logger.info(
            log_record(
                "ai.run_start",
                id=ctx.event_id,
                path=path,
                prompts=len(prompts_by_line),
            )
        )
        try:
            result_text = run_codex(
                source_path=path,
                source_content=source_without_commands,
                prompt=prompt,
                timeout_seconds=ctx.args.require("ai-timeout-seconds").value,
            )
        except CodexRunError as exc:
            logger.error(
                log_record(
                    "ai.run_failed",
                    id=ctx.event_id,
                    path=path,
                    reason=exc.reason,
                    error=exc,
                    duration_ms=(time.monotonic() - started_at) * 1000.0,
                )
            )
            self._notify_failure(ctx, path, exc.reason)
            return None

        try:
            with open(path, "r", encoding="utf-8", newline="") as handle:
                current_text = handle.read()
        except (OSError, UnicodeDecodeError) as exc:
            logger.error(
                log_record(
                    "ai.run_failed",
                    id=ctx.event_id,
                    path=path,
                    reason="source_recheck_failed",
                    error=exc,
                    duration_ms=(time.monotonic() - started_at) * 1000.0,
                )
            )
            self._notify_failure(ctx, path, "source file could not be rechecked")
            return None

        if current_text != source_text:
            logger.warning(
                log_record(
                    "ai.run_skipped",
                    id=ctx.event_id,
                    path=path,
                    reason="source_changed_during_run",
                    duration_ms=(time.monotonic() - started_at) * 1000.0,
                )
            )
            self._notify_failure(ctx, path, "file changed while Codex was running")
            return None

        try:
            write_text_atomic(
                path,
                normalize_newlines(result_text, detect_newline(source_text)),
            )
        except OSError as exc:
            logger.error(
                log_record(
                    "ai.run_failed",
                    id=ctx.event_id,
                    path=path,
                    reason="source_write_failed",
                    error=exc,
                    duration_ms=(time.monotonic() - started_at) * 1000.0,
                )
            )
            self._notify_failure(ctx, path, "result could not be written")
            return None

        logger.info(
            log_record(
                "ai.run_done",
                id=ctx.event_id,
                path=path,
                duration_ms=(time.monotonic() - started_at) * 1000.0,
            )
        )
        return {path: 1}

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx)
