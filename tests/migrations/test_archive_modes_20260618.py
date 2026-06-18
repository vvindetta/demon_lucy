from __future__ import annotations

from pathlib import Path

from demon_lucy.migrations.archive_modes_20260618 import ArchiveModes20260618
from demon_lucy.runtime import run_config_migrations


def test_archive_modes_migration_rewrites_old_config_flags(tmp_path: Path) -> None:
    config_path = tmp_path / "config.txt"
    config_path.write_text(
        "--archive-pair now.md past.md 6\n"
        "--archive-default-dest-path global.md\n"
        "--archive\n",
        encoding="utf-8",
    )

    results = run_config_migrations(str(config_path))

    assert [result.get_migration_name() for result in results] == [
        "archive_modes_20260618"
    ]
    assert config_path.read_text(encoding="utf-8") == (
        "--archive-auto-pair now.md past.md 6 text\n"
        "--archive-global-dest-path global.md\n"
        "--archive-local text\n"
    )
    backup_path = tmp_path / "config.txt.bak-archive_modes_20260618"
    assert backup_path.read_text(encoding="utf-8") == (
        "--archive-pair now.md past.md 6\n"
        "--archive-default-dest-path global.md\n"
        "--archive\n"
    )


def test_archive_modes_migration_is_idempotent(tmp_path: Path) -> None:
    config_path = tmp_path / "config.txt"
    config_path.write_text(
        "--archive-auto-pair now.md past.md 6 text\n",
        encoding="utf-8",
    )

    results = run_config_migrations(str(config_path))

    assert results == []
    assert config_path.read_text(encoding="utf-8") == (
        "--archive-auto-pair now.md past.md 6 text\n"
    )
    assert not (tmp_path / "config.txt.bak-archive_modes_20260618").exists()


def test_archive_modes_migration_reports_needed_only_before_migrate(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.txt"
    config_path.write_text("--archive\n", encoding="utf-8")
    migration = ArchiveModes20260618(str(config_path))

    assert migration.is_migration_needed() is True
    migration.migrate()

    assert migration.is_migration_needed() is False
    assert migration.backup_path is not None
    assert migration.backup_path.endswith(".bak-archive_modes_20260618")
