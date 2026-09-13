from __future__ import annotations

import json
import hashlib
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from demon_lucy.modules.kdeconnect_sync.git_packets import (
    GitPacketError,
    GitPacketRepository,
)
from demon_lucy.modules.abstract_module import System
from demon_lucy.modules.kdeconnect_sync import KdeconnectSync
from demon_lucy.modules.kdeconnect_sync.receiver import (
    LocalChangesError,
    PacketReceiver,
)


def test_applies_binary_unicode_renames_and_deletions_in_parent_order(
    repo, phone, git, deliver, receive
):
    (repo / "заметка.txt").write_text("first\n", encoding="utf-8")
    (repo / "delete.txt").write_text("delete me\n")
    first = deliver()
    (repo / "заметка.txt").rename(repo / "новое имя.txt")
    (repo / "delete.txt").unlink()
    (repo / "image.bin").write_bytes(b"\0\xff\xfe")
    second = deliver()
    result = receive()
    assert result.applied == 2
    assert result.pending == 0
    assert (phone / "новое имя.txt").read_text() == "first\n"
    assert (phone / "image.bin").read_bytes() == b"\0\xff\xfe"
    assert not (phone / "заметка.txt").exists()
    assert not (phone / "delete.txt").exists()
    assert git("status", "--porcelain", cwd=phone) == ""
    assert git("rev-parse", "HEAD^{tree}", cwd=phone) == git("rev-parse", "HEAD^{tree}")
    assert git("rev-parse", "HEAD", cwd=phone) != second.commit
    assert receive().skipped == 2
    assert git("rev-list", "--count", "HEAD", cwd=phone) == "2"
    assert first.parent == ""


def test_waits_for_missing_predecessor_then_applies_both(
    repo, phone, git, incoming, deliver, receive
):
    (repo / "note.md").write_text("one\n")
    git("add", ".")
    git("commit", "-m", "base")
    first = git("rev-parse", "HEAD")
    (repo / "note.md").write_text("two\n")
    deliver()
    assert receive().pending == 1
    assert not (phone / "note.md").exists()
    deliver(first)
    assert receive().applied == 2
    assert (phone / "note.md").read_text() == "two\n"


def test_local_uncommitted_edits_are_preserved(repo, phone, git, deliver, receive):
    (repo / "note.md").write_text("base\n")
    deliver()
    receive()
    before_head = git("rev-parse", "HEAD", cwd=phone)
    (phone / "note.md").write_text("phone edit\n")
    (repo / "note.md").write_text("PC edit\n")
    deliver()
    with pytest.raises(LocalChangesError):
        receive()
    assert (phone / "note.md").read_text() == "phone edit\n"
    assert git("rev-parse", "HEAD", cwd=phone) == before_head
    assert git("diff", "--cached", cwd=phone) == ""


def test_local_commits_are_not_blindly_overwritten(repo, phone, git, deliver, receive):
    (repo / "note.md").write_text("base\n")
    deliver()
    receive()
    (phone / "note.md").write_text("phone commit\n")
    git("add", "note.md", cwd=phone)
    git("commit", "-m", "phone edit", cwd=phone)
    head = git("rev-parse", "HEAD", cwd=phone)
    (repo / "note.md").write_text("PC edit\n")
    deliver()
    assert receive().pending == 1
    assert (phone / "note.md").read_text() == "phone commit\n"
    assert git("rev-parse", "HEAD", cwd=phone) == head


def test_untracked_phone_file_is_preserved(repo, phone, deliver, receive):
    (repo / "note.md").write_text("PC edit\n")
    deliver()
    (phone / "note.md").write_text("phone edit\n")
    with pytest.raises(LocalChangesError):
        receive()
    assert (phone / "note.md").read_text() == "phone edit\n"


def test_packets_already_present_through_git_are_skipped(
    repo, phone, git, deliver, receive
):
    (repo / "note.md").write_text("already synced\n")
    packet = deliver()
    git("fetch", str(repo), "main", cwd=phone)
    git("merge", "--ff-only", "FETCH_HEAD", cwd=phone)
    result = receive()
    assert result.applied == 0
    assert result.skipped == 1
    assert git("rev-parse", "HEAD", cwd=phone) == packet.commit


