from __future__ import annotations

from demon_lucy.lib.args.parser import Template

KDECONNECT_SYNC_TEMPLATE: Template = [
    (
        "--kdeconnect-sync",
        bool,
        False,
        "Enable KDE Connect patch sync for edit events.",
        False,
    ),
    (
        "--kdeconnect-device-id",
        str,
        "",
        "KDE Connect device id used for mount/sync operations.",
        False,
    ),
    (
        "--kdeconnect-remote-root",
        str,
        "",
        "Phone repository root path (for example /storage/emulated/0/Notes).",
        False,
    ),
    (
        "--kdeconnect-patch-queue-dir",
        str,
        ".demon_lucy/patch_queue",
        "Project-local patch queue directory.",
        False,
    ),
    (
        "--kdeconnect-patch-coalesce-milliseconds",
        int,
        250,
        "Coalesce window for rapid edit events before building one patch packet.",
        False,
    ),
    (
        "--kdeconnect-patch-retry-seconds",
        float,
        5.0,
        "Retry delay for failed phone transfer attempts.",
        False,
    ),
    (
        "--kdeconnect-patch-max-retries",
        int,
        3,
        "Maximum transfer retries per packet before giving up.",
        False,
    ),
    (
        "--kdeconnect-binary-fallback-enabled",
        bool,
        False,
        "Reserved flag for future binary fallback mode.",
        False,
    ),
    (
        "--kdeconnect-command-timeout-seconds",
        float,
        10.0,
        "Timeout for kdeconnect-cli commands.",
        False,
    ),
    (
        "--kdeconnect-mount-retry-seconds",
        float,
        1.5,
        "Delay between mount retries when device is temporarily unavailable.",
        False,
    ),
    (
        "--kdeconnect-dry-run",
        bool,
        False,
        "Build patch packets without transferring to the phone.",
        False,
    ),
]
