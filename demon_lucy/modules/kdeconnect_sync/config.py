from __future__ import annotations

from demon_lucy.lib.args.models import KnownArg, Template

KDECONNECT_SYNC_TEMPLATE: Template = [
    KnownArg(
        name="kdeconnect-sync",
        value_type=bool,
        default=False,
        description="Enable KDE Connect patch sync for edit events.",
    ),
    KnownArg(
        name="kdeconnect-device-id",
        value_type=str,
        default="",
        description="KDE Connect device id used for mount/sync operations.",
    ),
    KnownArg(
        name="kdeconnect-remote-root",
        value_type=str,
        default="",
        description="Phone repository root path (for example /storage/emulated/0/Notes).",
    ),
    KnownArg(
        name="kdeconnect-patch-queue-dir",
        value_type=str,
        default=".demon_lucy/patch_queue",
        description="Project-local patch queue directory.",
    ),
    KnownArg(
        name="kdeconnect-patch-coalesce-milliseconds",
        value_type=int,
        default=250,
        description="Coalesce window for rapid edit events before building one patch packet.",
    ),
    KnownArg(
        name="kdeconnect-patch-max-retries",
        value_type=int,
        default=3,
        description="Maximum transfer retries per packet before giving up.",
    ),
    KnownArg(
        name="kdeconnect-command-timeout-seconds",
        value_type=float,
        default=10.0,
        description="Timeout for kdeconnect-cli commands.",
    ),
    KnownArg(
        name="kdeconnect-mount-retry-seconds",
        value_type=float,
        default=1.5,
        description="Delay between mount retries when device is temporarily unavailable.",
    ),
    KnownArg(
        name="kdeconnect-dry-run",
        value_type=bool,
        default=False,
        description="Build patch packets without transferring to the phone.",
    ),
]
