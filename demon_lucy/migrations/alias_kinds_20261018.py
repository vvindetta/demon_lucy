from __future__ import annotations

from demon_lucy.lib.args.line_edit import (
    ArgSegment,
    render_arg_segments,
    split_arg_segments,
)
from demon_lucy.lib.args.parser import is_valid_flag_token, split_arg_line
from demon_lucy.migrations import Migration


def _migrate_alias_line(line: str) -> tuple[str, bool]:
    if line.lstrip().startswith("#") or "--alias" not in line:
        return line, False
    try:
        segments = split_arg_segments(split_arg_line(line))
    except ValueError:
        return line, False
    migrated: list[ArgSegment] = []
    changed = False
    for segment in segments:
        flag, separator, inline = segment.flag.partition("=")
        if flag != "--alias":
            migrated.append(segment)
            continue
        values = (inline, *segment.values) if separator else segment.values
        if not values:
            changed = True
            continue
        for value in values:
            name, equals, expansion = value.partition("=")
            try:
                tokens = split_arg_line(expansion)
            except ValueError:
                tokens = []
            if not equals or (tokens and is_valid_flag_token(tokens[0])):
                migrated.append(ArgSegment("--alias-command", (value,)))
            else:
                # Old named text aliases were invoked as --name. Symbol-only
                # rules retain their literal source, including surrounding spaces.
                normalized = name.strip().lstrip("-").strip().lower()
                source = "--" + normalized if normalized else name
                replacement = expansion.strip() if normalized else expansion
                migrated.append(
                    ArgSegment("--alias-text", (source + "=" + replacement,))
                )
            changed = True
    if not changed:
        return line, False
    indent = line[: len(line) - len(line.lstrip())]
    newline = line[len(line.rstrip("\r\n")) :]
    return indent + render_arg_segments(migrated) + newline, True


class AliasKinds20261018(Migration):
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.backup_path: str | None = None

    def get_migration_name(self) -> str:
        return "alias_kinds_20261018"

    def get_migration_description(self) -> str:
        return "Splits --alias rules into --alias-command and --alias-text."

    def is_migration_needed(self) -> bool:
        return self.is_config_file_migration_needed(
            self.config_path, migrate_line=_migrate_alias_line
        )

    def migrate(self) -> None:
        self.backup_path = self.migrate_config_file(
            self.config_path,
            migration_name=self.get_migration_name(),
            migrate_line=_migrate_alias_line,
        )