def test_empty_commits_advance_receive_chain(repo, phone, git, deliver, receive):
    (repo / "note.md").write_text("unchanged\n")
    deliver()
    deliver()
    assert receive().applied == 2
    assert git("rev-list", "--count", "HEAD", cwd=phone) == "2"


def test_checksum_failure_never_changes_phone(
    repo, phone, incoming, deliver, receive, git
):
    (repo / "note.md").write_text("PC edit\n")
    packet = deliver()
    (incoming / Path(packet.patch_path).name).write_bytes(b"bad bytes")
    with pytest.raises(ValueError, match="checksum"):
        receive()
    assert not (phone / "note.md").exists()
    assert git("for-each-ref", cwd=phone) == ""


def test_patch_without_ready_metadata_is_ignored(
    repo, phone, incoming, deliver, receive
):
    (repo / "note.md").write_text("PC edit\n")
    packet = deliver()
    (incoming / Path(packet.metadata_path).name).unlink()
    assert receive().applied == 0
    assert not (phone / "note.md").exists()


def test_metadata_paths_must_match_patch(repo, phone, incoming, deliver, receive, git):
    (repo / "note.md").write_text("PC edit\n")
    packet = deliver()
    path = incoming / Path(packet.metadata_path).name
    metadata = json.loads(path.read_text())
    metadata["path_list"] = ["innocent.md"]
    path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="path list"):
        receive()
    assert not (phone / "note.md").exists()
    assert git("for-each-ref", cwd=phone) == ""


@pytest.mark.parametrize(
    "path", ["../outside", ".git/config", ".demon_lucy/patch_queue/attack"]
)
def test_unsafe_declared_paths_are_rejected(repo, incoming, deliver, receive, path):
    (repo / "note.md").write_text("PC edit\n")
    packet = deliver()
    metadata_path = incoming / Path(packet.metadata_path).name
    metadata = json.loads(metadata_path.read_text())
    metadata["path_list"] = [path]
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError):
        receive()


def test_symlink_creation_is_rejected_before_worktree_writes(
    repo, phone, deliver, receive
):
    (repo / "link").symlink_to("/tmp/outside")
    deliver()
    with pytest.raises(ValueError, match="symlinks"):
        receive()
    assert not (phone / "link").is_symlink()


def test_existing_symlink_cannot_redirect_patch(
    repo, phone, tmp_path, deliver, receive
):
    (repo / "folder").mkdir()
    (repo / "folder/note.md").write_text("PC edit\n")
    deliver()
    outside = tmp_path / "outside"
    outside.mkdir()
    (phone / "folder").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        receive()
    assert list(outside.iterdir()) == []


def test_dry_run_does_not_modify_files_index_refs_or_exclude(
    repo, phone, git, deliver, receive
):
    (repo / "note.md").write_text("PC edit\n")
    deliver()
    before = {
        str(path.relative_to(phone)): path.read_bytes()
        for path in phone.rglob("*")
        if path.is_file()
    }
    result = receive("--kdeconnect-dry-run")
    after = {
        str(path.relative_to(phone)): path.read_bytes()
        for path in phone.rglob("*")
        if path.is_file()
    }
    assert result.pending == 1
    assert before == after


@pytest.mark.parametrize("after_apply", [False, True])
def test_interrupted_apply_resumes_without_duplicate_commit(
    repo, phone, git, deliver, receive, monkeypatch, after_apply
):
    (repo / "note.md").write_text("PC edit\n")
    deliver()
    original = GitPacketRepository.run
    failed = False

    def interrupt(self, arguments, **kwargs):
        nonlocal failed
        match = (
            arguments == ["update-ref", "--stdin"]
            if after_apply
            else arguments[:2] == ["apply", "--index"]
        )
        if match and not failed:
            failed = True
            raise OSError("simulated interruption")
        return original(self, arguments, **kwargs)

    monkeypatch.setattr(GitPacketRepository, "run", interrupt)
    with pytest.raises(OSError, match="interruption"):
        receive()
    assert failed
    assert receive().applied == 1
    assert (phone / "note.md").read_text() == "PC edit\n"
    assert git("status", "--porcelain", cwd=phone) == ""
    assert git("rev-list", "--count", "HEAD", cwd=phone) == "1"


