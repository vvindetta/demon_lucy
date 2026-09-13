from __future__ import annotations

import threading
from pathlib import Path

import pytest
from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileOpenedEvent,
)
from watchdog.observers import Observer

from demon_lucy.file_handler import FileHandler
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.lib.git_state import (
    release_repo_process_lock,
    repo_process_lock_path,
    try_create_repo_process_lock,
)
from demon_lucy.module_manager import ModuleManager
from demon_lucy.modules.kdeconnect_sync import KdeconnectSync
from demon_lucy.runtime import DEMON_LUCY_STARTUP_TEMPLATE


@pytest.fixture
def make_handler(phone):
    def make(*extra):
        args = parse_args(
            args=[
                "--sys-watch-paths",
                str(phone),
                "--sys-notification-provider",
                "disable",
                "--sys-git-repo-lock-wait-timeout-seconds",
                "0",
                *extra,
            ],
            template=DEMON_LUCY_STARTUP_TEMPLATE,
        )
        manager = ModuleManager([KdeconnectSync()], args, run_mode="daemon")
        return FileHandler(manager, open_cooldown_seconds=0)

    return make


@pytest.fixture
def applies(monkeypatch):
    import demon_lucy.modules.kdeconnect_sync as kdeconnect

    original = kdeconnect.apply_incoming
    calls = []

    def record(repo, context, system):
        result = original(repo, context, system)
        calls.append((context, threading.current_thread(), result))
        return result

    monkeypatch.setattr(kdeconnect, "apply_incoming", record)
    monkeypatch.setattr(
        kdeconnect, "sync_repo", lambda _: pytest.fail("receiving tried to send")
    )
    return calls


@pytest.mark.parametrize("event_type", ["created", "modified", "moved"])
def test_ready_marker_applies_on_existing_event_thread_without_enable_flags(
    repo, phone, incoming, git, deliver, make_handler, applies, event_type
):
    (repo / "note.md").write_text("automatic\n")
    packet = deliver()
    marker = incoming / Path(packet.metadata_path).name
    handler = make_handler()
    assert handler.modules.args.require("kdeconnect-apply").value is False
    assert handler.modules.args.require("kdeconnect-sync").value is False
    assert handler.modules.args.require("kdeconnect-device-id").value == ""
    event = {
        "created": FileCreatedEvent(str(marker)),
        "modified": FileModifiedEvent(str(marker)),
        "moved": FileMovedEvent(str(incoming / ".upload.tmp"), str(marker)),
    }[event_type]
    handler.dispatch(event)
    assert (phone / "note.md").read_text() == "automatic\n"
    assert git("status", "--porcelain", cwd=phone) == ""
    context, thread, result = applies[0]
    assert context.event is event
    assert context.event_id
    assert thread is threading.current_thread()
    assert result.applied == 1
    head = git("rev-parse", "HEAD", cwd=phone)
    handler.dispatch(event)
    assert applies[-1][2].skipped == 1
    assert git("rev-parse", "HEAD", cwd=phone) == head


def test_patch_arrival_waits_for_ready_marker(
    repo, phone, incoming, deliver, make_handler, applies
):
    (repo / "note.md").write_text("complete\n")
    packet = deliver()
    marker = incoming / Path(packet.metadata_path).name
    temporary = incoming / ".upload.tmp"
    marker.rename(temporary)
    handler = make_handler()
    handler.dispatch(FileCreatedEvent(str(incoming / Path(packet.patch_path).name)))
    handler.dispatch(FileCreatedEvent(str(temporary)))
    assert applies == []
    assert not (phone / "note.md").exists()
    temporary.rename(marker)
    handler.dispatch(FileMovedEvent(str(temporary), str(marker)))
    assert (phone / "note.md").read_text() == "complete\n"


@pytest.mark.parametrize(
    "relative",
    [
        "note.md",
        "config.json",
        ".demon_lucy/patch_queue/outgoing_pc_to_phone/packet.json",
        ".demon_lucy/patch_queue/incoming_pc_to_phone_extra/packet.json",
        ".demon_lucy/patch_queue/incoming_pc_to_phone/nested/packet.json",
    ],
)
def test_unrelated_paths_do_not_trigger_receiving(
    phone, make_handler, applies, relative
):
    path = phone / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n")
    make_handler().dispatch(FileCreatedEvent(str(path)))
    assert applies == []


@pytest.mark.parametrize("event_class", [FileDeletedEvent, FileOpenedEvent])
def test_deleting_or_reading_marker_does_not_apply(
    repo, incoming, deliver, make_handler, applies, event_class
):
    (repo / "note.md").write_text("waiting\n")
    packet = deliver()
    marker = incoming / Path(packet.metadata_path).name
    make_handler().dispatch(event_class(str(marker)))
    assert applies == []


