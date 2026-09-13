"""Local Git snapshots and binary patches for KDE Connect sync.

Callers hold ``locked_git_repo`` across commits, packet publication and ref updates.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from demon_lucy.lib.git_state import GitRepoBusyError


class GitPacketError(Exception):
    pass


@dataclass(frozen=True)
class CommitPatch:
    commit: str
    parent: str
    paths: tuple[str, ...]
    content: bytes
    timestamp: int


@dataclass(frozen=True)
class GitPacketRepository:
    root: str
    timeout_seconds: float

    def run(
        self,
        arguments: list[str],
        *,
        input_bytes: bytes | None = None,
        index_path: Path | None = None,
        allowed_returncodes: tuple[int, ...] = (0,),
    ) -> bytes:
        environment = os.environ.copy()
        for key in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_COMMON_DIR",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        ):
            environment.pop(key, None)
        environment.update(GIT_TERMINAL_PROMPT="0", LC_ALL="C", GIT_OPTIONAL_LOCKS="0")
        if index_path is not None:
            environment["GIT_INDEX_FILE"] = str(index_path)
        result = subprocess.run(
            ["git", "--literal-pathspecs", "-C", self.root, *arguments],
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.timeout_seconds,
            check=False,
            env=environment,
        )
        if result.returncode not in allowed_returncodes:
            error = result.stderr.decode("utf-8", errors="replace").strip()
            if "index.lock" in error:
                raise GitRepoBusyError(error)
            raise GitPacketError(f"git {arguments[0]}: {error}")
        return result.stdout

    def git_path(self, name: str) -> Path:
        value = os.fsdecode(self.run(["rev-parse", "--git-path", name]).rstrip(b"\n"))
        return Path(self.root, value)

    def read_ref(self, name: str) -> str:
        # for-each-ref returns an empty result for a missing ref, but propagates IO errors.
        return (
            self.run(["for-each-ref", "--format=%(objectname)", name]).decode().strip()
        )

    def update_ref(
        self, name: str, commit: str, *, expected: str | None = None
    ) -> None:
        arguments = ["update-ref", name, commit]
        if expected is not None:
            arguments.append(expected or "0" * len(commit))
        self.run(arguments)

    def head_commit(self) -> str:
        return (
            self.run(
                ["rev-parse", "--verify", "--quiet", "HEAD"], allowed_returncodes=(0, 1)
            )
            .decode()
            .strip()
        )

    def require_idle(self) -> None:
        if self.run(["ls-files", "--unmerged", "-z"]):
            raise GitPacketError("resolve unmerged files before sending patches")
        for marker in (
            "MERGE_HEAD",
            "CHERRY_PICK_HEAD",
            "REVERT_HEAD",
            "rebase-merge",
            "rebase-apply",
            "sequencer",
        ):
            if self.git_path(marker).exists():
                raise GitRepoBusyError(f"unfinished Git operation: {marker}")

    def commit_dirty_tree(self, *, message: str) -> str:
        self.require_idle()
        if self.run(["status", "--porcelain=v1", "-z", "--untracked-files=all"]):
            self.run(["add", "-A", "--", "."])
            # Some status entries (e.g. dirty submodules) cannot be staged here.
            if self.run(["diff", "--cached", "--name-only", "-z"]):
                self.run(["commit", "-m", message])
        return self.head_commit()

    def commits_after(self, previous: str, head: str) -> list[str]:
        if not previous:
            return [head]
        history = self.run(["rev-list", "--first-parent", head]).decode().splitlines()
        if previous not in history:
            raise GitPacketError(
                "queued commit is no longer on HEAD's first-parent history; use a new queue after rewriting history"
            )
        return list(reversed(history[: history.index(previous)]))

    def patch_for_commit(self, commit: str) -> CommitPatch:
        parents = (
            self.run(["rev-list", "--parents", "-n", "1", commit]).decode().split()
        )
        parent = parents[1] if len(parents) > 1 else ""
        base_tree = (
            parent
            or self.run(["hash-object", "-w", "-t", "tree", "--stdin"], input_bytes=b"")
            .decode()
            .strip()
        )
        diff_args = ["diff", "--no-ext-diff", "--no-textconv", "--find-renames"]
        content = self.run(
            [*diff_args, "--binary", "--full-index", base_tree, commit, "--"]
        )
        paths = self.run(
            [*diff_args, "--no-renames", "--name-only", "-z", base_tree, commit, "--"]
        )
        timestamp = int(self.run(["show", "-s", "--format=%ct", commit]).strip())
        return CommitPatch(
            commit=commit,
            parent=parent,
            paths=tuple(os.fsdecode(path) for path in paths.split(b"\0") if path),
            content=content,
            timestamp=timestamp,
        )
