from __future__ import annotations

import json
from pathlib import Path

import pytest

from demon_lucy.lib.git_state import (
    repo_process_lock_path,
    try_create_repo_process_lock,
)
from demon_lucy.modules.kdeconnect_sync.transport import TransferResult, TransferStatus
from demon_lucy.modules.kdeconnect_sync.worker import sync_repo


def test_standalone_sender_snapshots_full_tree_and_sends_binary_packet(
    repo,
    git,
    make_request,
    monkeypatch,
    notifications,
    tmp_path,
):
    (repo / "note.md").write_text("one\n", encoding="utf-8")
    (repo / "другая заметка.txt").write_text("two\n", encoding="utf-8")
    (repo / "binary.dat").write_bytes(b"\0\xff\xfe\x01")
    sent = []

    def send(*, settings, packet):
        assert not Path(repo_process_lock_path(str(repo))).exists()
        sent.append(packet)
        return TransferResult(TransferStatus.SENT, remote_incoming_dir="/mounted/queue")

    monkeypatch.setattr(
        "demon_lucy.modules.kdeconnect_sync.worker.transfer_packet_to_phone", send
    )
    request = make_request()
    assert sync_repo(request) is False
    assert len(sent) == 1
    assert notifications == []
    assert git("status", "--porcelain") == ""
    metadata = json.loads(Path(sent[0].metadata_path).read_text())
    assert set(metadata["path_list"]) == {"note.md", "другая заметка.txt", "binary.dat"}
    assert metadata["base_commit"] == ""
    assert metadata["format"] == "git-diff-binary"
    assert b"GIT binary patch" in Path(sent[0].patch_path).read_bytes()
    receiver = tmp_path / "receiver"
    receiver.mkdir()
    git("init", cwd=receiver)
    git("apply", "--index", sent[0].patch_path, cwd=receiver)
    assert (receiver / "binary.dat").read_bytes() == b"\0\xff\xfe\x01"
    assert sync_repo(request) is False
    assert len(sent) == 1


def test_pending_transfer_retried_on_clean_tree_after_module_restart(
    repo,
    git,
    make_request,
    monkeypatch,
    notifications,
):
    (repo / "note.md").write_text("unsent\n")
    seen = []

    def send(*, settings, packet):
        seen.append(packet.commit)
        return TransferResult(
            TransferStatus.RETRY if len(seen) == 1 else TransferStatus.SENT,
            error_text="offline",
        )

    monkeypatch.setattr(
        "demon_lucy.modules.kdeconnect_sync.worker.transfer_packet_to_phone", send
    )
    assert sync_repo(make_request()) is True
    head = git("rev-parse", "HEAD")
    assert git("status", "--porcelain") == ""
    assert sync_repo(make_request()) is False
    assert seen == [head, head]
    assert notifications == []
    assert git("rev-list", "--count", "HEAD") == "1"


def test_external_commits_and_renames_deletions_are_queued_in_order(
    repo,
    git,
    make_request,
    monkeypatch,
    notifications,
    tmp_path,
):
    (repo / "old.md").write_text("keep this content\n")
    (repo / "delete.md").write_text("remove me\n")
    sent = []
    monkeypatch.setattr(
        "demon_lucy.modules.kdeconnect_sync.worker.transfer_packet_to_phone",
        lambda *, settings, packet: sent.append(packet)
        or TransferResult(TransferStatus.SENT),
    )
    assert sync_repo(make_request()) is False
    base = git("rev-parse", "HEAD")
    receiver = tmp_path / "receiver"
    git("clone", "--quiet", str(repo), str(receiver))
    (repo / "old.md").rename(repo / "new.md")
    (repo / "delete.md").unlink()
    git("add", "-A")
    git("commit", "-m", "external commit, e.g. the Git module")
    external = git("rev-parse", "HEAD")
    (repo / "another.md").write_text("also send\n")
    assert sync_repo(make_request()) is False
    assert [packet.commit for packet in sent] == [
        base,
        external,
        git("rev-parse", "HEAD"),
    ]
    for packet in sent[1:]:
        git("apply", "--index", packet.patch_path, cwd=receiver)
    assert not (receiver / "old.md").exists()
    assert not (receiver / "delete.md").exists()
    assert (receiver / "new.md").read_text() == "keep this content\n"
    assert (receiver / "another.md").read_text() == "also send\n"
    metadata = json.loads(Path(sent[1].metadata_path).read_text())
    assert set(metadata["path_list"]) == {"old.md", "new.md", "delete.md"}
    assert notifications == []


