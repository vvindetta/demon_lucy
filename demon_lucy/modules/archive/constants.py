from __future__ import annotations

from demon_lucy.lib.args.models import KnownArg, Template
from demon_lucy.modules.archive.types import ArchiveOutputMode

ARCHIVE_TEMPLATE: Template = [
    KnownArg(
        name="archive",
        value_type=bool,
        default=False,
        description="Force archive using the first available route: configured pair, local .archive, then global destination.",
    ),
    KnownArg(
        name="archive-pair",
        value_type=str,
        default=[],
        description="Archive now using the configured --archive-auto-pair rule. Usage: --archive-pair [text|file].",
    ),
    KnownArg(
        name="archive-local",
        value_type=str,
        default=[],
        description="Archive the current note beside itself. Usage: --archive-local [text|file].",
    ),
    KnownArg(
        name="archive-global",
        value_type=str,
        default=[],
        description="Archive the current note to the global destination. Usage: --archive-global [text|file].",
    ),
    KnownArg(
        name="archive-auto-pair",
        value_type=str,
        default=[],
        description="Automatic pair archive rule: <src> <dest> [idle_hours] [text|file]. Text mode appends to a file; file mode writes into a directory. Example: --archive-auto-pair now.md past.md 12 text.",
    ),
    KnownArg(
        name="archive-auto-local",
        value_type=str,
        default=[],
        description="Archive idle sources beside themselves. Text mode appends beside the source; file mode writes into .archive/. Usage: --archive-auto-local <src> [idle_hours] [text|file].",
    ),
    KnownArg(
        name="archive-auto-global",
        value_type=str,
        default=[],
        description="Archive idle sources using --archive-global-dest-path or the Git repository root fallback. Usage: --archive-auto-global <src> [idle_hours] [text|file].",
    ),
    KnownArg(
        name="archive-ignore-paths",
        value_type=str,
        default=[".lucy"],
        description="Exclude matching archive events, sources, and destinations. Relative paths match whole path components at any depth; absolute paths match the target and its contents. An explicit list replaces the default; an empty list disables exclusions.",
    ),
    KnownArg(
        name="archive-default-mode",
        value_type=ArchiveOutputMode,
        default=ArchiveOutputMode.TEXT,
        description="Output mode for archive rules that do not specify one. Values: text, file.",
    ),
    KnownArg(
        name="archive-global-dest-path",
        value_type=str,
        default="",
        description="Global archive destination. In text mode this is a file path; in file "
        "mode this is a directory path. If empty, text mode uses archive.md at "
        "the Git repo root, and file mode uses .archive/ at the Git repo root.",
    ),
    KnownArg(
        name="archive-idle-hours",
        value_type=float,
        default=12.0,
        description="Minimum source age in hours before automatic archiving.",
    ),
    KnownArg(
        name="archive-date-prefix",
        value_type=str,
        default="--- ",
        description="Text before the archive date header. The date comes from the source's latest Git commit when available, otherwise today.",
    ),
    KnownArg(
        name="archive-date-suffix",
        value_type=str,
        default="",
        description="Text appended right after archive date in text-mode history header.",
    ),
    KnownArg(
        name="archive-force-filesystem-mtime",
        value_type=bool,
        default=False,
        description="Force OS filesystem mtime checks even inside Git repositories.",
    ),
]

STRIP_FLAGS = [
    "--archive",
    "--archive-pair",
    "--archive-local",
    "--archive-global",
    "--archive-auto-pair",
    "--archive-auto-local",
    "--archive-auto-global",
    "--archive-default-mode",
    "--archive-global-dest-path",
    "--archive-default-dest-path",
]
