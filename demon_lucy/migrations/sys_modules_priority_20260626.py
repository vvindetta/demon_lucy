from __future__ import annotations

from typing import Optional, Tuple

from demon_lucy.lib.args.line_edit import ArgSegment
from demon_lucy.migrations import Migration

_OLD_FLAG = "--modules-priority"
_NEW_FLAG = "--sys-modules-priority"


def _migrate_modules_priority_segment(
    segment: ArgSegment,
) -> Tuple[Optional[ArgSegment], bool]:
    if segment.flag == _OLD_FLAG:
        return segment.with_flag(_NEW_FLAG), True

    inline_prefix = _OLD_FLAG + "="
    if segment.flag.startswith(inline_prefix):
        inline_value = segment.flag[len(inline_prefix) :]
        values = (inline_value, *segment.values) if inline_value else segment.values
        return ArgSegment(_NEW_FLAG, values), True

    return segment, False


class SysModulesPriority20260626(Migration):
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.backup_path: str | None = None

    def get_migration_name(self) -> str:
        return "sys_modules_priority_20260626"

    def get_migration_description(self) -> str:
        return "Renames --modules-priority to --sys-modules-priority."

    def is_migration_needed(self) -> bool:
        return self.is_arg_config_file_migration_needed(
            self.config_path,
            migrate_segment=_migrate_modules_priority_segment,
            candidate_flags={_OLD_FLAG},
        )

    def migrate(self) -> None:
        self.backup_path = self.migrate_arg_config_file(
            self.config_path,
            migration_name=self.get_migration_name(),
            migrate_segment=_migrate_modules_priority_segment,
            candidate_flags={_OLD_FLAG},
        )