def test_packet_publication_failure_recovered_before_new_commits(
    repo,
    git,
    make_request,
    monkeypatch,
    notifications,
):
    import demon_lucy.modules.kdeconnect_sync.worker as worker

    (repo / "note.md").write_text("first\n")
    original = worker.publish_packet
    monkeypatch.setattr(
        worker,
        "publish_packet",
        lambda *_args: (_ for _ in ()).throw(OSError("disk full")),
    )
    assert sync_repo(make_request()) is False
    first = git("rev-parse", "HEAD")
    assert len(notifications) == 1
    monkeypatch.setattr(worker, "publish_packet", original)
    sent = []
    monkeypatch.setattr(
        worker,
        "transfer_packet_to_phone",
        lambda *, settings, packet: sent.append(packet.commit)
        or TransferResult(TransferStatus.SENT),
    )
    (repo / "note.md").write_text("second\n")
    assert sync_repo(make_request()) is False
    assert sent == [first, git("rev-parse", "HEAD")]


def test_dry_run_does_not_commit_create_queue_or_contact_phone(
    repo,
    git,
    make_request,
    monkeypatch,
    notifications,
):
    (repo / "note.md").write_text("unsaved\n")
    original_status = git("status", "--porcelain")
    exclude = (repo / ".git/info/exclude").read_bytes()
    monkeypatch.setattr(
        "demon_lucy.modules.kdeconnect_sync.worker.transfer_packet_to_phone",
        lambda **_: pytest.fail("dry run contacted phone"),
    )
    assert sync_repo(make_request("--kdeconnect-dry-run")) is False
    assert git("status", "--porcelain") == original_status
    assert git("for-each-ref") == ""
    assert (repo / ".git/info/exclude").read_bytes() == exclude
    assert not (repo / ".demon_lucy").exists()
    assert notifications == []


def test_busy_repo_preserves_changes_without_notification(
    repo, git, make_request, notifications
):
    (repo / "note.md").write_text("pending\n")
    lock = repo_process_lock_path(str(repo))
    assert try_create_repo_process_lock(lock)
    assert sync_repo(make_request()) is True
    assert git("for-each-ref") == ""
    assert Path(lock).exists()
    assert notifications == []


def test_lock_creation_failure_does_not_run_unlocked(
    repo, git, make_request, monkeypatch, notifications
):
    (repo / "note.md").write_text("pending\n")
    monkeypatch.setattr(
        "demon_lucy.lib.git_state.try_create_repo_process_lock",
        lambda _: (_ for _ in ()).throw(PermissionError("denied")),
    )
    assert sync_repo(make_request()) is False
    assert git("for-each-ref") == ""
    assert len(notifications) == 1


def test_empty_repository_is_noop(repo, make_request, notifications):
    assert sync_repo(make_request()) is False
    assert notifications == []


def test_main_and_linked_worktree_share_lock_for_common_exclude_updates(
    repo, git, make_request, notifications, tmp_path
):
    from dataclasses import replace

    (repo / "note.md").write_text("base\n")
    git("add", ".")
    git("commit", "-m", "base")
    worktree = tmp_path / "worktree"
    git("worktree", "add", "-b", "other", str(worktree))
    main_lock = repo_process_lock_path(str(repo))
    assert repo_process_lock_path(str(worktree)) == main_lock
    assert try_create_repo_process_lock(main_lock)
    (worktree / "note.md").write_text("pending\n")
    head = git("rev-parse", "HEAD", cwd=worktree)
    assert sync_repo(replace(make_request(), repo_root=str(worktree))) is True
    assert git("rev-parse", "HEAD", cwd=worktree) == head
    assert notifications == []


