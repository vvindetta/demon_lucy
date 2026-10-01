from __future__ import annotations

from datetime import date

import pyfiglet

from demon_lucy.lib.args.line_edit import delete_args_from_string
from demon_lucy.lib.args.models import KnownArg, ParsedArgs, Template
from demon_lucy.lib.text_file import (
    detect_newline,
    normalize_newlines,
    write_text_atomic,
)
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    ModuleResult,
    System,
)


class Banner(AbstractModule):
    name: str = "banner"
    priority: int = 10

    template: Template = [
        KnownArg(
            name="banner",
            value_type=str,
            default=[],
            description="Insert an ASCII text banner at the command line. Usage: --banner <text ...>. Example: --banner hello world.",
        ),
        KnownArg(
            name="banner-date",
            value_type=bool,
            default=False,
            description="Insert today's date as an ASCII banner at the command line. Usage: --banner-date.",
        ),
    ]

    @staticmethod
    def _banner_text(args: ParsedArgs) -> str:
        banner = args.require("banner")
        raw_banner: list[str] = banner.value
        if not raw_banner:
            return ""

        if not banner.lines:
            return " ".join(item.strip() for item in raw_banner).strip()

        first_line = banner.lines[0]
        values: list[str] = []
        for value, line in zip(raw_banner, banner.lines):
            if line != first_line:
                break
            text = value.strip()
            if text:
                values.append(text)
        return " ".join(values).strip()

    def _commands_by_line(self, args: ParsedArgs) -> dict[int, dict[str, str]]:
        commands: dict[int, dict[str, str]] = {}
        banner = args.require("banner")
        banner_text = self._banner_text(args)
        if banner_text and banner.lines:
            commands[banner.lines[0]] = {"--banner": banner_text}

        banner_date = args.require("banner-date")
        if banner_date.value and banner_date.lines:
            today = date.today().isoformat()
            for line_number in banner_date.lines:
                commands.setdefault(line_number, {})["--banner-date"] = today
        return commands

    @staticmethod
    def _render_banner(text: str, newline: str) -> str:
        lines = pyfiglet.figlet_format(text).splitlines()
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        return "".join(line + newline for line in lines)

    def _apply(self, *, path: str, args: ParsedArgs) -> dict[str, int] | None:
        commands_by_line = self._commands_by_line(args)
        if not commands_by_line:
            return None

        with open(path, "r", encoding="utf-8", newline="") as handle:
            lines = handle.readlines()
        original_text = "".join(lines)
        newline = detect_newline(original_text)

        for line_number, commands in sorted(commands_by_line.items(), reverse=True):
            index = line_number - 1
            if not 0 <= index < len(lines):
                continue
            rendered = {
                flag: banner
                for flag, text in commands.items()
                if (banner := self._render_banner(text, newline))
            }
            if not rendered:
                continue
            cleaned = delete_args_from_string(
                normalize_newlines(lines[index], "\n"), rendered
            )
            lines[index] = "".join(rendered.values())
            if cleaned.strip():
                lines[index] += normalize_newlines(cleaned, newline)

        updated_text = "".join(lines)
        if updated_text == original_text:
            return None
        write_text_atomic(path, updated_text, expected_text=original_text)
        return {path: 1}

    def created(self, ctx: Context, system: System) -> ModuleResult | None:
        changed = self._apply(path=ctx.path, args=ctx.args)
        return ModuleResult(context=ctx, changed=changed) if changed else None

    def modified(self, ctx: Context, system: System) -> ModuleResult | None:
        changed = self._apply(path=ctx.path, args=ctx.args)
        return ModuleResult(context=ctx, changed=changed) if changed else None

    def moved(self, ctx: Context, system: System) -> ModuleResult | None:
        changed = self._apply(path=ctx.path, args=ctx.args)
        return ModuleResult(context=ctx, changed=changed) if changed else None
