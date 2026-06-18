from __future__ import annotations

from typing import Optional, Tuple

from demon_lucy.lib.args.line_edit import ArgSegment
from demon_lucy.migrations import Migration

_OLD_FLAGS = {
    "--archive",
    "--archive-pair",
    "--archive-default-dest-path",
}
_OUTPUT_MODES = {"text", "file"}


def _migrate_archive_segment(segment: ArgSegment) -> Tuple[Optional[ArgSegment], bool]:
    if segment.flag == "--archive":
        return ArgSegment("--archive-local", ("text",)), True

    if segment.flag == "--archive-pair":
        values = list(segment.values)
        if not any(value.strip().lower() in _OUTPUT_MODES for value in values[2:]):
            values.append("text")
        return ArgSegment("--archive-auto-pair", tuple(values)), True

    if segment.flag == "--archive-default-dest-path":
        return segment.with_flag("--archive-global-dest-path"), True

    return segment, False


class ArchiveModes20260618(Migration):
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.backup_path: str | None = None

    def get_migration_name(self) -> str:
        return "archive_modes_20260618"

    def get_migration_description(self) -> str:
        return (
            "Renames legacy archive config flags to pair/local/global archive "
            "rules and adds explicit text mode to migrated pair rules."
        )

    def is_migration_needed(self) -> bool:
        return self.is_arg_config_file_migration_needed(
            self.config_path,
            migrate_segment=_migrate_archive_segment,
            candidate_flags=_OLD_FLAGS,
        )

    def migrate(self) -> None:
        self.backup_path = self.migrate_arg_config_file(
            self.config_path,
            migration_name=self.get_migration_name(),
            migrate_segment=_migrate_archive_segment,
            candidate_flags=_OLD_FLAGS,
        )
