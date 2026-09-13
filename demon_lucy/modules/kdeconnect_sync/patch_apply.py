"""Prepare incoming patches in an isolated index before touching the working tree."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from demon_lucy.lib.path import (
    canonical_path,
    find_parent_git_repo,
    path_inside_no_symlinks,
    path_is_inside,
)
from demon_lucy.modules.kdeconnect_sync.git_packets import GitPacketRepository


@dataclass(frozen=True)
class PreparedTree:
    tree: str
    paths: tuple[str, ...]
    patch: bytes


def validate_patch_path(root: str, path: str, *, excluded_directory: str) -> None:
    if (
        not path
        or "\0" in path
        or "\\" in path
        or ":" in path.split("/")[0]
        or any(
            part in {"", ".", ".."} or part.casefold() == ".git"
            for part in path.split("/")
        )
    ):
        raise ValueError(f"unsafe patch path: {path!r}")
    target = path_inside_no_symlinks(root, path)
    if path_is_inside(target, excluded_directory):
        raise ValueError(f"patch must not modify its queue: {path!r}")
    owner = find_parent_git_repo(target)
    if owner is None or canonical_path(owner) != canonical_path(root):
        raise ValueError(f"patch crosses a nested repository: {path!r}")


def prepare_patch_tree(
    repository: GitPacketRepository,
    *,
    base: str,
    content: bytes,
    excluded_directory: str,
) -> PreparedTree:
    """Apply only to a temporary index; reject links, submodules and unsafe paths."""
    with tempfile.TemporaryDirectory(prefix="lucy-patch-index-") as temporary:
        index = Path(temporary, "index")
        repository.run(
            ["read-tree", base] if base else ["read-tree", "--empty"], index_path=index
        )
        before = repository.run(["write-tree"], index_path=index).decode().strip()
        if content:
            repository.run(
                ["apply", "--cached", "--whitespace=nowarn", "-"],
                input_bytes=content,
                index_path=index,
            )
        raw = repository.run(
            [
                "diff",
                "--cached",
                "--raw",
                "--no-abbrev",
                "--no-renames",
                "-z",
                before,
                "--",
            ],
            index_path=index,
        ).split(b"\0")
        paths = []
        for position in range(0, len(raw) - 1, 2):
            modes = raw[position].split()
            if modes[0] not in {b":000000", b":100644", b":100755"} or modes[1] not in {
                b"000000",
                b"100644",
                b"100755",
            }:
                raise ValueError(
                    "patches may only change regular files, not symlinks or submodules"
                )
            path = os.fsdecode(raw[position + 1])
            validate_patch_path(
                repository.root, path, excluded_directory=excluded_directory
            )
            paths.append(path)
        tree = repository.run(["write-tree"], index_path=index).decode().strip()
        # Publish only Git's canonical net change. Do not replay arbitrary
        # intermediate operations from a received multi-diff document on disk.
        patch = repository.run(
            [
                "diff",
                "--cached",
                "--binary",
                "--full-index",
                "--no-ext-diff",
                "--no-textconv",
                "--no-renames",
                before,
                "--",
            ],
            index_path=index,
        )
        return PreparedTree(tree, tuple(paths), patch)