def test_custom_queue_path_is_detected(
    repo, phone, incoming, deliver, make_handler, applies
):
    (repo / "note.md").write_text("custom queue\n")
    packet = deliver()
    custom = phone / "incoming packets/incoming_pc_to_phone"
    custom.parent.mkdir()
    incoming.rename(custom)
    handler = make_handler("--kdeconnect-patch-queue-dir", "incoming packets")
    handler.dispatch(FileCreatedEvent(str(custom / Path(packet.metadata_path).name)))
    assert applies[0][2].applied == 1
    assert (phone / "note.md").read_text() == "custom queue\n"


def test_missing_predecessor_is_applied_on_its_arrival(
    repo, phone, incoming, git, deliver, make_handler, applies
):
    (repo / "note.md").write_text("base\n")
    git("add", ".")
    git("commit", "-m", "base")
    base = git("rev-parse", "HEAD")
    (repo / "note.md").write_text("next\n")
    packet = deliver()
    handler = make_handler()
    handler.dispatch(FileCreatedEvent(str(incoming / Path(packet.metadata_path).name)))
    assert applies[-1][2].pending == 1
    assert not (phone / "note.md").exists()
    predecessor = deliver(base)
    handler.dispatch(
        FileCreatedEvent(str(incoming / Path(predecessor.metadata_path).name))
    )
    assert applies[-1][2].applied == 2
    assert (phone / "note.md").read_text() == "next\n"


def test_busy_repository_waits_for_another_marker_event(
    repo, phone, incoming, deliver, make_handler, applies, notifications
):
    (repo / "note.md").write_text("waiting\n")
    packet = deliver()
    event = FileModifiedEvent(str(incoming / Path(packet.metadata_path).name))
    handler = make_handler()
    lock = repo_process_lock_path(str(phone))
    assert try_create_repo_process_lock(lock)
    try:
        handler.dispatch(event)
        assert applies == []
        assert notifications == []
    finally:
        assert release_repo_process_lock(lock)
    handler.dispatch(event)
    assert (phone / "note.md").read_text() == "waiting\n"


def test_local_edits_are_preserved_on_marker_arrival(
    repo, phone, incoming, deliver, make_handler, notifications
):
    (repo / "note.md").write_text("from PC\n")
    packet = deliver()
    (phone / "note.md").write_text("phone edit\n")
    make_handler().dispatch(
        FileCreatedEvent(str(incoming / Path(packet.metadata_path).name))
    )
    assert (phone / "note.md").read_text() == "phone edit\n"
    assert len(notifications) == 1


def test_ignored_queue_events_do_not_receive(
    repo, phone, incoming, deliver, make_handler, applies
):
    (repo / "note.md").write_text("ignored\n")
    packet = deliver()
    handler = make_handler("--sys-ignore-paths", str(incoming))
    handler.dispatch(FileCreatedEvent(str(incoming / Path(packet.metadata_path).name)))
    assert applies == []
    assert not (phone / "note.md").exists()


def test_packet_cannot_bypass_ignored_note_paths(
    repo, phone, incoming, deliver, make_handler, notifications
):
    (repo / "note.md").write_text("ignored\n")
    packet = deliver()
    handler = make_handler("--sys-ignore-paths", str(phone / "note.md"))
    handler.dispatch(FileCreatedEvent(str(incoming / Path(packet.metadata_path).name)))
    assert not (phone / "note.md").exists()
    assert "excluded by --sys-ignore-paths" in notifications[0]["message"]


def test_watcher_detects_atomic_ready_marker_publication(
    repo, phone, incoming, deliver, make_handler, monkeypatch
):
    import demon_lucy.modules.kdeconnect_sync as kdeconnect

    (repo / "note.md").write_text("watchdog integration\n")
    packet = deliver()
    marker = incoming / Path(packet.metadata_path).name
    temporary = incoming / ".upload.tmp"
    marker.rename(temporary)
    completed = threading.Event()
    original = kdeconnect.apply_incoming

    def apply(*args):
        result = original(*args)
        if result.applied:
            completed.set()
        return result

    monkeypatch.setattr(kdeconnect, "apply_incoming", apply)
    observer = Observer()
    observer.schedule(make_handler(), str(phone), recursive=True)
    observer.start()
    try:
        temporary.rename(marker)
        assert completed.wait(5), "ready-marker event was not applied"
        assert (phone / "note.md").read_text() == "watchdog integration\n"
    finally:
        observer.stop()
        observer.join(timeout=5)
    assert not observer.is_alive()
