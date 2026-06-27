from __future__ import annotations

import os
import shutil
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable

from demon_lucy.lib.args.line_edit import (
    ArgSegmentMigrator,
    migrate_arg_line_segments,
)

ConfigLineMigrator = Callable[[str], tuple[str, bool]]


class Migration(ABC):
    """
    Abstract migration contract plus shared config/arg migration helpers.

    Runtime creates each migration object, checks whether it is needed, and runs
    it only when the migration reports work to do.
    """

    @abstractmethod
    def get_migration_name(self) -> str:
        pass

    @abstractmethod
    def get_migration_description(self) -> str:
        pass

    @abstractmethod
    def is_migration_needed(self) -> bool:
        pass

    @abstractmethod
    def migrate(self) -> None:
        pass

    @staticmethod
    def backup_path_for(config_path: str, migration_name: str) -> str:
        base_path = f"{config_path}.bak-{migration_name}"
        if not os.path.exists(base_path):
            return base_path

        index = 2
        while True:
            candidate = f"{base_path}.{index}"
            if not os.path.exists(candidate):
                return candidate
            index += 1

    @staticmethod
    def read_config_lines(config_path: str) -> list[str] | None:
        if not os.path.isfile(config_path):
            return None

        try:
            with open(config_path, "r", encoding="utf-8") as config_file:
                return config_file.readlines()
        except OSError:
            return None

    @staticmethod
    def migrate_config_lines(
        lines: Iterable[str],
        *,
        migrate_line: ConfigLineMigrator,
    ) -> tuple[list[str], bool]:
        changed = False
        new_lines: list[str] = []
        for line in lines:
            migrated_line, line_changed = migrate_line(line)
            changed = changed or line_changed
            new_lines.append(migrated_line)
        return new_lines, changed

    @staticmethod
    def is_config_file_migration_needed(
        config_path: str,
        *,
        migrate_line: ConfigLineMigrator,
    ) -> bool:
        old_lines = Migration.read_config_lines(config_path)
        if old_lines is None:
            return False
        _new_lines, changed = Migration.migrate_config_lines(
            old_lines,
            migrate_line=migrate_line,
        )
        return changed

    @staticmethod
    def is_arg_config_file_migration_needed(
        config_path: str,
        *,
        migrate_segment: ArgSegmentMigrator,
        candidate_flags: Iterable[str] = (),
    ) -> bool:
        return Migration.is_config_file_migration_needed(
            config_path,
            migrate_line=lambda line: migrate_arg_line_segments(
                line,
                migrate_segment=migrate_segment,
                candidate_flags=candidate_flags,
            ),
        )

    @staticmethod
    def migrate_config_file(
        config_path: str,
        *,
        migration_name: str,
        migrate_line: ConfigLineMigrator,
    ) -> str | None:
        old_lines = Migration.read_config_lines(config_path)
        if old_lines is None:
            return None

        new_lines, changed = Migration.migrate_config_lines(
            old_lines,
            migrate_line=migrate_line,
        )
        if not changed:
            return None

        backup_path = Migration.backup_path_for(config_path, migration_name)
        tmp_path = f"{config_path}.tmp-{migration_name}"
        try:
            shutil.copy2(config_path, backup_path)
            with open(tmp_path, "w", encoding="utf-8") as tmp_file:
                tmp_file.writelines(new_lines)
            os.replace(tmp_path, config_path)
        except OSError:
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except OSError:
                pass
            return None

        return backup_path

    @staticmethod
    def migrate_arg_config_file(
        config_path: str,
        *,
        migration_name: str,
        migrate_segment: ArgSegmentMigrator,
        candidate_flags: Iterable[str] = (),
    ) -> str | None:
        return Migration.migrate_config_file(
            config_path,
            migration_name=migration_name,
            migrate_line=lambda line: migrate_arg_line_segments(
                line,
                migrate_segment=migrate_segment,
                candidate_flags=candidate_flags,
            ),
        )


ConfigMigrationFactory = Callable[[str], Migration]


# Migration modules import the toolkit above, so keep registry imports last.
from demon_lucy.migrations import (  # noqa: E402
    archive_modes_20260618,  # noqa: E402
    sys_modules_priority_20260626,  # noqa: E402
)

MIGRATIONS: tuple[ConfigMigrationFactory, ...] = (
    archive_modes_20260618.ArchiveModes20260618,
    sys_modules_priority_20260626.SysModulesPriority20260626,
)
