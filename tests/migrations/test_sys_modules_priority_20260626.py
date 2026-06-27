from __future__ import annotations

from pathlib import Path

from demon_lucy.migrations.sys_modules_priority_20260626 import (
    SysModulesPriority20260626,
)
from demon_lucy.runtime import run_config_migrations


def test_sys_modules_priority_migration_rewrites_old_config_flag(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.txt"
    config_path.write_text(
        "--modules-priority banner=5 renamer=20\n",
        encoding="utf-8",
    )

    results = run_config_migrations(str(config_path))

    assert [result.get_migration_name() for result in results] == [
        "sys_modules_priority_20260626"
    ]
    assert config_path.read_text(encoding="utf-8") == (
        "--sys-modules-priority banner=5 renamer=20\n"
    )
    backup_path = tmp_path / "config.txt.bak-sys_modules_priority_20260626"
    assert backup_path.read_text(encoding="utf-8") == (
        "--modules-priority banner=5 renamer=20\n"
    )


def test_sys_modules_priority_migration_rewrites_inline_old_config_flag(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.txt"
    config_path.write_text(
        "--modules-priority=banner=5 renamer=20\n",
        encoding="utf-8",
    )

    results = run_config_migrations(str(config_path))

    assert [result.get_migration_name() for result in results] == [
        "sys_modules_priority_20260626"
    ]
    assert config_path.read_text(encoding="utf-8") == (
        "--sys-modules-priority banner=5 renamer=20\n"
    )


def test_sys_modules_priority_migration_is_idempotent(tmp_path: Path) -> None:
    config_path = tmp_path / "config.txt"
    config_path.write_text(
        "--sys-modules-priority banner=5\n",
        encoding="utf-8",
    )

    results = run_config_migrations(str(config_path))

    assert results == []
    assert config_path.read_text(encoding="utf-8") == (
        "--sys-modules-priority banner=5\n"
    )
    assert not (tmp_path / "config.txt.bak-sys_modules_priority_20260626").exists()


def test_sys_modules_priority_migration_reports_needed_only_before_migrate(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.txt"
    config_path.write_text("--modules-priority banner=5\n", encoding="utf-8")
    migration = SysModulesPriority20260626(str(config_path))

    assert migration.is_migration_needed() is True
    migration.migrate()

    assert migration.is_migration_needed() is False
    assert migration.backup_path is not None
    assert migration.backup_path.endswith(".bak-sys_modules_priority_20260626")
