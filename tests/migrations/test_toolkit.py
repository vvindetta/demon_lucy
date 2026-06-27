from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from demon_lucy.lib.args.line_edit import (
    ArgSegment,
    migrate_arg_line_segments,
    split_arg_segments,
)
from demon_lucy.migrations import (
    MIGRATIONS,
    Migration,
)
from demon_lucy.migrations.archive_modes_20260618 import ArchiveModes20260618
from demon_lucy.migrations.sys_modules_priority_20260626 import (
    SysModulesPriority20260626,
)


def test_migration_toolkit_splits_arg_segments_with_quoted_values() -> None:
    segments = split_arg_segments(
        ["--old", "hello world", "--keep", "x", "y", "--flag"]
    )

    assert segments == [
        ArgSegment("--old", ("hello world",)),
        ArgSegment("--keep", ("x", "y")),
        ArgSegment("--flag", ()),
    ]


def test_migration_toolkit_rewrites_arg_line_segments() -> None:
    def _migrate(segment: ArgSegment) -> Tuple[Optional[ArgSegment], bool]:
        if segment.flag == "--old":
            return segment.with_flag("--new"), True
        return segment, False

    line, changed = migrate_arg_line_segments(
        '--old "hello world" --keep x\n',
        migrate_segment=_migrate,
        candidate_flags={"--old"},
    )

    assert changed is True
    assert line == "--new 'hello world' --keep x\n"


def test_migration_toolkit_rewrites_arg_config_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config.txt"
    config_path.write_text("--old value\n", encoding="utf-8")

    def _migrate(segment: ArgSegment) -> Tuple[Optional[ArgSegment], bool]:
        if segment.flag == "--old":
            return segment.with_flag("--new"), True
        return segment, False

    assert Migration.is_arg_config_file_migration_needed(
        str(config_path),
        migrate_segment=_migrate,
        candidate_flags={"--old"},
    )
    backup_path = Migration.migrate_arg_config_file(
        str(config_path),
        migration_name="arg_test_migration",
        migrate_segment=_migrate,
        candidate_flags={"--old"},
    )

    assert backup_path is not None
    assert config_path.read_text(encoding="utf-8") == "--new value\n"


def test_migration_toolkit_migrates_config_file_with_numbered_backup(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.txt"
    config_path.write_text("--old value\n", encoding="utf-8")
    first_backup_path = tmp_path / "config.txt.bak-test_migration"
    first_backup_path.write_text("previous backup\n", encoding="utf-8")

    def _migrate_line(line: str) -> Tuple[str, bool]:
        return line.replace("--old", "--new"), "--old" in line

    backup_path = Migration.migrate_config_file(
        str(config_path),
        migration_name="test_migration",
        migrate_line=_migrate_line,
    )

    assert backup_path is not None
    assert backup_path.endswith(".bak-test_migration.2")
    assert config_path.read_text(encoding="utf-8") == "--new value\n"
    assert (tmp_path / "config.txt.bak-test_migration.2").read_text(
        encoding="utf-8"
    ) == "--old value\n"


def test_migration_registry_contains_migration_classes() -> None:
    assert MIGRATIONS == (
        ArchiveModes20260618,
        SysModulesPriority20260626,
    )
    migration = MIGRATIONS[0]("config.txt")
    assert isinstance(migration, Migration)
    assert migration.get_migration_name() == "archive_modes_20260618"
    assert migration.get_migration_description()

    second_migration = MIGRATIONS[1]("config.txt")
    assert isinstance(second_migration, Migration)
    assert second_migration.get_migration_name() == "sys_modules_priority_20260626"
    assert second_migration.get_migration_description()
