from __future__ import annotations

from watchdog.events import FileModifiedEvent

import lucy_notes_manager.modules.kdeconnect_sync as kde_mod
from lucy_notes_manager.modules.abstract_module import Context, System
from lucy_notes_manager.modules.git.worker import (
    DirtyTreeCommitResult,
    PatchPacketBuildResult,
)
from lucy_notes_manager.modules.kdeconnect_sync import KdeconnectSync
from lucy_notes_manager.modules.kdeconnect_sync.transport import TransferResult


def _base_config(*, enabled: bool, dry_run: bool = False) -> dict:
    return {
        "kdeconnect_sync": enabled,
        "kdeconnect_device_id": "device-1",
        "kdeconnect_remote_root": "/storage/emulated/0/Notes",
        "kdeconnect_patch_queue_dir": ".lucy/patch_queue",
        "kdeconnect_patch_coalesce_milliseconds": 0,
        "kdeconnect_patch_retry_seconds": 5.0,
        "kdeconnect_patch_max_retries": 3,
        "kdeconnect_binary_fallback_enabled": False,
        "kdeconnect_command_timeout_seconds": 10.0,
        "kdeconnect_mount_retry_seconds": 1.0,
        "kdeconnect_dry_run": dry_run,
        "sys_notification_provider": "disable",
        "sys_notification_min_interval_seconds": 0.1,
        "sys_notification_error_backoff_base_seconds": 0.1,
        "sys_notification_error_backoff_max_seconds": 1.0,
        "sys_notification_error_burst_limit": 3,
        "sys_notification_error_burst_window_seconds": 10.0,
    }


def test_modified_noop_when_module_disabled(monkeypatch):
    module = KdeconnectSync()
    called = {"value": False}

    monkeypatch.setattr(
        module,
        "_schedule_repo_sync",
        lambda **_kwargs: called.__setitem__("value", True),
    )
    monkeypatch.setattr(
        module, "_run_repo_sync", lambda **_kwargs: called.__setitem__("value", True)
    )

    ctx = Context(
        path="/repo/note.md", config=_base_config(enabled=False), arg_lines={}
    )
    system = System(
        event=FileModifiedEvent("/repo/note.md"), global_template=[], modules=[module]
    )

    result = module.modified(ctx, system)

    assert result is None
    assert called["value"] is False


def test_modified_ignores_patch_queue_internal_files(monkeypatch):
    module = KdeconnectSync()
    called = {"value": False}

    monkeypatch.setattr(kde_mod, "find_parent_with", lambda _path, _marker: "/repo")
    monkeypatch.setattr(
        module,
        "_schedule_repo_sync",
        lambda **_kwargs: called.__setitem__("value", True),
    )

    queue_file = "/repo/.lucy/patch_queue/outgoing_pc_to_phone/p-1.patch"
    ctx = Context(path=queue_file, config=_base_config(enabled=True), arg_lines={})
    system = System(
        event=FileModifiedEvent(queue_file), global_template=[], modules=[module]
    )

    result = module.modified(ctx, system)

    assert result is None
    assert called["value"] is False


def test_modified_oneshot_builds_and_sends_patch(monkeypatch):
    module = KdeconnectSync()

    monkeypatch.setattr(kde_mod, "find_parent_with", lambda _path, _marker: "/repo")
    monkeypatch.setattr(
        kde_mod, "ensure_queue_excluded_in_repo", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        kde_mod,
        "commit_dirty_tree",
        lambda *_args, **_kwargs: DirtyTreeCommitResult(
            status="committed",
            repo_root="/repo",
            commit_sha="abc123",
            changed_paths=("note.md",),
        ),
    )
    monkeypatch.setattr(
        kde_mod,
        "build_patch_packet",
        lambda *_args, **_kwargs: PatchPacketBuildResult(
            status="built",
            repo_root="/repo",
            patch_id="p-1",
            patch_path="/tmp/p-1.patch",
            metadata_path="/tmp/p-1.json",
        ),
    )

    transfer_calls: list[dict] = []

    def _transfer(**kwargs):
        transfer_calls.append(dict(kwargs))
        return TransferResult(status="sent", remote_incoming_dir="/mnt/phone/incoming")

    monkeypatch.setattr(kde_mod, "transfer_packet_to_phone", _transfer)

    ctx = Context(path="/repo/note.md", config=_base_config(enabled=True), arg_lines={})
    system = System(
        event=FileModifiedEvent("/repo/note.md"),
        global_template=[],
        modules=[module],
        run_mode="oneshot",
    )
    result = module.modified(ctx, system)

    assert result is None
    assert len(transfer_calls) == 1
    assert transfer_calls[0]["device_id"] == "device-1"
    assert transfer_calls[0]["remote_root"] == "/storage/emulated/0/Notes"


def test_modified_oneshot_silently_skips_when_git_repo_is_busy(monkeypatch):
    module = KdeconnectSync()
    notifications: list[dict] = []

    monkeypatch.setattr(kde_mod, "find_parent_with", lambda _path, _marker: "/repo")
    monkeypatch.setattr(
        kde_mod, "ensure_queue_excluded_in_repo", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        kde_mod,
        "safe_notify",
        lambda **kwargs: notifications.append(kwargs),
    )
    monkeypatch.setattr(
        kde_mod,
        "commit_dirty_tree",
        lambda *_args, **_kwargs: DirtyTreeCommitResult(
            status="busy",
            repo_root="/repo",
            error_text="repo lock is busy",
        ),
    )

    ctx = Context(path="/repo/note.md", config=_base_config(enabled=True), arg_lines={})
    system = System(
        event=FileModifiedEvent("/repo/note.md"),
        global_template=[],
        modules=[module],
        run_mode="oneshot",
    )

    result = module.modified(ctx, system)

    assert result is None
    assert notifications == []
