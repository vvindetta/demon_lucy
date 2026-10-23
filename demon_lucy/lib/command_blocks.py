from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass

from demon_lucy.lib.args.aliases import CommandAliasRule, flag_head, parse_command_rule
from demon_lucy.lib.args.models import Template
from demon_lucy.lib.args.parser import is_valid_flag_token, parse_args, split_arg_line
from demon_lucy.lib.dynamic_blocks.parser import dynamic_block_marker


@dataclass(frozen=True)
class CommandBlock:
    """Zero-based line slices: header at start, body [start + 1:body_end]."""

    start: int
    body_end: int
    end: int


def literal_line_numbers(lines: list[str]) -> set[int]:
    """Protect fenced/indented code and generated blocks (one-based lines)."""
    protected: set[int] = set()
    dynamic_arg: str | None = None
    fence_character = ""
    fence_length = 0
    for number, line in enumerate(lines, start=1):
        marker = dynamic_block_marker(line)
        if dynamic_arg is not None:
            protected.add(number)
            if marker == (dynamic_arg, "end"):
                dynamic_arg = None
            continue
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        if fence_character:
            protected.add(number)
            if (
                fence
                and fence[1][0] == fence_character
                and len(fence[1]) >= fence_length
                and not fence[2].strip()
            ):
                fence_character = ""
        elif fence:
            protected.add(number)
            fence_character = fence[1][0]
            fence_length = len(fence[1])
        elif marker is not None:
            protected.add(number)
            if marker[1] == "begin":
                dynamic_arg = marker[0]
        elif line.startswith(("    ", "\t")):
            protected.add(number)
    return protected


def starts_command_block(line: str, opening_flag: str) -> bool:
    stripped = line.strip()
    return (
        stripped == opening_flag
        or stripped.startswith(opening_flag + "=")
        or bool(re.match(re.escape(opening_flag) + r"\s", stripped))
    )


def find_command_blocks(
    lines: list[str],
    *,
    opening_flag: str,
    closing_flag: str = "--end",
    aliases: Collection[str] = (),
) -> list[CommandBlock]:
    protected = literal_line_numbers(lines)
    blocks: list[CommandBlock] = []
    start: int | None = None
    for index, line in enumerate(lines):
        if index + 1 in protected:
            continue
        if start is not None:
            if line.strip() == closing_flag:
                blocks.append(CommandBlock(start, index, index + 1))
                start = None
        elif starts_command_block(line, opening_flag) or any(
            starts_command_block(line.lower(), alias) for alias in aliases
        ):
            start = index
    if start is not None:
        blocks.append(CommandBlock(start, len(lines), len(lines)))
    return blocks


def command_block_aliases(
    lines: list[str],
    *,
    opening_flag: str,
    template: Template,
    configured_rules: Collection[str] = (),
) -> set[str]:
    """Recognize possible block aliases before interpreting any payload flags.

    Collect potential openers conservatively: a rule inside a body must not
    override an earlier opener and expose that body's flags to the source note.
    Normal alias validation/precedence still controls the actual expansion.
    """
    schema = next((item for item in template if item.name == "alias-command"), None)
    if schema is None:
        return set()
    rules = list(configured_rules)
    protected = literal_line_numbers(lines)
    protected.update(
        number
        for block in find_command_blocks(lines, opening_flag=opening_flag)
        for number in range(block.start + 2, block.end + 1)
    )
    for number, line in enumerate(lines, start=1):
        if number in protected or "--alias-command" not in line:
            continue
        try:
            tokens = split_arg_line(line.strip())
        except ValueError:
            continue
        if not tokens or not is_valid_flag_token(tokens[0]):
            continue
        argument = parse_args(
            template=[schema], args=tokens, include_defaults=False
        ).find("alias-command")
        if argument is not None:
            rules.extend(argument.value)
    known_flags = {f"--{item.name}" for item in template}
    aliases: set[str] = set()
    for raw in rules:
        rule = parse_command_rule(raw, known_flag_values=known_flags)
        if (
            isinstance(rule, CommandAliasRule)
            and flag_head(rule.expansion_tokens[0]) == opening_flag
        ):
            aliases.add(rule.alias_flag)
    return aliases
