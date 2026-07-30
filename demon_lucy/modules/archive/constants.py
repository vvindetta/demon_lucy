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
        description="Force archive through the configured --archive-auto-pair rule. "
        "Optional value: text or file.",
    ),
    KnownArg(
        name="archive-local",
        value_type=str,
        default=[],
        description="Force archive the current file beside itself. Optional value: text or file.",
    ),
    KnownArg(
        name="archive-global",
        value_type=str,
        default=[],
        description="Force archive the current file into the global archive destination. "
        "Optional value: text or file.",
    ),
    KnownArg(
        name="archive-auto-pair",
        value_type=str,
        default=[],
        description="Automatic pair archive rule: <src> <dest> [idle_hours] [text|file]. "
        "In text mode dest is an archive file; in file mode dest is a directory.",
    ),
    KnownArg(
        name="archive-auto-local",
        value_type=str,
        default=[],
        description="Automatic local archive rule: <src> [idle_hours] [text|file]. "
        "Text mode appends beside the source; file mode writes into .archive/.",
    ),
    KnownArg(
        name="archive-auto-global",
        value_type=str,
        default=[],
        description="Automatic global archive rule: <src> [idle_hours] [text|file]. "
        "Uses --archive-global-dest-path, or the Git repo root fallback.",
    ),
    KnownArg(
        name="archive-default-mode",
        value_type=ArchiveOutputMode,
        default=ArchiveOutputMode.TEXT,
        description="Default archive output mode for rules without explicit mode: text or file.",
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
        description="Archive source file when its last modification age is >= this many hours. Default: 12",
    ),
    KnownArg(
        name="archive-date-prefix",
        value_type=str,
        default="--- ",
        description="Text inserted before archive date in text-mode history header. The date "
        "uses the source file's latest Git commit when available, otherwise "
        "today's date. Default: '--- '.",
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