def test_recovery_does_not_overwrite_edits_made_after_interruption(
    repo, phone, deliver, receive, monkeypatch
):
    (repo / "note.md").write_text("PC edit\n")
    deliver()
    original = GitPacketRepository.run

    def interrupt(self, arguments, **kwargs):
        if arguments == ["update-ref", "--stdin"]:
            raise OSError("simulated interruption")
        return original(self, arguments, **kwargs)

    monkeypatch.setattr(GitPacketRepository, "run", interrupt)
    with pytest.raises(OSError):
        receive()
    monkeypatch.setattr(GitPacketRepository, "run", original)
    (phone / "note.md").write_text("new phone edit\n")
    with pytest.raises(LocalChangesError):
        receive()
    assert (phone / "note.md").read_text() == "new phone edit\n"


def test_receiver_cli_needs_no_device_id_remote_root_or_kde_cli(
    repo, phone, deliver, tmp_path
):
    (repo / "note.md").write_text("from PC\n")
    deliver()
    config = tmp_path / "empty-config.txt"
    config.write_text("")
    project = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [
            sys.executable,
            str(project / "main_oneshot.py"),
            "--sys-config-path",
            str(config),
            "--sys-modules",
            "kdeconnect_sync",
            "--kdeconnect-apply",
            "--sys-notification-provider",
            "disable",
            "--sys-log-level",
            "info",
        ],
        cwd=phone,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (phone / "note.md").read_text() == "from PC\n"
    assert "kdeconnect.packet_applied" in result.stdout + result.stderr


def test_apply_takes_precedence_over_sender_settings(
    repo, phone, deliver, make_request, monkeypatch
):
    (repo / "note.md").write_text("PC edit\n")
    deliver()
    context = replace(make_request("--kdeconnect-apply").context, path=str(phone))
    monkeypatch.setattr(
        "demon_lucy.modules.kdeconnect_sync.sync_repo",
        lambda _: pytest.fail("receiver tried sending"),
    )
    KdeconnectSync().modified(context, System([], []))
    assert (phone / "note.md").read_text() == "PC edit\n"


@pytest.mark.parametrize(
    "unsafe_path", ["../outside", ".git/config", ".demon_lucy/patch_queue/evil"]
)
def test_unsafe_actual_patch_is_rejected_even_when_metadata_lies(
    repo, phone, incoming, deliver, receive, unsafe_path
):
    (repo / "note.md").write_text("PC edit\n")
    packet = deliver()
    patch = (incoming / Path(packet.patch_path).name).read_bytes()
    patch = patch.replace(b"note.md", unsafe_path.encode())
    (incoming / Path(packet.patch_path).name).write_bytes(patch)
    metadata_path = incoming / Path(packet.metadata_path).name
    metadata = json.loads(metadata_path.read_text())
    metadata["sha256"] = hashlib.sha256(patch).hexdigest()
    metadata_path.write_text(json.dumps(metadata))
    original_config = (phone / ".git/config").read_bytes()
    with pytest.raises((GitPacketError, ValueError)):
        receive()
    assert (phone / ".git/config").read_bytes() == original_config
    assert not (phone.parent / "outside").exists()
    assert not (incoming.parent / "evil").exists()
    assert not (phone / "note.md").exists()


def test_ambiguous_incoming_branches_are_rejected_before_apply(
    repo, phone, git, deliver, receive
):
    (repo / "note.md").write_text("base\n")
    base = deliver().commit
    receive()
    (repo / "note.md").write_text("branch one\n")
    deliver()
    git("switch", "-c", "second", base)
    (repo / "note.md").write_text("branch two\n")
    deliver()
    with pytest.raises(ValueError, match="multiple incoming"):
        receive()
    assert (phone / "note.md").read_text() == "base\n"


