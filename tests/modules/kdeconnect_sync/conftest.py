from __future__ import annotations

import subprocess
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from watchdog.events import FileModifiedEvent

from demon_lucy.lib.args.parser import parse_args
from demon_lucy.lib.operating_system import OperatingSystem
from demon_lucy.modules.abstract_module import Context, System
from demon_lucy.modules.kdeconnect_sync.git_packets import GitPacketRepository
from demon_lucy.modules.kdeconnect_sync.queue import publish_packet
from demon_lucy.modules.kdeconnect_sync.receiver import apply_incoming
from demon_lucy.modules.kdeconnect_sync.config import (
    KDECONNECT_SYNC_TEMPLATE,
    SyncSettings,
)
from demon_lucy.modules.kdeconnect_sync.worker import SyncRequest
from demon_lucy.runtime import DEMON_LUCY_STARTUP_TEMPLATE


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    directory = tmp_path / "repo"
    directory.mkdir()
    for arguments in (
        ["init", "-b", "main"],
        ["config", "user.name", "Test Author"],
        ["config", "user.email", "test@example.invalid"],
        ["config", "commit.gpgsign", "false"],
        ["config", "core.hooksPath", "/dev/null"],
    ):
        subprocess.run(
            ["git", "-C", str(directory), *arguments], check=True, capture_output=True
        )
    return directory


@pytest.fixture
def git(repo: Path):
    def run(*arguments: str, cwd: Path | None = None) -> str:
        result = subprocess.run(
            ["git", "-C", str(cwd or repo), *arguments],
            check=True,
            capture_output=True,
        )
        return result.stdout.decode("utf-8", errors="surrogateescape").strip()

    return run


@pytest.fixture
def make_request(repo: Path):
    def make(*extra: str, mode: str = "oneshot") -> SyncRequest:
        args = parse_args(
            args=[
                "--kdeconnect-sync",
                "--kdeconnect-device-id",
                "phone",
                "--kdeconnect-remote-root",
                "storage/emulated/0/Notes",
                "--kdeconnect-patch-coalesce-milliseconds",
                "0",
                "--kdeconnect-mount-retry-seconds",
                "0",
                "--sys-git-repo-lock-wait-timeout-seconds",
                "0",
                "--sys-notification-provider",
                "disable",
                *extra,
            ],
            template=[*DEMON_LUCY_STARTUP_TEMPLATE, *KDECONNECT_SYNC_TEMPLATE],
        )
        context = Context(
            path=str(repo / "note.md"),
            args=args,
            run_mode=mode,
            event_id="evt-kde-test",
            event=FileModifiedEvent(str(repo / "note.md")),
        )
        return SyncRequest(
            str(repo), context, SyncSettings.from_args(args), OperatingSystem.LINUX
        )

    return make


@pytest.fixture
def notifications(monkeypatch):
    messages = []
    monkeypatch.setattr(
        "demon_lucy.modules.kdeconnect_sync.worker.safe_notify",
        lambda **kwargs: messages.append(kwargs),
    )
    return messages


@pytest.fixture
def phone(tmp_path, git):
    path = tmp_path / "phone"
    path.mkdir()
    git("init", "-b", "main", cwd=path)
    git("config", "user.name", "Phone", cwd=path)
    git("config", "user.email", "phone@example.invalid", cwd=path)
    git("config", "commit.gpgsign", "false", cwd=path)
    git("config", "core.hooksPath", "/dev/null", cwd=path)
    return path


@pytest.fixture
def incoming(phone):
    path = phone / ".demon_lucy/patch_queue/incoming_pc_to_phone"
    path.mkdir(parents=True)
    return path


@pytest.fixture
def deliver(repo, git, incoming, tmp_path):
    outgoing = tmp_path / "outgoing"
    outgoing.mkdir()

    def send(commit=None):
        if commit is None:
            git("add", "-A")
            git("commit", "--allow-empty", "-m", "PC edit")
            commit = git("rev-parse", "HEAD")
        packet = publish_packet(
            str(outgoing),
            GitPacketRepository(str(repo), 10).patch_for_commit(commit),
            "pc",
        )
        shutil.copyfile(packet.patch_path, incoming / Path(packet.patch_path).name)
        shutil.copyfile(
            packet.metadata_path, incoming / Path(packet.metadata_path).name
        )
        return packet

    return send


@pytest.fixture
def receive(phone, make_request):
    def run(*extra):
        context = replace(
            make_request("--kdeconnect-apply", *extra).context,
            path=str(phone),
            event=None,
            run_mode="cli",
        )
        return apply_incoming(str(phone), context, System([], []))

    return run
