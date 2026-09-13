from __future__ import annotations

import threading
from dataclasses import replace

import pytest
from watchdog.events import FileModifiedEvent

from demon_lucy.lib.args.parser import parse_args
from demon_lucy.module_manager import ModuleManager
from demon_lucy.modules.abstract_module import System
from demon_lucy.modules.kdeconnect_sync import KdeconnectSync
from demon_lucy.modules.kdeconnect_sync.config import KDECONNECT_SYNC_TEMPLATE


@pytest.mark.parametrize(
    "flag,value",
    [
        ("kdeconnect-patch-queue-dir", "/tmp/outside"),
        ("kdeconnect-patch-queue-dir", "../outside"),
        ("kdeconnect-patch-queue-dir", "."),
        ("kdeconnect-patch-queue-dir", ".git/queue"),
        ("kdeconnect-patch-queue-dir", "C:/queue"),
        ("kdeconnect-patch-queue-dir", "notes\nanything"),
        ("kdeconnect-remote-root", "/storage/emulated/0/Notes"),
        ("kdeconnect-remote-root", "../../outside"),
        ("kdeconnect-device-id", ""),
        ("kdeconnect-patch-max-retries", "0"),
        ("kdeconnect-patch-coalesce-milliseconds", "-1"),
        ("kdeconnect-command-timeout-seconds", "0"),
        ("kdeconnect-command-timeout-seconds", "nan"),
        ("kdeconnect-mount-retry-seconds", "-1"),
        ("kdeconnect-patch-retry-seconds", "0"),
    ],
)
def test_invalid_config_is_rejected(make_request, flag, value):
    with pytest.raises(ValueError):
        make_request(f"--{flag}", value)


def test_can_load_kdeconnect_without_git_module(make_request, repo, monkeypatch):
    request = make_request()
    module = KdeconnectSync()
    manager = ModuleManager([module], request.context.args, run_mode="oneshot")
    called = []
    monkeypatch.setattr(
        "demon_lucy.modules.kdeconnect_sync.sync_repo",
        lambda req: called.append(req) or False,
    )
    (repo / "note.md").write_text("hello\n")
    manager.run(str(repo / "note.md"), FileModifiedEvent(str(repo / "note.md")))
    assert len(called) == 1
    assert called[0].context.args.find("git-commit-message") is None


def test_internal_queue_events_do_not_schedule_sync(make_request, repo, monkeypatch):
    request = make_request(mode="daemon")
    path = str(repo / ".demon_lucy/patch_queue/outgoing_pc_to_phone/packet.json")
    context = replace(request.context, path=path, event=FileModifiedEvent(path))
    module = KdeconnectSync()
    monkeypatch.setattr(
        module, "_schedule", lambda _: pytest.fail("queue event was scheduled")
    )
    module.modified(context, System([], [module]))


def test_invalid_settings_notify_before_any_sync(
    make_request, monkeypatch, notifications
):
    request = make_request()
    args = request.context.args
    args = args.merged_with(
        parse_args(
            args=["--kdeconnect-remote-root", "../outside"],
            template=KDECONNECT_SYNC_TEMPLATE,
            include_defaults=False,
        )
    )
    module = KdeconnectSync()
    monkeypatch.setattr(
        "demon_lucy.modules.kdeconnect_sync.sync_repo",
        lambda _: pytest.fail("invalid config ran sync"),
    )
    module.modified(replace(request.context, args=args), System([], [module]))
    assert len(notifications) == 1


def test_one_worker_coalesces_edits_arriving_during_transfer(make_request, monkeypatch):
    request = make_request(mode="daemon")
    module = KdeconnectSync()
    started = threading.Event()
    release = threading.Event()
    calls = []

    def sync(item):
        calls.append(item.context.event_id)
        if len(calls) == 1:
            started.set()
            assert release.wait(3)
        return False

    monkeypatch.setattr("demon_lucy.modules.kdeconnect_sync.sync_repo", sync)
    try:
        module._schedule(request)
        assert started.wait(3)
        for index in range(20):
            module._schedule(
                replace(
                    request, context=replace(request.context, event_id=f"evt-{index}")
                )
            )
        assert calls == ["evt-kde-test"]
    finally:
        release.set()
    with module._condition:
        assert module._condition.wait_for(lambda: not module._active, timeout=3)
    assert calls == ["evt-kde-test", "evt-19"]


def test_daemon_retries_without_another_file_event(make_request, monkeypatch):
    request = make_request("--kdeconnect-patch-retry-seconds", "0.01", mode="daemon")
    module = KdeconnectSync()
    calls = []

    def sync(item):
        calls.append(item)
        return len(calls) == 1

    monkeypatch.setattr("demon_lucy.modules.kdeconnect_sync.sync_repo", sync)
    module._schedule(request)
    with module._condition:
        assert module._condition.wait_for(lambda: not module._active, timeout=3)
    assert len(calls) == 2


def test_direct_cli_flushes_queue_synchronously(make_request, monkeypatch):
    request = make_request(mode="cli")
    module = KdeconnectSync()
    called = []
    monkeypatch.setattr(
        "demon_lucy.modules.kdeconnect_sync.sync_repo",
        lambda req: called.append(req) or False,
    )
    module.cli(replace(request.context, event=None), System([], [module]))
    assert len(called) == 1
    assert module._active == set()


def test_disabled_module_does_not_validate_or_schedule(make_request, monkeypatch):
    request = make_request(mode="daemon")
    disabled = parse_args(args=[], template=KDECONNECT_SYNC_TEMPLATE)
    context = replace(request.context, args=request.context.args.merged_with(disabled))
    module = KdeconnectSync()
    monkeypatch.setattr(
        module, "_schedule", lambda _: pytest.fail("disabled module scheduled sync")
    )
    module.modified(context, System([], [module]))
    assert module._active == set()


def test_unexpected_worker_failure_reports_once_and_cleans_up(
    make_request, monkeypatch, notifications, caplog
):
    def fail(request):
        raise RuntimeError("unexpected failure")

    monkeypatch.setattr("demon_lucy.modules.kdeconnect_sync.sync_repo", fail)
    module = KdeconnectSync()
    module._schedule(make_request(mode="daemon"))
    with module._condition:
        assert module._condition.wait_for(lambda: not module._active, timeout=3)
    assert module._pending == {}
    assert len(notifications) == 1
    records = [record for record in caplog.records if record.levelname == "ERROR"]
    assert len(records) == 1
    assert "kdeconnect.sync_failed" in records[0].message
    assert records[0].exc_info is not None