@pytest.mark.parametrize("index_flag", ["--assume-unchanged", "--skip-worktree"])
def test_hidden_local_edits_are_not_overwritten(
    repo, phone, git, deliver, receive, index_flag
):
    (repo / "note.md").write_text("base\n")
    deliver()
    receive()
    git("update-index", index_flag, "note.md", cwd=phone)
    (phone / "note.md").write_text("hidden phone edit\n")
    (repo / "note.md").write_text("PC edit\n")
    deliver()
    with pytest.raises(ValueError, match="not supported"):
        receive()
    assert (phone / "note.md").read_text() == "hidden phone edit\n"


def test_missing_author_identity_fails_before_changing_notes(
    repo, phone, git, deliver, receive
):
    (repo / "note.md").write_text("PC edit\n")
    deliver()
    git("config", "user.name", "", cwd=phone)
    with pytest.raises(GitPacketError):
        receive()
    assert not (phone / "note.md").exists()
    assert git("for-each-ref", cwd=phone) == ""


def test_apply_failure_notifies_once_and_propagates_to_cli(
    repo, phone, deliver, make_request, notifications
):
    (repo / "note.md").write_text("PC edit\n")
    deliver()
    (phone / "note.md").write_text("phone edit\n")
    context = replace(
        make_request("--kdeconnect-apply").context, path=str(phone), run_mode="cli"
    )
    with pytest.raises(LocalChangesError):
        KdeconnectSync().cli(context, System([], []))
    assert len(notifications) == 1
    assert notifications[0]["name"] == f"kdeconnect-sync:{phone}"


def test_receive_refs_are_private_to_linked_worktrees(
    repo, phone, tmp_path, git, deliver, receive, make_request
):
    (repo / "note.md").write_text("base\n")
    packet = deliver()
    git("fetch", str(repo), "main", cwd=phone)
    git("merge", "--ff-only", "FETCH_HEAD", cwd=phone)
    linked = tmp_path / "linked-phone"
    git("worktree", "add", "-b", "linked", str(linked), cwd=phone)
    receiver = PacketReceiver(
        GitPacketRepository(str(phone), 10),
        str(phone / ".demon_lucy/patch_queue"),
        "test",
    )
    receive()
    ref = receiver.apply_ref(packet.commit)
    assert git("for-each-ref", ref, cwd=phone)
    assert git("for-each-ref", ref, cwd=linked) == ""


def test_merge_delta_can_be_received(repo, phone, git, deliver, receive):
    (repo / "note.md").write_text("base\n")
    deliver()
    receive()
    git("switch", "-c", "feature")
    (repo / "feature.md").write_text("feature\n")
    git("add", ".")
    git("commit", "-m", "feature")
    git("switch", "main")
    git("merge", "--no-ff", "feature", "-m", "merge")
    deliver(git("rev-parse", "HEAD"))
    assert receive().applied == 1
    assert (phone / "feature.md").read_text() == "feature\n"


def test_recovery_finishes_receipt_when_branch_already_advanced(
    repo, phone, git, deliver, receive, monkeypatch
):
    (repo / "note.md").write_text("PC edit\n")
    deliver()
    original = GitPacketRepository.run
    failed = False

    def interrupt(self, arguments, **kwargs):
        nonlocal failed
        if arguments == ["update-ref", "--stdin"] and not failed:
            failed = True
            update = kwargs["input_bytes"].decode().splitlines()[1].split()
            original(self, ["update-ref", *update[1:]])
            raise OSError("interrupted after advancing branch")
        return original(self, arguments, **kwargs)

    monkeypatch.setattr(GitPacketRepository, "run", interrupt)
    with pytest.raises(OSError):
        receive()
    head = git("rev-parse", "HEAD", cwd=phone)
    assert receive().applied == 1
    assert git("rev-parse", "HEAD", cwd=phone) == head
    assert receive().skipped == 1


def test_existing_ignored_destination_is_not_overwritten(repo, phone, deliver, receive):
    (repo / "note.md").write_text("PC edit\n")
    deliver()
    with (phone / ".git/info/exclude").open("a") as handle:
        handle.write("\n/note.md\n")
    (phone / "note.md").write_text("ignored phone content\n")
    with pytest.raises(GitPacketError):
        receive()
    assert (phone / "note.md").read_text() == "ignored phone content\n"
