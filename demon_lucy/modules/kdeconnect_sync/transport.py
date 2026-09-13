from __future__ import annotations

import errno
import hashlib
import os
import subprocess
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from demon_lucy.lib.path import path_inside_no_symlinks
from demon_lucy.lib.text_file import write_bytes_atomic
from demon_lucy.modules.kdeconnect_sync.config import SyncSettings
from demon_lucy.modules.kdeconnect_sync.queue import Packet


class TransferStatus(StrEnum):
    SENT = "sent"
    RETRY = "retry"
    ERROR = "error"


@dataclass(frozen=True)
class TransferResult:
    status: TransferStatus
    remote_incoming_dir: str = ""
    error_text: str = ""


def _run_command(command: list[str], *, timeout_seconds: float) -> str:
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise ValueError("kdeconnect-cli is not installed") from exc
    if result.returncode:
        raise OSError(
            (result.stderr or result.stdout or "KDE Connect command failed").strip()
        )
    return result.stdout.strip()


def _publish_file(source: str, destination: str) -> None:
    content = Path(source).read_bytes()
    if Path(destination).exists():
        if Path(destination).read_bytes() != content:
            raise ValueError(
                f"remote packet already exists with different content: {destination}"
            )
        return
    write_bytes_atomic(destination, content)
    # Confirm readable remote bytes before publishing the ready marker or reporting sent.
    if (
        hashlib.sha256(Path(destination).read_bytes()).digest()
        != hashlib.sha256(content).digest()
    ):
        raise OSError(f"remote packet verification failed: {destination}")


def transfer_packet_to_phone(
    *, settings: SyncSettings, packet: Packet
) -> TransferResult:
    last_error = ""
    for attempt in range(settings.attempts):
        try:
            _run_command(
                ["kdeconnect-cli", "-d", settings.device_id, "--mount"],
                timeout_seconds=settings.timeout_seconds,
            )
            mount_point = _run_command(
                ["kdeconnect-cli", "-d", settings.device_id, "--get-mount-point"],
                timeout_seconds=settings.timeout_seconds,
            )
            # KDE's CLI can print the intended path even before sshfs is mounted.
            if not os.path.isabs(mount_point) or not os.path.ismount(mount_point):
                raise OSError("KDE Connect filesystem is not mounted yet")
            remote_root = path_inside_no_symlinks(mount_point, settings.remote_root)
            if not Path(remote_root).is_dir():
                raise ValueError(
                    f"remote repository directory does not exist: {remote_root}"
                )
            incoming = path_inside_no_symlinks(
                remote_root, f"{settings.queue_directory}/incoming_pc_to_phone"
            )
            Path(incoming).mkdir(parents=True, exist_ok=True)
            patch_destination = path_inside_no_symlinks(
                incoming, Path(packet.patch_path).name
            )
            metadata_destination = path_inside_no_symlinks(
                incoming, Path(packet.metadata_path).name
            )
            # The .json file is the ready marker; it must always be copied last.
            _publish_file(packet.patch_path, patch_destination)
            _publish_file(packet.metadata_path, metadata_destination)
            return TransferResult(TransferStatus.SENT, remote_incoming_dir=incoming)
        except ValueError as exc:
            return TransferResult(TransferStatus.ERROR, error_text=str(exc))
        except OSError as exc:
            if exc.errno in {
                errno.EACCES,
                errno.EPERM,
                errno.ENOSPC,
                errno.EDQUOT,
                errno.EROFS,
                errno.ENOTDIR,
                errno.EISDIR,
                errno.ENAMETOOLONG,
                errno.ENOTSUP,
            }:
                return TransferResult(TransferStatus.ERROR, error_text=str(exc))
            last_error = str(exc)
        except subprocess.TimeoutExpired as exc:
            last_error = str(exc)
        if attempt + 1 < settings.attempts:
            time.sleep(settings.mount_retry_seconds)
    return TransferResult(TransferStatus.RETRY, error_text=last_error)
