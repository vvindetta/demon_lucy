from __future__ import annotations

import os
import re
from typing import Optional

from lucy_notes_manager.lib.args import Template, get_args_from_file
from lucy_notes_manager.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)


class Formatter(AbstractModule):
    name: str = "formatter"
    priority: int = 40
    blank_lines_count: int = 30
    _todo_pattern = re.compile(r"^(\s*)-\s+(?!\[[ xX]\])(.+)$")

    template: Template = [
        (
            "--formatter-todo",
            bool,
            False,
            "Enable TODO formatting: converts list items like '- task' into unchecked checkboxes '- [ ] task' in the current file.",
        ),
        (
            "--formatter-blank",
            str,
            [],
            "Add 30 blank lines at file top and/or bottom. Values: up, down. Example: --formatter-blank up down",
        ),
    ]

    def _detect_newline(self, text: str) -> str:
        for separator in ("\r\n", "\n", "\r"):
            if separator in text:
                return separator
        return "\n"

    @staticmethod
    def _has_text(line: str) -> bool:
        return bool(line.rstrip("\r\n").strip())

    @staticmethod
    def _arg_lines_has_first_line_flag(arg_lines: dict) -> bool:
        for value in (arg_lines or {}).values():
            if not isinstance(value, list):
                continue
            for lineno in value:
                try:
                    if int(lineno) == 1:
                        return True
                except (TypeError, ValueError):
                    continue
        return False

    def _first_line_has_lucy_flags_in_file(
        self, *, path: str, global_template: Template | None, fallback_arg_lines: dict
    ) -> bool:
        if global_template:
            _known, _unknown, first_line_arg_lines = get_args_from_file(
                path=path,
                template=global_template,
                only_first_line=True,
            )
            return self._arg_lines_has_first_line_flag(first_line_arg_lines)

        return self._arg_lines_has_first_line_flag(fallback_arg_lines)

    @staticmethod
    def _normalize_blank_modes(raw_modes: object) -> set[str]:
        if not isinstance(raw_modes, list):
            return set()
        modes: set[str] = set()
        for item in raw_modes:
            text = str(item).strip().lower()
            if text in {"up", "down"}:
                modes.add(text)
        return modes

    def _format_todo_lines(self, lines: list[str]) -> tuple[list[str], bool]:
        changed = False
        new_lines: list[str] = []

        for original_line in lines:
            if original_line.endswith("\r\n"):
                newline = "\r\n"
                line = original_line[:-2]
            elif original_line.endswith("\n"):
                newline = "\n"
                line = original_line[:-1]
            elif original_line.endswith("\r"):
                newline = "\r"
                line = original_line[:-1]
            else:
                newline = ""
                line = original_line

            match = self._todo_pattern.match(line)
            if not match:
                new_lines.append(original_line)
                continue

            indent, content = match.groups()
            new_line = f"{indent}- [ ] {content}{newline}"
            new_lines.append(new_line)
            if new_line != original_line:
                changed = True

        return new_lines, changed

    def _apply(
        self,
        *,
        path: str,
        config: dict,
        arg_lines: dict,
        global_template: Template | None = None,
    ) -> Optional[IgnoreMap]:
        use_formatter_todo = bool(config.get("formatter_todo"))
        blank_modes = self._normalize_blank_modes(config.get("formatter_blank"))
        use_down = "down" in blank_modes
        use_up = "up" in blank_modes
        if not use_formatter_todo and not use_down and not use_up:
            return None

        if not os.path.isfile(path):
            return None

        try:
            with open(path, "r", encoding="utf-8") as file_handle:
                original_text = file_handle.read()
        except (OSError, UnicodeDecodeError):
            return None

        lines = original_text.splitlines(keepends=True)
        if not lines:
            return None

        new_lines = lines[:]
        changed = False

        if use_formatter_todo:
            new_lines, todo_changed = self._format_todo_lines(new_lines)
            changed = changed or todo_changed

        if use_up or use_down:
            non_empty_indexes = [
                idx for idx, line in enumerate(new_lines) if self._has_text(line)
            ]
            if not non_empty_indexes:
                if changed:
                    try:
                        with open(path, "w", encoding="utf-8") as file_handle:
                            file_handle.write("".join(new_lines))
                    except OSError:
                        return None
                    return {os.path.abspath(path): 1}
                return None

        if use_up:
            newline = self._detect_newline(original_text)
            if self._first_line_has_lucy_flags_in_file(
                path=path,
                global_template=global_template,
                fallback_arg_lines=arg_lines,
            ):
                head = new_lines[0]
                if not head.endswith(("\n", "\r")):
                    head = head + newline

                tail = new_lines[1:]
                first_non_empty_tail = next(
                    (idx for idx, line in enumerate(tail) if self._has_text(line)),
                    None,
                )
                if first_non_empty_tail is None:
                    tail = []
                else:
                    tail = tail[first_non_empty_tail:]

                new_lines = [head] + ([newline] * self.blank_lines_count) + tail
            else:
                first_non_empty = next(
                    (idx for idx, line in enumerate(new_lines) if self._has_text(line)),
                    None,
                )
                if first_non_empty is None:
                    return None
                new_lines = ([newline] * self.blank_lines_count) + new_lines[first_non_empty:]
            changed = changed or (new_lines != lines)

        if use_down:
            newline = self._detect_newline(original_text)
            last_non_empty = max(idx for idx, line in enumerate(new_lines) if self._has_text(line))
            head = new_lines[: last_non_empty + 1]
            if not head[-1].endswith(("\n", "\r")):
                head[-1] = head[-1] + newline
            new_lines = head + ([newline] * self.blank_lines_count)
            changed = changed or (new_lines != lines)

        if not changed:
            return None

        new_text = "".join(new_lines)

        try:
            with open(path, "w", encoding="utf-8") as file_handle:
                file_handle.write(new_text)
        except OSError:
            return None

        return {os.path.abspath(path): 1}

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(
            path=ctx.path,
            config=ctx.config,
            arg_lines=ctx.arg_lines,
            global_template=system.global_template,
        )

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(
            path=ctx.path,
            config=ctx.config,
            arg_lines=ctx.arg_lines,
            global_template=system.global_template,
        )

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(
            path=ctx.path,
            config=ctx.config,
            arg_lines=ctx.arg_lines,
            global_template=system.global_template,
        )
