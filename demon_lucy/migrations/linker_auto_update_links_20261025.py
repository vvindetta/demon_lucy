from __future__ import annotations

from demon_lucy.lib.args.line_edit import ArgSegment
from demon_lucy.migrations import Migration

_OLD_FLAG = "--linker-auto-update-md-links"
_NEW_FLAG = "--linker-auto-update-links"


def _migrate_linker_segment(segment: ArgSegment) -> tuple[ArgSegment, bool]:
    if segment.flag == _OLD_FLAG:
        return segment.with_flag(_NEW_FLAG), True
    return segment, False


class LinkerAutoUpdateLinks20261025(Migration):
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.backup_path: str | None = None

    def get_migration_name(self) -> str:
        return "linker_auto_update_links_20261025"

    def get_migration_description(self) -> str:
        return "Renames --linker-auto-update-md-links to --linker-auto-update-links."

    def is_migration_needed(self) -> bool:
        return self.is_arg_config_file_migration_needed(
            self.config_path,
            migrate_segment=_migrate_linker_segment,
            candidate_flags={_OLD_FLAG},
        )

    def migrate(self) -> None:
        self.backup_path = self.migrate_arg_config_file(
            self.config_path,
            migration_name=self.get_migration_name(),
            migrate_segment=_migrate_linker_segment,
            candidate_flags={_OLD_FLAG},
        )
