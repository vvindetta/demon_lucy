from __future__ import annotations

import errno
from dataclasses import replace
from pathlib import Path

import pytest

from demon_lucy.modules.kdeconnect_sync.git_packets import CommitPatch
from demon_lucy.modules.kdeconnect_sync.queue import publish_packet
from demon_lucy.modules.kdeconnect_sync.transport import (
    TransferStatus,
    transfer_packet_to_phone,
)


@pytest.fixture
def packet(tmp_path):
    directory = tmp_path / "packets"
    directory.mkdir()
    return publish_packet(
        str(directory),
        CommitPatch("a" * 40, "", ("note.md",), b"patch\0\xff\n", 1),
        "pc",
    )


@pytest.fixture
def mounted_phone(tmp_path, monkeypatch):
    mount = tmp_path / "mount"
    (mount / "storage/emulated/0/Notes").mkdir(parents=True)
    monkeypatch.setattr(
        "demon_lucy.modules.kdeconnect_sync.transport.os.path.ismount",
        lambda path: path == str(mount),
    )
    monkeypatch.setattr(
        "demon_lucy.modules.kdeconnect_sync.transport._run_command",
        lambda command, **_: str(mount) if command[-1] == "--get-mount-point" else "",
    )
    return mount


def test_publishes_patch_then_ready_metadata_and_repeated_transfer_is_idempotent(
    make_request,
    packet,
    mounted_phone,
    monkeypatch,
):
    import demon_lucy.modules.kdeconnect_sync.transport as transport

    original = transport.write_bytes_atomic
    written = []

    def write(path, content):
        written.append(Path(path).suffix)
        original(path, content)

    monkeypatch.setattr(transport, "write_bytes_atomic", write)
    result = transfer_packet_to_phone(settings=make_request().settings, packet=packet)
    assert result.status is TransferStatus.SENT
    assert written == [".patch", ".json"]
    incoming = Path(result.remote_incoming_dir)
    assert (incoming / Path(packet.patch_path).name).read_bytes() == b"patch\0\xff\n"
    assert not list(incoming.glob("*.tmp"))
    result = transfer_packet_to_phone(settings=make_request().settings, packet=packet)
    assert result.status is TransferStatus.SENT
    assert written == [".patch", ".json"]


def test_unmounted_path_is_not_used_as_local_destination(
    make_request, packet, mounted_phone, monkeypatch
):
    monkeypatch.setattr(
        "demon_lucy.modules.kdeconnect_sync.transport.os.path.ismount", lambda _: False
    )
    result = transfer_packet_to_phone(settings=make_request().settings, packet=packet)
    assert result.status is TransferStatus.RETRY
    assert not list(mounted_phone.rglob("*.patch"))


def test_failed_patch_copy_does_not_publish_ready_marker(
    make_request, packet, mounted_phone, monkeypatch
):
    writes = []

    def fail(path, content):
        writes.append(Path(path).suffix)
        raise OSError("device disconnected")

    monkeypatch.setattr(
        "demon_lucy.modules.kdeconnect_sync.transport.write_bytes_atomic", fail
    )
    result = transfer_packet_to_phone(settings=make_request().settings, packet=packet)
    assert result.status is TransferStatus.RETRY
    assert writes == [".patch"] * make_request().settings.attempts
    assert not list(mounted_phone.rglob("*.json"))


def test_symlink_remote_root_never_writes_outside_mount(
    make_request, packet, mounted_phone, tmp_path
):
    outside = tmp_path / "outside"
    outside.mkdir()
    (mounted_phone / "escape").symlink_to(outside, target_is_directory=True)
    result = transfer_packet_to_phone(
        settings=replace(make_request().settings, remote_root="escape"),
        packet=packet,
    )
    assert result.status is TransferStatus.ERROR
    assert list(outside.iterdir()) == []


def test_missing_remote_repo_is_not_created(make_request, packet, mounted_phone):
    result = transfer_packet_to_phone(
        settings=replace(make_request().settings, remote_root="missing"),
        packet=packet,
    )
    assert result.status is TransferStatus.ERROR
    assert not (mounted_phone / "missing").exists()


def test_existing_remote_packet_with_different_content_is_preserved(
    make_request, packet, mounted_phone
):
    incoming = (
        mounted_phone
        / "storage/emulated/0/Notes/.demon_lucy/patch_queue/incoming_pc_to_phone"
    )
    incoming.mkdir(parents=True)
    target = incoming / Path(packet.patch_path).name
    target.write_bytes(b"other content")
    result = transfer_packet_to_phone(settings=make_request().settings, packet=packet)
    assert result.status is TransferStatus.ERROR
    assert target.read_bytes() == b"other content"
    assert not list(incoming.glob("*.json"))


def test_cli_retries_are_bounded(make_request, packet, monkeypatch):
    calls = []

    def offline(command, **kwargs):
        calls.append(command)
        raise OSError("offline")

    monkeypatch.setattr(
        "demon_lucy.modules.kdeconnect_sync.transport._run_command", offline
    )
    result = transfer_packet_to_phone(settings=make_request().settings, packet=packet)
    assert result.status is TransferStatus.RETRY
    assert len(calls) == make_request().settings.attempts


def test_symlink_remote_packet_is_not_overwritten(
    make_request, packet, mounted_phone, tmp_path
):
    incoming = (
        mounted_phone
        / "storage/emulated/0/Notes/.demon_lucy/patch_queue/incoming_pc_to_phone"
    )
    incoming.mkdir(parents=True)
    outside = tmp_path / "private"
    outside.write_text("keep")
    (incoming / Path(packet.patch_path).name).symlink_to(outside)
    result = transfer_packet_to_phone(settings=make_request().settings, packet=packet)
    assert result.status is TransferStatus.ERROR
    assert outside.read_text() == "keep"


@pytest.mark.parametrize("error_code", [errno.EACCES, errno.ENOSPC, errno.EROFS])
def test_permanent_remote_write_failure_stops_without_retrying(
    make_request, packet, mounted_phone, monkeypatch, error_code
):
    writes = []

    def fail(path, content):
        writes.append(path)
        raise OSError(error_code, "cannot write to destination")

    monkeypatch.setattr(
        "demon_lucy.modules.kdeconnect_sync.transport.write_bytes_atomic", fail
    )
    result = transfer_packet_to_phone(settings=make_request().settings, packet=packet)
    assert result.status is TransferStatus.ERROR
    assert len(writes) == 1
    assert not list(mounted_phone.rglob("*.json"))


def test_missing_cli_is_an_actionable_failure(make_request, packet, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("kdeconnect-cli")

    monkeypatch.setattr(
        "demon_lucy.modules.kdeconnect_sync.transport.subprocess.run", missing
    )
    result = transfer_packet_to_phone(settings=make_request().settings, packet=packet)
    assert result.status is TransferStatus.ERROR
    assert "not installed" in result.error_text
