from __future__ import annotations

import io
import logging
import os

from demon_lucy.lib.args.models import ArgSource
from demon_lucy.lib.args.parser import is_valid_flag_token, split_arg_line
from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.notifications import safe_notify
from demon_lucy.lib.path import canonical_path
from demon_lucy.lib.text_file import SourceChangedError, write_text_atomic
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    ModuleResult,
    System,
)
from demon_lucy.modules.voice.config import TEMPLATE
from demon_lucy.modules.voice.errors import VoiceError
from demon_lucy.modules.voice.providers import TranscriptResult, listen_once

logger = logging.getLogger(__name__)


class Voice(AbstractModule):
    name: str = "voice"
    priority: int = 45
    template = TEMPLATE

    @staticmethod
    def _report_failure(ctx: Context, path: str, *, reason: str, error: object) -> None:
        message = " ".join(str(error).split())
        logger.error(
            log_record(
                "voice.inline_failed",
                id=ctx.event_id,
                path=path,
                reason=reason,
                error=message,
            )
        )
        safe_notify(
            f"voice:{path}",
            f"Voice failed for {os.path.basename(path)}: {message}",
            args=ctx.args,
            use_rare_mode=True,
        )

    def _apply(self, ctx: Context) -> ModuleResult | None:
        voice = ctx.args.require("voice")
        if (
            getattr(ctx.event, "is_directory", False)
            or voice.source is not ArgSource.FILE
            or not voice.value
            or not voice.lines
        ):
            return None

        path = canonical_path(ctx.path)
        try:
            with open(path, "r", encoding="utf-8", newline="") as handle:
                source_text = handle.read()
        except FileNotFoundError:
            logger.info(
                log_record(
                    "voice.inline_skip",
                    id=ctx.event_id,
                    path=path,
                    reason="source_missing",
                )
            )
            return None
        except (UnicodeDecodeError, OSError) as exc:
            self._report_failure(ctx, path, reason="source_unreadable", error=exc)
            return None

        # Match the parser's physical line numbers without changing line endings.
        lines = io.StringIO(source_text, newline="").readlines()
        transcribed: list[tuple[int, TranscriptResult]] = []
        for line_number in dict.fromkeys(voice.lines):
            index = line_number - 1
            if not 0 <= index < len(lines):
                continue
            try:
                tokens = split_arg_line(lines[index].strip())
            except ValueError:
                tokens = []
            if (
                not tokens
                or not is_valid_flag_token(tokens[0])
                or "--voice" not in tokens
            ):
                logger.info(
                    log_record(
                        "voice.inline_skip",
                        id=ctx.event_id,
                        path=path,
                        line=line_number,
                        reason="command_changed",
                    )
                )
                continue

            logger.info(
                log_record(
                    "voice.listen_started", id=ctx.event_id, path=path, line=line_number
                )
            )
            try:
                result = listen_once(ctx.args)
            except VoiceError as exc:
                self._report_failure(ctx, path, reason=exc.reason, error=exc)
                break

            text = " ".join(result.text.split())
            if not text:
                logger.info(
                    log_record(
                        "voice.inline_skip",
                        id=ctx.event_id,
                        path=path,
                        line=line_number,
                        reason="empty_transcript",
                    )
                )
                continue

            ending = lines[index][len(lines[index].rstrip("\r\n")) :]
            lines[index] = text + ending
            transcribed.append((line_number, result))

        if not transcribed:
            return None

        try:
            write_text_atomic(path, "".join(lines), expected_text=source_text)
        except SourceChangedError:
            self._report_failure(
                ctx,
                path,
                reason="source_changed_during_recording",
                error="The note changed during recording; the transcript was not written. Run --voice again.",
            )
            return None
        except OSError as exc:
            self._report_failure(ctx, path, reason="source_write_failed", error=exc)
            return None

        for line_number, result in transcribed:
            logger.info(
                log_record(
                    "voice.inline_transcribed",
                    id=ctx.event_id,
                    path=path,
                    line=line_number,
                    provider=result.provider,
                    model=result.model,
                )
            )
        return ModuleResult(context=ctx, changed={path: 1})

    def created(self, ctx: Context, system: System) -> ModuleResult | None:
        return self._apply(ctx)

    def modified(self, ctx: Context, system: System) -> ModuleResult | None:
        return self._apply(ctx)
