from __future__ import annotations

from datetime import date

import pyfiglet

from demon_lucy.lib.args.line_edit import delete_args_from_string
from demon_lucy.lib.args.models import KnownArg, ParsedArgs, Template
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
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
            description="Insert an ASCII banner (pyfiglet) at the line where the flag appears. "
            "Use '--banner date' to insert today's date. Example: --banner LOL, --banner hello world, or --banner date.",
        ),
        KnownArg(
            name="banner-separator",
            value_type=str,
            default="---",
            description="Separator line inserted before the banner when the banner is placed at the top of the file. "
            "Example: --banner-separator '---' (default).",
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

    def _apply(self, *, path: str, args: ParsedArgs) -> IgnoreMap | None:
        banner = args.require("banner")
        banner_text = self._banner_text(args)
        if not banner_text:
            return None

        if not banner.lines:
            return None
        lineno_1based = banner.lines[0]

        if banner_text == "date":
            banner_text = date.today().isoformat()

        sep: str = args.require("banner-separator").value
        sep = sep.strip()
        sep_line = sep + ("\n" if not sep.endswith("\n") else "")

        with open(path, "r+", encoding="utf-8") as f:
            lines = f.readlines()
            if not lines:
                lines = ["\n"]

            idx = max(0, min(len(lines) - 1, lineno_1based - 1))

            ascii_lines = pyfiglet.figlet_format(banner_text).splitlines(
                True
            )  # keep '\n'

            while ascii_lines and ascii_lines[0].strip() == "":
                ascii_lines.pop(0)

            while ascii_lines and ascii_lines[-1].strip() == "":
                ascii_lines.pop()

            if ascii_lines and not ascii_lines[-1].endswith("\n"):
                ascii_lines[-1] += "\n"

            if not ascii_lines:
                return None

            if idx == 0:
                lines[0] = delete_args_from_string(lines[0], ["--banner"])

                if lines[0].strip():
                    lines.insert(1, "\n")
                    insert_pos = 2
                else:
                    lines[0] = "\n"
                    insert_pos = 1

                lines[insert_pos:insert_pos] = [sep_line] + ascii_lines
            else:
                cleaned = delete_args_from_string(lines[idx], ["--banner"])

                lines[idx : idx + 1] = ascii_lines

                if cleaned.strip():
                    lines[idx + len(ascii_lines) : idx + len(ascii_lines)] = [cleaned]

            f.seek(0)
            f.truncate()
            f.writelines(lines)

        return {path: 1}

    def created(self, ctx: Context, system: System) -> IgnoreMap | None:
        return self._apply(path=ctx.path, args=ctx.args)

    def modified(self, ctx: Context, system: System) -> IgnoreMap | None:
        return self._apply(path=ctx.path, args=ctx.args)

    def moved(self, ctx: Context, system: System) -> IgnoreMap | None:
        return self._apply(path=ctx.path, args=ctx.args)
