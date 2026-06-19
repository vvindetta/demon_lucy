from __future__ import annotations

from demon_lucy.lib.args.parser import Template

TEXT_MODE = "text"
FILE_MODE = "file"
OUTPUT_MODES = {TEXT_MODE, FILE_MODE}

ARCHIVE_TEMPLATE: Template = [
    (
        "--archive-pair",
        str,
        [],
        "Force archive through the configured --archive-auto-pair rule. "
        "Optional value: text or file.",
        False,
    ),
    (
        "--archive-local",
        str,
        [],
        "Force archive the current file beside itself. Optional value: text or file.",
        False,
    ),
    (
        "--archive-global",
        str,
        [],
        "Force archive the current file into the global archive destination. "
        "Optional value: text or file.",
        False,
    ),
    (
        "--archive-auto-pair",
        str,
        [],
        "Automatic pair archive rule: <src> <dest> [idle_hours] [text|file]. "
        "In text mode dest is an archive file; in file mode dest is a directory.",
        False,
    ),
    (
        "--archive-auto-local",
        str,
        [],
        "Automatic local archive rule: <src> [idle_hours] [text|file]. "
        "Text mode appends beside the source; file mode writes into .archive/.",
        False,
    ),
    (
        "--archive-auto-global",
        str,
        [],
        "Automatic global archive rule: <src> [idle_hours] [text|file]. "
        "Uses --archive-global-dest-path, or the Git repo root fallback.",
        False,
    ),
    (
        "--archive-default-mode",
        str,
        TEXT_MODE,
        "Default archive output mode for rules without explicit mode: text or file.",
        False,
    ),
    (
        "--archive-global-dest-path",
        str,
        "",
        "Global archive destination. In text mode this is a file path; in file "
        "mode this is a directory path. If empty, text mode uses archive.md at "
        "the Git repo root, and file mode uses .archive/ at the Git repo root.",
        False,
    ),
    (
        "--archive-idle-hours",
        float,
        12.0,
        "Archive source file when its last modification age is >= this many hours. Default: 12",
        False,
    ),
    (
        "--archive-date-prefix",
        str,
        "-- ",
        "Text inserted before archive date in text-mode history header. The date "
        "uses the source file's latest Git commit when available, otherwise "
        "today's date. Default: '-- '.",
        False,
    ),
    (
        "--archive-date-suffix",
        str,
        "",
        "Text appended right after archive date in text-mode history header.",
        False,
    ),
    (
        "--archive-force-filesystem-mtime",
        bool,
        False,
        "Force OS filesystem mtime checks even inside Git repositories.",
        False,
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
