from __future__ import annotations

import logging
import os
from typing import Optional

from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.notifications import safe_notify
from demon_lucy.lib.path import canonical_path
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)
from demon_lucy.modules.voice.providers import (
    VoiceError,
    listen_once,
)

logger = logging.getLogger(__name__)


class Voice(AbstractModule):
    name: str = "voice"
    priority: int = 45

    template = [
        (
            "--voice",
            bool,
            False,
            "Record one voice snippet and replace --voice inline with recognized text.",
            False,
        ),
        (
            "--voice-offline-vosk-model-path",
            str,
            "",
            "Path to a local Vosk model directory.",
            False,
        ),
        (
            "--voice-timeout-seconds",
            int,
            60,
            "Safety timeout for one inline --voice listen. Default: 60.",
            False,
        ),
        (
            "--voice-recorder-path",
            str,
            "arecord",
            "Recorder executable that writes raw mono PCM16 audio to stdout. Default: arecord.",
            False,
        ),
        (
            "--voice-sample-rate",
            int,
            16000,
            "Recorder and Vosk sample rate. Default: 16000.",
            False,
        ),
    ]

    @staticmethod
    def _inline_text(text: str) -> str:
        return " ".join(str(text).split()).strip()

    def _replace_voice_line(self, line: str, text: str) -> tuple[str, bool]:
        inline_text = self._inline_text(text)
        if not inline_text:
            return line, False
        newline = "\n" if line.endswith("\n") else ""
        replaced = inline_text + newline
        return replaced, replaced != line

    @staticmethod
    def _notify_failure(ctx: Context, path: str, message: str) -> None:
        safe_notify(
            f"voice:{canonical_path(path)}",
            message,
            config=ctx.config,
            use_rare_mode=True,
        )

    def _apply(self, *, ctx: Context, system: System) -> Optional[IgnoreMap]:
        event = system.event
        if getattr(event, "is_directory", False):
            return None

        path = canonical_path(ctx.path)
        if not os.path.isfile(path):
            return None

        try:
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except (FileNotFoundError, UnicodeDecodeError, OSError):
            return None

        voice_line_indexes: list[int] = []
        for raw_lineno in ctx.arg_lines.get("voice") or []:
            try:
                index = int(raw_lineno) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(lines) and index not in voice_line_indexes:
                voice_line_indexes.append(index)

        if not voice_line_indexes:
            return None

        changed = False
        for index in voice_line_indexes:
            try:
                result = listen_once(ctx.config)
            except VoiceError as exc:
                logger.error(
                    log_record(
                        "voice.inline_failed",
                        id=system.event_id,
                        path=path,
                        line=index + 1,
                        reason=exc.reason,
                        error=exc,
                    )
                )
                self._notify_failure(
                    ctx,
                    path,
                    f"Voice inline failed for {os.path.basename(path)}: {exc}",
                )
                continue

            new_line, line_changed = self._replace_voice_line(lines[index], result.text)
            if not line_changed:
                logger.info(
                    log_record(
                        "voice.inline_skip",
                        id=system.event_id,
                        path=path,
                        line=index + 1,
                        reason="empty_transcript",
                    )
                )
                continue

            lines[index] = new_line
            changed = True
            logger.info(
                log_record(
                    "voice.inline_transcribed",
                    id=system.event_id,
                    path=path,
                    line=index + 1,
                    provider=result.provider,
                    model=result.model,
                )
            )

        if not changed:
            return None

        with open(path, "w", encoding="utf-8") as handle:
            handle.writelines(lines)

        return {path: 1}

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)
