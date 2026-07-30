from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class TransferResult:
    status: str
    remote_incoming_dir: str = ""
    error_text: str = ""


def _run_command(
    command: list[str], *, timeout_seconds: float
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout_seconds,
    )


def _mounted_remote_root(mount_point: str, remote_root: str) -> str:
    clean_mount = os.path.normpath(mount_point)
    remote_rel = remote_root.lstrip(os.sep)
    return os.path.normpath(os.path.join(clean_mount, remote_rel))


def _copy_atomic(src_path: str, dest_path: str) -> None:
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    tmp_path = dest_path + ".tmp"
    shutil.copy2(src_path, tmp_path)
    with open(tmp_path, "rb") as handle:
        os.fsync(handle.fileno())
    os.replace(tmp_path, dest_path)


def transfer_packet_to_phone(
    *,
    device_id: str,
    remote_root: str,
    queue_dir_name: str,
    packet_paths: Sequence[str],
    timeout_seconds: float,
    mount_retry_seconds: float,
    max_retries: int,
) -> TransferResult:
    if not device_id.strip():
        return TransferResult(status="error", error_text="empty kdeconnect device id")
    if not remote_root.strip():
        return TransferResult(status="error", error_text="empty kdeconnect remote root")

    retries_left = max(1, max_retries)
    last_error = "kdeconnect transfer failed"
    while retries_left > 0:
        retries_left -= 1
        try:
            mount_result = _run_command(
                ["kdeconnect-cli", "-d", device_id, "--mount"],
                timeout_seconds=timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_error = str(exc)
            if retries_left > 0:
                _sleep_seconds(mount_retry_seconds)
            continue

        if mount_result.returncode != 0:
            last_error = (
                mount_result.stderr or mount_result.stdout or "kdeconnect mount failed"
            ).strip()
            if retries_left > 0:
                _sleep_seconds(mount_retry_seconds)
            continue

        try:
            mount_point_result = _run_command(
                ["kdeconnect-cli", "-d", device_id, "--get-mount-point"],
                timeout_seconds=timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_error = str(exc)
            if retries_left > 0:
                _sleep_seconds(mount_retry_seconds)
            continue

        mount_point = (mount_point_result.stdout or "").strip()
        if mount_point_result.returncode != 0 or not mount_point:
            last_error = (
                mount_point_result.stderr
                or mount_point_result.stdout
                or "kdeconnect mount point is empty"
            ).strip()
            if retries_left > 0:
                _sleep_seconds(mount_retry_seconds)
            continue

        remote_repo_root = _mounted_remote_root(
            mount_point=mount_point, remote_root=remote_root
        )
        remote_incoming_dir = os.path.join(
            remote_repo_root,
            queue_dir_name,
            "incoming_pc_to_phone",
        )
        try:
            os.makedirs(remote_incoming_dir, exist_ok=True)
            for src_path in packet_paths:
                file_name = os.path.basename(src_path)
                dest_path = os.path.join(remote_incoming_dir, file_name)
                _copy_atomic(src_path=src_path, dest_path=dest_path)
        except OSError as exc:
            last_error = str(exc)
            if retries_left > 0:
                _sleep_seconds(mount_retry_seconds)
            continue

        return TransferResult(status="sent", remote_incoming_dir=remote_incoming_dir)

    return TransferResult(status="error", error_text=last_error)


def _sleep_seconds(value: float) -> None:
    if value <= 0:
        return
    import time

    time.sleep(value)
