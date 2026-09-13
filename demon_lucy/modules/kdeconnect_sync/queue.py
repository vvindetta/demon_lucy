from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from demon_lucy.lib.path import path_inside_no_symlinks, path_is_inside
from demon_lucy.lib.text_file import write_bytes_atomic, write_text_atomic
from demon_lucy.modules.kdeconnect_sync.config import (
    SyncSettings,
    validate_relative_directory,
)
from demon_lucy.modules.kdeconnect_sync.git_packets import (
    CommitPatch,
    GitPacketRepository,
)


def queue_root_for_repo(repo_root: str, queue_dir_name: str) -> str:
    return path_inside_no_symlinks(
        repo_root, validate_relative_directory(queue_dir_name)
    )


def is_queue_internal_path(
    path_value: str, repo_root: str, queue_dir_name: str
) -> bool:
    return path_is_inside(path_value, queue_root_for_repo(repo_root, queue_dir_name))


def destination_key(settings: SyncSettings) -> str:
    identity = "\0".join(
        (settings.device_id, settings.remote_root, settings.queue_directory)
    )
    return hashlib.sha256(identity.encode()).hexdigest()[:24]


def ensure_queue_excluded_in_repo(
    repository: GitPacketRepository, queue_dir_name: str
) -> None:
    queue_dir_name = validate_relative_directory(queue_dir_name)
    if repository.run(["ls-files", "-z", "--", queue_dir_name]):
        raise ValueError(
            "patch queue contains tracked files; choose an untracked queue directory"
        )
    exclude = repository.git_path("info/exclude")
    if exclude.is_symlink() or exclude.parent.is_symlink():
        raise ValueError("Git exclude path must not be a symlink")
    exclude.parent.mkdir(parents=True, exist_ok=True)
    escaped = "".join(
        "\\" + char if char in "\\*?[] " else char for char in queue_dir_name
    )
    pattern = f"/{escaped}/"
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if pattern in existing.splitlines():
        return
    separator = "\n" if existing and not existing.endswith("\n") else ""
    write_text_atomic(str(exclude), existing + separator + pattern + "\n")


@dataclass(frozen=True)
class Packet:
    patch_path: str
    metadata_path: str
    commit: str
    parent: str
    digest: str
    paths: tuple[str, ...] = ()


def publish_packet(directory: str, patch: CommitPatch, author_device: str) -> Packet:
    patch_path = path_inside_no_symlinks(directory, f"{patch.commit}.patch")
    metadata_path = path_inside_no_symlinks(directory, f"{patch.commit}.json")
    digest = hashlib.sha256(patch.content).hexdigest()
    metadata = {
        "version": 1,
        "format": "git-diff-binary",
        "patch_id": patch.commit,
        "origin_commit": patch.commit,
        "base_commit": patch.parent,
        "path_list": list(patch.paths),
        "sha256": digest,
        "created_at": patch.timestamp,
        "author_device": author_device,
    }
    # Metadata is the ready marker: publish it only after the complete patch.
    write_bytes_atomic(patch_path, patch.content)
    write_text_atomic(
        metadata_path, json.dumps(metadata, ensure_ascii=True, sort_keys=True) + "\n"
    )
    return Packet(
        patch_path, metadata_path, patch.commit, patch.parent, digest, patch.paths
    )


def load_packet(directory: str, commit: str) -> Packet:
    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit) is None:
        raise ValueError(f"invalid packet commit: {commit}")
    metadata_path = path_inside_no_symlinks(directory, f"{commit}.json")
    patch_path = path_inside_no_symlinks(directory, f"{commit}.patch")
    if not Path(metadata_path).is_file():
        raise ValueError(f"packet metadata is not a regular file: {commit}")
    metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError(f"invalid packet metadata: {commit}")
    parent = metadata.get("base_commit")
    paths = metadata.get("path_list")
    if (
        metadata.get("version") != 1
        or metadata.get("format") != "git-diff-binary"
        or metadata.get("patch_id") != commit
        or metadata.get("origin_commit") != commit
        or not isinstance(parent, str)
        or not isinstance(paths, list)
        or any(not isinstance(path, str) for path in paths)
        or len(set(paths)) != len(paths)
        or (parent and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", parent) is None)
        or not Path(patch_path).is_file()
    ):
        raise ValueError(f"invalid packet metadata: {commit}")
    digest = hashlib.sha256(Path(patch_path).read_bytes()).hexdigest()
    if metadata.get("sha256") != digest:
        raise ValueError(f"packet checksum mismatch: {commit}")
    return Packet(patch_path, metadata_path, commit, parent, digest, tuple(paths))


def pending_packets(directory: str, queued: str, sent: str, first: str) -> list[Packet]:
    """Walk the durable parent chain rather than sorting filenames or timestamps."""
    packets: list[Packet] = []
    cursor = queued
    seen: set[str] = set()
    while cursor and cursor != sent:
        metadata_path = path_inside_no_symlinks(directory, f"{cursor}.json")
        if not Path(metadata_path).exists():
            raise ValueError(f"queued packet is missing: {cursor}")
        packet = load_packet(directory, cursor)
        if packet.commit in seen:
            raise ValueError("cycle in the packet queue")
        seen.add(packet.commit)
        packets.append(packet)
        if not sent and cursor == first:
            break
        cursor = packet.parent
    if sent and cursor != sent:
        raise ValueError("sent commit does not belong to the queued packet chain")
    if not sent and packets and packets[-1].commit != first:
        raise ValueError("packet chain does not reach the first queued commit")
    return list(reversed(packets))
