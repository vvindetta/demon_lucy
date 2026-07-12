from __future__ import annotations

from demon_lucy.lib.args.parser import ArgTemplate, Template

KDECONNECT_SYNC_TEMPLATE: Template = [
    ArgTemplate(
        name="--kdeconnect-sync",
        value_type=bool,
        default=False,
        description="Enable KDE Connect patch sync for edit events.",
        required=False,
    ),
    ArgTemplate(
        name="--kdeconnect-device-id",
        value_type=str,
        default="",
        description="KDE Connect device id used for mount/sync operations.",
        required=False,
    ),
    ArgTemplate(
        name="--kdeconnect-remote-root",
        value_type=str,
        default="",
        description="Phone repository root path (for example /storage/emulated/0/Notes).",
        required=False,
    ),
    ArgTemplate(
        name="--kdeconnect-patch-queue-dir",
        value_type=str,
        default=".demon_lucy/patch_queue",
        description="Project-local patch queue directory.",
        required=False,
    ),
    ArgTemplate(
        name="--kdeconnect-patch-coalesce-milliseconds",
        value_type=int,
        default=250,
        description="Coalesce window for rapid edit events before building one patch packet.",
        required=False,
    ),
    ArgTemplate(
        name="--kdeconnect-patch-retry-seconds",
        value_type=float,
        default=5.0,
        description="Retry delay for failed phone transfer attempts.",
        required=False,
    ),
    ArgTemplate(
        name="--kdeconnect-patch-max-retries",
        value_type=int,
        default=3,
        description="Maximum transfer retries per packet before giving up.",
        required=False,
    ),
    ArgTemplate(
        name="--kdeconnect-binary-fallback-enabled",
        value_type=bool,
        default=False,
        description="Reserved flag for future binary fallback mode.",
        required=False,
    ),
    ArgTemplate(
        name="--kdeconnect-command-timeout-seconds",
        value_type=float,
        default=10.0,
        description="Timeout for kdeconnect-cli commands.",
        required=False,
    ),
    ArgTemplate(
        name="--kdeconnect-mount-retry-seconds",
        value_type=float,
        default=1.5,
        description="Delay between mount retries when device is temporarily unavailable.",
        required=False,
    ),
    ArgTemplate(
        name="--kdeconnect-dry-run",
        value_type=bool,
        default=False,
        description="Build patch packets without transferring to the phone.",
        required=False,
    ),
]
