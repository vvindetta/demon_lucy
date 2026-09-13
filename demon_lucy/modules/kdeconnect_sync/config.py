from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import PurePosixPath

from demon_lucy.lib.args.models import KnownArg, ParsedArgs, Template

KDECONNECT_SYNC_TEMPLATE: Template = [
    KnownArg(
        name="kdeconnect-apply",
        value_type=bool,
        default=False,
        description="Apply incoming packets immediately in CLI/oneshot mode. Incoming ready-marker file events trigger receiving automatically.",
    ),
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
        description="Existing repository directory relative to KDE Connect's mounted filesystem (for example storage/emulated/0/Notes).",
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
        description="Transfer attempts per cycle; unsent packets remain queued for later cycles.",
    ),
    KnownArg(
        name="kdeconnect-command-timeout-seconds",
        value_type=float,
        default=10.0,
        description="Timeout for each KDE Connect or local Git command.",
    ),
    KnownArg(
        name="kdeconnect-mount-retry-seconds",
        value_type=float,
        default=1.5,
        description="Delay between mount retries when device is temporarily unavailable.",
    ),
    KnownArg(
        name="kdeconnect-commit-message",
        value_type=str,
        default="Lucy: snapshot for KDE Connect",
        description="Commit message for local snapshots made by KDE Connect sync.",
    ),
    KnownArg(
        name="kdeconnect-patch-retry-seconds",
        value_type=float,
        default=30.0,
        description="Daemon delay before retrying a busy repository or pending transfer.",
    ),
    KnownArg(
        name="kdeconnect-dry-run",
        value_type=bool,
        default=False,
        description="Preview sending or receiving without commits, queue writes, mounts, or note changes.",
    ),
]


@dataclass(frozen=True)
class ApplySettings:
    queue_directory: str
    timeout_seconds: float
    dry_run: bool

    @classmethod
    def from_args(cls, args: ParsedArgs) -> ApplySettings:
        timeout = args.require("kdeconnect-command-timeout-seconds").value
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError(
                "--kdeconnect-command-timeout-seconds must be positive and finite"
            )
        return cls(
            queue_directory=validate_relative_directory(
                args.require("kdeconnect-patch-queue-dir").value
            ),
            timeout_seconds=timeout,
            dry_run=args.require("kdeconnect-dry-run").value,
        )


def validate_relative_directory(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or value != value.strip()
        or path.is_absolute()
        or not path.parts
        or any(part in {"..", ".git"} for part in path.parts)
        or any(character in value for character in "\\\r\n\0")
        or ":" in path.parts[0]
    ):
        raise ValueError(f"unsafe relative directory: {value!r}")
    return path.as_posix()


@dataclass(frozen=True)
class SyncSettings:
    device_id: str
    remote_root: str
    queue_directory: str
    commit_message: str
    coalesce_seconds: float
    retry_seconds: float
    attempts: int
    timeout_seconds: float
    mount_retry_seconds: float
    dry_run: bool

    @classmethod
    def from_args(cls, args: ParsedArgs) -> SyncSettings:
        device_id = args.require("kdeconnect-device-id").value.strip()
        if not device_id or any(char in device_id for char in "\r\n\0"):
            raise ValueError("--kdeconnect-device-id must identify a paired device")
        remote_root = validate_relative_directory(
            args.require("kdeconnect-remote-root").value
        )
        queue_directory = validate_relative_directory(
            args.require("kdeconnect-patch-queue-dir").value
        )
        commit_message = args.require("kdeconnect-commit-message").value.strip()
        if not commit_message or "\0" in commit_message:
            raise ValueError("--kdeconnect-commit-message must not be empty")
        for name, minimum, inclusive in (
            ("kdeconnect-patch-coalesce-milliseconds", 0, True),
            ("kdeconnect-patch-retry-seconds", 0, False),
            ("kdeconnect-command-timeout-seconds", 0, False),
            ("kdeconnect-mount-retry-seconds", 0, True),
            ("kdeconnect-patch-max-retries", 1, True),
        ):
            value = args.require(name).value
            if (
                not math.isfinite(value)
                or value < minimum
                or (value == minimum and not inclusive)
            ):
                raise ValueError(f"invalid --{name}: {value}")
        return cls(
            device_id=device_id,
            remote_root=remote_root,
            queue_directory=queue_directory,
            commit_message=commit_message,
            coalesce_seconds=args.require(
                "kdeconnect-patch-coalesce-milliseconds"
            ).value
            / 1000,
            retry_seconds=args.require("kdeconnect-patch-retry-seconds").value,
            attempts=args.require("kdeconnect-patch-max-retries").value,
            timeout_seconds=args.require("kdeconnect-command-timeout-seconds").value,
            mount_retry_seconds=args.require("kdeconnect-mount-retry-seconds").value,
            dry_run=args.require("kdeconnect-dry-run").value,
        )
