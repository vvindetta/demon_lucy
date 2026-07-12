from __future__ import annotations

from collections.abc import Iterable

from demon_lucy.lib.args.parser import Template, is_valid_flag_token


def template_flag_names(template: Template) -> tuple[str, ...]:
    return tuple(item.name for item in template)


def complete_flag_token(token: str, flag_names: Iterable[str]) -> str:
    if not is_valid_flag_token(token):
        return token
    matches = [flag for flag in flag_names if flag.startswith(token)]
    if len(matches) != 1:
        return token
    return matches[0]


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


def complete_flag_prefixes_in_line(
    line: str,
    *,
    template: Template,
) -> str:
    flag_spans = _flag_token_spans(line)
    if not flag_spans:
        return line

    first_nonspace = len(line) - len(line.lstrip())
    if flag_spans[0][0] != first_nonspace:
        return line

    flag_names = template_flag_names(template)
    completed = line
    for start, end in reversed(flag_spans):
        token = line[start:end]
        completed_token = complete_flag_token(token, flag_names)
        if completed_token == token:
            continue
        completed = completed[:start] + completed_token + completed[end:]
    return completed
