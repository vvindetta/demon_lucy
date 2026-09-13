from __future__ import annotations

from pathlib import Path

import pytest

from demon_lucy.modules.kdeconnect_sync.git_packets import (
    CommitPatch,
    GitPacketRepository,
)
from demon_lucy.modules.kdeconnect_sync.queue import (
    ensure_queue_excluded_in_repo,
    load_packet,
    pending_packets,
    publish_packet,
)


def test_exclude_is_literal_anchored_and_idempotent(repo, git):
    repository = GitPacketRepository(str(repo), 10)
    directory = "queue [pc]"
    ensure_queue_excluded_in_repo(repository, directory)
    exclude = (repo / ".git/info/exclude").read_bytes()
    ensure_queue_excluded_in_repo(repository, directory)
    assert (repo / ".git/info/exclude").read_bytes() == exclude
    assert (
        git("check-ignore", f"{directory}/packet.patch") == f"{directory}/packet.patch"
    )


def test_tracked_queue_is_rejected_before_excluding(repo, git):
    (repo / "existing").mkdir()
    (repo / "existing/note.md").write_text("keep tracking me")
    git("add", ".")
    original = (repo / ".git/info/exclude").read_bytes()
    with pytest.raises(ValueError, match="tracked"):
        ensure_queue_excluded_in_repo(GitPacketRepository(str(repo), 10), "existing")
    assert (repo / ".git/info/exclude").read_bytes() == original


def test_queue_parent_chain_order_not_commit_name_or_time(tmp_path):
    first = publish_packet(
        str(tmp_path), CommitPatch("f" * 40, "", (), b"first", 9), "pc"
    )
    second = publish_packet(
        str(tmp_path), CommitPatch("a" * 40, first.commit, (), b"second", 1), "pc"
    )
    assert pending_packets(str(tmp_path), second.commit, "", first.commit) == [
        first,
        second,
    ]
    assert pending_packets(
        str(tmp_path), second.commit, first.commit, first.commit
    ) == [second]


def test_missing_first_packet_is_not_silently_skipped(tmp_path):
    first = "f" * 40
    second = publish_packet(
        str(tmp_path), CommitPatch("a" * 40, first, (), b"second", 1), "pc"
    )
    with pytest.raises(ValueError, match="missing"):
        pending_packets(str(tmp_path), second.commit, "", first)


def test_symlink_packet_is_rejected(tmp_path):
    packet = publish_packet(
        str(tmp_path), CommitPatch("f" * 40, "", (), b"patch", 1), "pc"
    )
    Path(packet.patch_path).rename(tmp_path / "original")
    Path(packet.patch_path).symlink_to(tmp_path / "original")
    with pytest.raises(ValueError, match="symlink"):
        load_packet(str(tmp_path), packet.commit)
