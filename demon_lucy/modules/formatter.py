from __future__ import annotations

import os
import re
from collections.abc import Iterable
from typing import Optional

from demon_lucy.lib.args.line_edit import delete_args_from_string
from demon_lucy.lib.args.parser import (
    ArgTemplate,
    Template,
    is_valid_flag_token,
    parse_args,
    split_arg_line,
)
from demon_lucy.lib.date_sections import complete_partial_date_section_headers
from demon_lucy.lib.text_file import detect_newline
from demon_lucy.lib.dynamic_blocks.parser import parse_dynamic_blocks
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)


def _complete_flag_token(token: str, flag_names: Iterable[str]) -> str:
    if not is_valid_flag_token(token):
        return token

    matches = [flag for flag in flag_names if flag.startswith(token)]
    if not matches:
        return token
    return os.path.commonprefix(matches)


def _flag_token_spans(line: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    in_single_quote = False
    in_double_quote = False
    index = 0
    while index < len(line):
        char = line[index]
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            index += 1
            continue
        if char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            index += 1
            continue
        if in_single_quote or in_double_quote:
            index += 1
            continue
        if (
            char == "-"
            and index + 2 < len(line)
            and line[index + 1] == "-"
            and line[index + 2].isalpha()
        ):
            end = index + 3
            while end < len(line):
                next_char = line[end]
                if not (next_char.isalnum() or next_char in {"_", "-"}):
                    break
                end += 1
            spans.append((index, end))
            index = end
            continue
        index += 1
    return spans


def _complete_flag_prefixes_in_line(line: str, *, template: Template) -> str:
    flag_spans = _flag_token_spans(line)
    if not flag_spans:
        return line

    first_nonspace = len(line) - len(line.lstrip())
    if flag_spans[0][0] != first_nonspace:
        return line

    flag_names = tuple(item.name for item in template)
    completed = line
    for start, end in reversed(flag_spans):
        token = line[start:end]
        completed_token = _complete_flag_token(token, flag_names)
        if completed_token != token:
            completed = completed[:start] + completed_token + completed[end:]
    return completed


class Formatter(AbstractModule):
    name: str = "formatter"
    priority: int = 23
    blank_lines_count: int = 30
    _todo_pattern = re.compile(r"^(\s*)-\s+(?!\[[ xX]\])(.+)$")

    template: Template = [
        ArgTemplate(
            name="--formatter-todo",
            value_type=bool,
            default=False,
            description="Enable TODO formatting: converts list items like '- task' into unchecked checkboxes '- [ ] task' in the current file.",
            required=False,
        ),
        ArgTemplate(
            name="--formatter-blank",
            value_type=str,
            default=[],
            description="Add blank lines at file top and/or bottom. Values: up, down, both, and optional int count. Example: --formatter-blank both 20",
            required=False,
        ),
        ArgTemplate(
            name="--formatter-date",
            value_type=bool,
            default=False,
            description="Complete consecutive archive date headers written as '--- day' from the previous full date.",
            required=False,
        ),
        ArgTemplate(
            name="--formatter-complete-args",
            value_type=bool,
            default=False,
            description="Complete Demon Lucy arguments to their longest shared prefix.",
            required=False,
        ),
    ]

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

    @staticmethod
    def _line_has_demon_lucy_flag(
        line: str,
        global_template: Template | None,
    ) -> bool:
        if not global_template:
            return False

        stripped = line.strip()
        if not stripped:
            return False

        start = stripped.split()[0]
        if not is_valid_flag_token(start):
            return False

        try:
            tokens = split_arg_line(stripped)
        except ValueError:
            return False

        known_args, _unknown = parse_args(
            args=tokens,
            template=global_template,
            include_defaults=False,
        )
        return bool(known_args)

    def _first_line_has_demon_lucy_flags(
        self,
        *,
        lines: list[str],
        global_template: Template | None,
        fallback_arg_lines: dict,
    ) -> bool:
        if global_template:
            return bool(lines) and self._line_has_demon_lucy_flag(
                lines[0],
                global_template,
            )

        return self._arg_lines_has_first_line_flag(fallback_arg_lines)

    @staticmethod
    def _formatter_flags_by_line(arg_lines: dict) -> dict[int, set[str]]:
        flags_by_line: dict[int, set[str]] = {}
        for key, flag in (
            ("formatter_todo", "--formatter-todo"),
            ("formatter_blank", "--formatter-blank"),
            ("formatter_date", "--formatter-date"),
            ("formatter_complete_args", "--formatter-complete-args"),
        ):
            for raw_line in arg_lines.get(key) or []:
                try:
                    line_number = int(raw_line)
                except (TypeError, ValueError):
                    continue
                flags_by_line.setdefault(line_number, set()).add(flag)
        return flags_by_line

    def _remove_formatter_flags(
        self,
        lines: list[str],
        arg_lines: dict,
    ) -> tuple[list[str], bool]:
        flags_by_line = self._formatter_flags_by_line(arg_lines)
        if not flags_by_line:
            return lines, False

        changed = False
        result: list[str] = []
        for line_number, line in enumerate(lines, start=1):
            flags = flags_by_line.get(line_number)
            if not flags:
                result.append(line)
                continue

            try:
                cleaned = delete_args_from_string(line, sorted(flags))
            except ValueError:
                result.append(line)
                continue

            if cleaned == line:
                result.append(line)
                continue

            changed = True
            if cleaned.strip():
                result.append(cleaned)

        return result, changed

    @staticmethod
    def _normalize_blank_modes_and_count(
        raw_modes: object,
        default_count: int,
    ) -> tuple[set[str], int]:
        if not isinstance(raw_modes, list):
            return set(), default_count

        modes: set[str] = set()
        count = default_count

        for item in raw_modes:
            text = str(item).strip().lower()
            if text in {"both", "up/down", "down/up"}:
                modes.update({"up", "down"})
                continue

            if text in {"up", "down"}:
                modes.add(text)
                continue

            if text.isdigit():
                parsed = int(text)
                if parsed >= 0:
                    count = parsed

        return modes, count

    @staticmethod
    def _collect_blank_tokens(config: dict) -> list[str]:
        tokens: list[str] = []

        blank_values = config.get("formatter_blank")
        if isinstance(blank_values, list):
            tokens.extend(str(item) for item in blank_values)

        return tokens

    def _blank_config(self, config: dict) -> tuple[set[str], int]:
        return self._normalize_blank_modes_and_count(
            self._collect_blank_tokens(config),
            default_count=self.blank_lines_count,
        )

    def _format_todo_lines(
        self,
        lines: list[str],
        *,
        protected_lines: set[int],
    ) -> tuple[list[str], bool]:
        changed = False
        new_lines: list[str] = []

        for line_number, original_line in enumerate(lines, start=1):
            if line_number in protected_lines:
                new_lines.append(original_line)
                continue

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

    @staticmethod
    def _complete_arg_lines(
        lines: list[str],
        *,
        protected_lines: set[int],
        global_template: Template | None,
    ) -> tuple[list[str], bool]:
        if not global_template:
            return lines, False

        changed = False
        completed_lines: list[str] = []
        for line_number, line in enumerate(lines, start=1):
            if line_number in protected_lines:
                completed_lines.append(line)
                continue

            completed = _complete_flag_prefixes_in_line(
                line,
                template=global_template,
            )
            completed_lines.append(completed)
            changed = changed or completed != line
        return completed_lines, changed

    def _apply(
        self,
        *,
        path: str,
        config: dict,
        arg_lines: dict,
        global_template: Template | None = None,
    ) -> Optional[IgnoreMap]:
        use_formatter_todo = bool(config.get("formatter_todo"))
        use_formatter_date = bool(config.get("formatter_date"))
        use_formatter_complete_args = bool(config.get("formatter_complete_args"))
        blank_modes, blank_lines_count = self._blank_config(config)
        use_down = "down" in blank_modes
        use_up = "up" in blank_modes
        if (
            not use_formatter_todo
            and not use_formatter_date
            and not use_formatter_complete_args
            and not use_down
            and not use_up
        ):
            return None

        if not os.path.isfile(path):
            return None

        try:
            with open(path, "r", encoding="utf-8", newline="") as file_handle:
                original_text = file_handle.read()
        except (OSError, UnicodeDecodeError):
            return None

        lines = original_text.splitlines(keepends=True)
        if not lines:
            return None

        new_lines = lines[:]
        changed = False

        new_lines, flags_removed = self._remove_formatter_flags(new_lines, arg_lines)
        changed = changed or flags_removed

        try:
            dynamic_blocks = parse_dynamic_blocks("".join(new_lines))
        except ValueError:
            protected_lines: set[int] | None = None
        else:
            protected_lines = {
                line_number
                for block in dynamic_blocks
                for line_number in range(block.line, block.end_line + 1)
            }

        if use_formatter_complete_args:
            if protected_lines is not None:
                new_lines, complete_args_changed = self._complete_arg_lines(
                    new_lines,
                    protected_lines=protected_lines,
                    global_template=global_template,
                )
                changed = changed or complete_args_changed

        if use_formatter_todo:
            if protected_lines is not None:
                new_lines, todo_changed = self._format_todo_lines(
                    new_lines,
                    protected_lines=protected_lines,
                )
                changed = changed or todo_changed

        if use_formatter_date:
            if protected_lines is not None:
                new_lines, date_changed = complete_partial_date_section_headers(
                    new_lines,
                    protected_line_numbers=protected_lines,
                )
                changed = changed or date_changed

        if use_up or use_down:
            non_empty_indexes = [
                idx for idx, line in enumerate(new_lines) if self._has_text(line)
            ]
            if not non_empty_indexes:
                if changed:
                    try:
                        with open(
                            path,
                            "w",
                            encoding="utf-8",
                            newline="",
                        ) as file_handle:
                            file_handle.write("".join(new_lines))
                    except OSError:
                        return None
                    return {os.path.abspath(path): 1}
                return None

        if use_up:
            newline = detect_newline(original_text)
            if self._first_line_has_demon_lucy_flags(
                lines=new_lines,
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

                new_lines = [head] + ([newline] * blank_lines_count) + tail
            else:
                first_non_empty = next(
                    (idx for idx, line in enumerate(new_lines) if self._has_text(line)),
                    None,
                )
                if first_non_empty is None:
                    return None
                new_lines = ([newline] * blank_lines_count) + new_lines[
                    first_non_empty:
                ]
            changed = changed or (new_lines != lines)

        if use_down:
            newline = detect_newline(original_text)
            last_non_empty = max(
                idx for idx, line in enumerate(new_lines) if self._has_text(line)
            )
            head = new_lines[: last_non_empty + 1]
            if not head[-1].endswith(("\n", "\r")):
                head[-1] = head[-1] + newline
            new_lines = head + ([newline] * blank_lines_count)
            changed = changed or (new_lines != lines)

        if not changed:
            return None

        new_text = "".join(new_lines)

        try:
            with open(path, "w", encoding="utf-8", newline="") as file_handle:
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
