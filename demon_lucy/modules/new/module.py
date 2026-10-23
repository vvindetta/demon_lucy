from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from demon_lucy.lib.args.models import KnownArg, Template
from demon_lucy.lib.args.parser import split_arg_line
from demon_lucy.lib.command_blocks import CommandBlock, find_command_blocks
from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.markdown_links import format_file_link
from demon_lucy.lib.notifications import safe_notify
from demon_lucy.lib.path import abs_expand_path, canonical_path
from demon_lucy.lib.text_file import create_bytes_atomic, write_text_atomic
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    ModuleResult,
    System,
)
from demon_lucy.modules.new.storage import CreatedFile, rollback_created_file

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NewFileRequest:
    block: CommandBlock
    path: str
    content: bytes
    replacement: str


class New(AbstractModule):
    name = "new"
    priority = 1
    template: Template = [
        KnownArg(
            name="new",
            value_type=str,
            default=[],
            literal_value_count=1,
            description="Move the following text into a new file and leave a Markdown link. Usage: --new <path>. End with --end or the end of the note; names without an extension use .md.",
        ),
        KnownArg(
            name="end",
            value_type=bool,
            default=False,
            description="End the content of a --new block. Optional at the end of the note.",
        ),
    ]

    def _requests(self, path: str, lines: list[str]) -> list[NewFileRequest]:
        requests: list[NewFileRequest] = []
        destinations: set[str] = set()
        for block in find_command_blocks(lines, opening_flag="--new"):
            tokens = split_arg_line(lines[block.start].strip())
            flag, separator, inline_value = tokens[0].partition("=")
            values = ([inline_value] if separator else []) + tokens[1:]
            if flag != "--new" or len(values) != 1 or not values[0].strip():
                raise ValueError(
                    f"line {block.start + 1}: --new requires one file path"
                )
            raw_path = values[0]
            if raw_path.endswith(("/", os.sep)) or Path(raw_path).name in {
                "",
                ".",
                "..",
            }:
                raise ValueError(f"expected a filename: {raw_path}")
            target = Path(raw_path).expanduser()
            if not target.suffix:
                target = target.with_name(target.name + ".md")
            if not target.is_absolute():
                target = Path(path).parent / target
            destination = abs_expand_path(str(target))
            identity = os.path.normcase(canonical_path(destination))
            if identity == os.path.normcase(canonical_path(path)):
                raise ValueError("the destination is the source note")
            if identity in destinations:
                raise ValueError(f"duplicate destination: {destination}")
            if os.path.lexists(destination):
                raise FileExistsError(f"destination already exists: {destination}")
            destinations.add(identity)
            last_line = lines[block.end - 1]
            newline = last_line[len(last_line.rstrip("\r\n")) :]
            requests.append(
                NewFileRequest(
                    block=block,
                    path=destination,
                    content="".join(lines[block.start + 1 : block.body_end]).encode(
                        "utf-8"
                    ),
                    replacement=format_file_link(path, destination) + newline,
                )
            )
        return requests

    def _apply(self, ctx: Context, system: System) -> ModuleResult | None:
        try:
            with open(ctx.path, "r", encoding="utf-8", newline="") as handle:
                original = handle.read()
        except (OSError, UnicodeError):
            return None
        created: list[CreatedFile] = []
        try:
            lines = original.splitlines(keepends=True)
            requests = self._requests(ctx.path, lines)
            if not requests:
                return None
            for request in requests:
                Path(request.path).parent.mkdir(parents=True, exist_ok=True)
                identity = create_bytes_atomic(request.path, request.content)
                created.append(CreatedFile(request.path, request.content, identity))
            for request in reversed(requests):
                lines[request.block.start : request.block.end] = [request.replacement]
            write_text_atomic(ctx.path, "".join(lines), expected_text=original)
        except (OSError, UnicodeError, ValueError) as exc:
            retained: list[str] = []
            for item in reversed(created):
                try:
                    if not rollback_created_file(item, system.operating_system):
                        retained.append(item.path)
                except OSError:
                    retained.append(item.path)
            logger.error(
                log_record(
                    "new.failed",
                    id=ctx.event_id,
                    path=ctx.path,
                    reason="transfer_failed",
                    error=exc,
                    retained=",".join(retained),
                )
            )
            safe_notify(
                f"new:{ctx.path}",
                f"Could not create note from {ctx.path}: {exc}"
                + (f"; preserved files: {', '.join(retained)}" if retained else ""),
                args=ctx.args,
                use_rare_mode=True,
            )
            return (
                ModuleResult(context=ctx, changed={path: 1 for path in retained})
                if retained
                else None
            )

        changed = {item.path: 1 for item in created}
        changed[ctx.path] = 1
        logger.info(
            log_record(
                "new.created",
                id=ctx.event_id,
                path=ctx.path,
                files=len(created),
                dest=",".join(item.path for item in created),
            )
        )
        return ModuleResult(context=ctx, changed=changed)

    def created(self, ctx: Context, system: System) -> ModuleResult | None:
        return self._apply(ctx, system)

    def modified(self, ctx: Context, system: System) -> ModuleResult | None:
        return self._apply(ctx, system)

    def moved(self, ctx: Context, system: System) -> ModuleResult | None:
        return self._apply(ctx, system)