def test_symlink_queue_is_rejected_before_committing(
    repo, tmp_path, git, make_request, notifications
):
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / ".demon_lucy").symlink_to(outside, target_is_directory=True)
    (repo / "note.md").write_text("pending\n")
    assert sync_repo(make_request()) is False
    assert list(outside.iterdir()) == []
    assert git("for-each-ref") == ""
    assert len(notifications) == 1


def test_worktree_uses_common_git_exclude(
    repo, tmp_path, git, make_request, monkeypatch, notifications
):
    from dataclasses import replace

    (repo / "note.md").write_text("base\n")
    git("add", ".")
    git("commit", "-m", "base")
    worktree = tmp_path / "worktree"
    git("worktree", "add", "-b", "other", str(worktree))
    (worktree / "note.md").write_text("worktree change\n")
    monkeypatch.setattr(
        "demon_lucy.modules.kdeconnect_sync.worker.transfer_packet_to_phone",
        lambda **_: TransferResult(TransferStatus.SENT),
    )
    assert sync_repo(replace(make_request(), repo_root=str(worktree))) is False
    assert git("status", "--porcelain", cwd=worktree) == ""
    assert notifications == []


def test_tampered_queued_packet_is_not_sent(
    repo, make_request, monkeypatch, notifications
):
    (repo / "note.md").write_text("original\n")
    monkeypatch.setattr(
        "demon_lucy.modules.kdeconnect_sync.worker.transfer_packet_to_phone",
        lambda **_: TransferResult(TransferStatus.RETRY),
    )
    assert sync_repo(make_request()) is True
    packet = next((repo / ".demon_lucy").rglob("*.patch"))
    packet.write_text("tampered")
    monkeypatch.setattr(
        "demon_lucy.modules.kdeconnect_sync.worker.transfer_packet_to_phone",
        lambda **_: pytest.fail("corrupt packet sent"),
    )
    assert sync_repo(make_request()) is False
    assert "checksum" in notifications[-1]["message"]


def test_merge_commit_patch_applies_against_first_parent(
    repo, git, make_request, monkeypatch, notifications, tmp_path
):
    (repo / "base.md").write_text("base\n")
    sent = []
    monkeypatch.setattr(
        "demon_lucy.modules.kdeconnect_sync.worker.transfer_packet_to_phone",
        lambda *, settings, packet: sent.append(packet)
        or TransferResult(TransferStatus.SENT),
    )
    assert sync_repo(make_request()) is False
    receiver = tmp_path / "receiver"
    git("clone", "--quiet", str(repo), str(receiver))
    git("switch", "-c", "feature")
    (repo / "feature.md").write_text("feature\n")
    git("add", ".")
    git("commit", "-m", "feature")
    git("switch", "main")
    (repo / "main.md").write_text("main\n")
    git("add", ".")
    git("commit", "-m", "main change")
    git("merge", "--no-ff", "feature", "-m", "merge feature")
    assert sync_repo(make_request()) is False
    assert (
        len(sent) == 3
    )  # initial, main, merge; merged branch is represented by the merge delta
    for packet in sent[1:]:
        git("apply", "--index", packet.patch_path, cwd=receiver)
    assert (receiver / "feature.md").read_text() == "feature\n"
    assert (receiver / "main.md").read_text() == "main\n"
    assert notifications == []


def test_main_repo_and_linked_worktree_have_separate_queue_cursors(
    repo, git, make_request, monkeypatch, notifications, tmp_path
):
    from dataclasses import replace

    (repo / "note.md").write_text("base\n")
    monkeypatch.setattr(
        "demon_lucy.modules.kdeconnect_sync.worker.transfer_packet_to_phone",
        lambda **_: TransferResult(TransferStatus.SENT),
    )
    assert sync_repo(make_request()) is False
    original = git("for-each-ref", "--format=%(refname) %(objectname)", "refs/worktree")
    worktree = tmp_path / "worktree"
    git("worktree", "add", "-b", "linked", str(worktree))
    (worktree / "note.md").write_text("linked change\n")
    assert sync_repo(replace(make_request(), repo_root=str(worktree))) is False
    assert (
        git("for-each-ref", "--format=%(refname) %(objectname)", "refs/worktree")
        == original
    )
    assert sync_repo(make_request()) is False
    assert notifications == []
