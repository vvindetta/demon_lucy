from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from demon_lucy.lib.git_state import locked_git_repo
from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.path import path_inside_no_symlinks, path_is_inside
from demon_lucy.modules.abstract_module import Context, System
from demon_lucy.modules.kdeconnect_sync.config import ApplySettings
from demon_lucy.modules.kdeconnect_sync.git_packets import (
    GitPacketError,
    GitPacketRepository,
)
from demon_lucy.modules.kdeconnect_sync.patch_apply import (
    prepare_patch_tree,
    validate_patch_path,
)
from demon_lucy.modules.kdeconnect_sync.queue import (
    Packet,
    ensure_queue_excluded_in_repo,
    load_packet,
    queue_root_for_repo,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApplyResult:
    applied: int = 0
    skipped: int = 0
    pending: int = 0


class LocalChangesError(GitPacketError):
    pass


class PacketReceiver:
    """A single receive stream, called only while holding Lucy's repository lock."""

    def __init__(
        self,
        repository: GitPacketRepository,
        queue_root: str,
        event_id: str,
        *,
        ignored_paths: tuple[str, ...] = (),
    ):
        self.repository = repository
        self.queue_root = queue_root
        self.incoming = path_inside_no_symlinks(queue_root, "incoming_pc_to_phone")
        self.event_id = event_id
        self.ignored_paths = ignored_paths
        key = hashlib.sha256(
            os.path.relpath(queue_root, repository.root).encode()
        ).hexdigest()[:24]
        self.reference = f"refs/worktree/demon-lucy/kdeconnect-received/{key}"
        self.pending_ref = f"{self.reference}/pending"

    def packets(self) -> list[Packet]:
        if not Path(self.incoming).exists():
            return []
        packets = []
        for path in sorted(Path(self.incoming).glob("*.json")):
            packet = load_packet(self.incoming, path.stem)
            for relative in packet.paths:
                validate_patch_path(
                    self.repository.root, relative, excluded_directory=self.queue_root
                )
                if any(
                    value.strip()
                    and path_is_inside(str(Path(self.repository.root, relative)), value)
                    for value in self.ignored_paths
                ):
                    raise ValueError(
                        f"packet targets a path excluded by --sys-ignore-paths: {relative}"
                    )
            packets.append(packet)
        return packets

    def require_clean(self, *, staged: bool = True) -> None:
        repository = self.repository
        entries = repository.run(["ls-files", "-v", "-z"]).split(b"\0")
        if any(entry[:1] == b"S" or entry[:1].islower() for entry in entries if entry):
            raise ValueError(
                "sparse/skip-worktree and assume-unchanged entries are not supported for receiving"
            )
        if repository.run(
            ["diff", "--name-only", "--no-ext-diff", "--no-textconv", "-z"]
        ):
            raise LocalChangesError(
                "local note edits are present; commit or resolve them before applying"
            )
        if staged and repository.run(["diff", "--cached", "--name-only", "-z"]):
            raise LocalChangesError(
                "staged local changes are present; commit or resolve them before applying"
            )
        untracked = repository.run(["ls-files", "--others", "--exclude-standard", "-z"])
        if any(
            not path_is_inside(
                str(Path(repository.root, os.fsdecode(path))), self.queue_root
            )
            for path in untracked.split(b"\0")
            if path
        ):
            raise LocalChangesError(
                "untracked local notes are present; commit or move them before applying"
            )

    def apply_ref(self, origin: str) -> str:
        return f"{self.reference}/applied/{origin}"

    def current_branch(self) -> str:
        return (
            self.repository.run(
                ["symbolic-ref", "--quiet", "HEAD"], allowed_returncodes=(0, 1)
            )
            .decode()
            .strip()
            or "HEAD"
        )

    def content(self, packet: Packet) -> bytes:
        # Read one immutable snapshot; never apply a file reopened after validation.
        path = path_inside_no_symlinks(self.incoming, f"{packet.commit}.patch")
        content = Path(path).read_bytes()
        if hashlib.sha256(content).hexdigest() != packet.digest:
            raise ValueError(f"packet changed after validation: {packet.commit}")
        return content

    def prepare(self, packet: Packet) -> str:
        repository = self.repository
        self.require_clean()
        head = repository.head_commit()
        if packet.parent != head and (
            not head
            or not packet.parent
            or repository.read_ref(self.apply_ref(packet.parent)) != head
        ):
            raise ValueError(
                "repository moved away from the packet's base before applying"
            )
        branch = self.current_branch()
        prepared = prepare_patch_tree(
            repository,
            base=head,
            content=self.content(packet),
            excluded_directory=self.queue_root,
        )
        if set(prepared.paths) != set(packet.paths):
            raise ValueError("packet path list does not match the actual patch")
        message = "Lucy: apply KDE Connect packet\n\n" + json.dumps(
            {"origin": packet.commit, "sha256": packet.digest, "branch": branch},
            sort_keys=True,
        )
        arguments = [
            "-c",
            "commit.gpgsign=false",
            "commit-tree",
            prepared.tree,
            "-m",
            message,
        ]
        if head:
            arguments.extend(["-p", head])
        commit = repository.run(arguments).decode().strip()
        # The prepared commit is the durable journal and pins all result objects.
        # Worktree writes start only after this ref is durable.
        repository.update_ref(self.pending_ref, commit, expected="")
        return commit

    def complete(self, commit: str, packets: list[Packet]) -> None:
        repository = self.repository
        parts = (
            repository.run(["rev-list", "--parents", "-n", "1", commit])
            .decode()
            .split()
        )
        if len(parts) not in {1, 2}:
            raise ValueError("invalid prepared receive commit")
        base = parts[1] if len(parts) == 2 else ""
        raw_message = repository.run(["show", "-s", "--format=%B", commit]).decode()
        prefix = "Lucy: apply KDE Connect packet\n\n"
        if not raw_message.startswith(prefix):
            raise ValueError("invalid receive journal")
        journal = json.loads(raw_message[len(prefix) :])
        if (
            not isinstance(journal, dict)
            or not isinstance(journal.get("origin"), str)
            or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", journal["origin"]) is None
        ):
            raise ValueError("invalid receive journal origin")
        branch = journal.get("branch")
        if branch != self.current_branch():
            raise ValueError(
                "branch changed during an interrupted apply; return to the original branch first"
            )
        head = repository.head_commit()
        if head not in {base, commit}:
            raise ValueError(
                "HEAD changed during an interrupted apply; reconcile the prepared receive commit manually"
            )
        tree = repository.run(["rev-parse", f"{commit}^{{tree}}"]).decode().strip()
        index_tree = repository.run(["write-tree"]).decode().strip()
        if index_tree != tree:
            if head == commit:
                raise ValueError(
                    "HEAD was finalized but the index no longer matches the received result"
                )
            # Either no files were changed yet, or a partial IO failure occurred.
            # Only retry from the original clean state; never reset user content.
            self.require_clean()
            packet = next(
                (item for item in packets if item.commit == journal["origin"]), None
            )
            if packet is None or packet.digest != journal.get("sha256"):
                raise ValueError(
                    "original packet is missing or changed for an interrupted apply"
                )
            content = self.content(packet)
            prepared = prepare_patch_tree(
                repository,
                base=base,
                content=content,
                excluded_directory=self.queue_root,
            )
            if prepared.tree != tree or set(prepared.paths) != set(packet.paths):
                raise ValueError("packet no longer matches the prepared receive commit")
            if prepared.patch:
                repository.run(
                    ["apply", "--index", "--check", "--whitespace=nowarn", "-"],
                    input_bytes=prepared.patch,
                )
                repository.run(
                    ["apply", "--index", "--whitespace=nowarn", "-"],
                    input_bytes=prepared.patch,
                )
        self.require_clean(staged=False)
        if repository.run(["write-tree"]).decode().strip() != tree:
            raise ValueError(
                "working index does not match the prepared receive result; no progress recorded"
            )
        if self.current_branch() != branch:
            raise ValueError("branch changed while applying; no progress recorded")
        # Commit progress and deduplication together. A crash before this transaction
        # is recovered from the prepared ref and exact index/worktree checks above.
        zero = "0" * len(commit)
        receipt = self.apply_ref(journal["origin"])
        recorded = repository.read_ref(receipt)
        if recorded and recorded != commit:
            raise ValueError("receive receipt conflicts with the prepared commit")
        receipt_command = (
            f"verify {receipt} {commit}\n"
            if recorded
            else f"create {receipt} {commit}\n"
        )
        commands = (
            "start\n"
            f"update {branch} {commit} {head or zero}\n"
            f"{receipt_command}"
            f"delete {self.pending_ref} {commit}\n"
            "prepare\ncommit\n"
        )
        repository.run(["update-ref", "--stdin"], input_bytes=commands.encode())
        logger.info(
            log_record(
                "kdeconnect.packet_applied",
                id=self.event_id,
                repo=repository.root,
                patch_id=journal["origin"],
                commit=commit,
            )
        )

    def run(self) -> ApplyResult:
        repository = self.repository
        packets = self.packets()
        applied = skipped = 0
        prepared = repository.read_ref(self.pending_ref)
        if prepared:
            self.complete(prepared, packets)
            applied += 1
        prefix = f"{self.reference}/applied/"
        receipts = {
            name.removeprefix(prefix): commit
            for name, commit in (
                line.split()
                for line in repository.run(
                    ["for-each-ref", "--format=%(refname) %(objectname)", prefix]
                )
                .decode()
                .splitlines()
            )
        }
        head = repository.head_commit()
        history = (
            set(repository.run(["rev-list", head]).decode().splitlines())
            if head
            else set()
        )
        remaining = []
        for packet in packets:
            if packet.commit in receipts:
                skipped += 1
            elif packet.commit in history:
                # Normal Git sync may already have delivered this exact commit.
                repository.update_ref(
                    self.apply_ref(packet.commit), packet.commit, expected=""
                )
                receipts[packet.commit] = packet.commit
                skipped += 1
            else:
                remaining.append(packet)
        packets = remaining
        while packets:
            candidates = [
                packet
                for packet in packets
                if packet.parent == head
                or (head and packet.parent and receipts.get(packet.parent) == head)
            ]
            if not candidates:
                break
            if len(candidates) > 1:
                raise ValueError(
                    "multiple incoming packets extend the same base; select the intended stream manually"
                )
            packet = candidates[0]
            commit = self.prepare(packet)
            self.complete(commit, packets)
            head = commit
            receipts[packet.commit] = commit
            packets.remove(packet)
            applied += 1
        if packets:
            logger.warning(
                log_record(
                    "kdeconnect.apply_wait",
                    id=self.event_id,
                    repo=repository.root,
                    reason="missing_base",
                    pending=len(packets),
                    message="waiting for predecessor packets or the matching Git base; no forced apply",
                )
            )
        return ApplyResult(applied, skipped, len(packets))


def apply_incoming(
    repo_root: str,
    ctx: Context,
    system: System,
) -> ApplyResult:
    settings = ApplySettings.from_args(ctx.args)
    repository = GitPacketRepository(repo_root, settings.timeout_seconds)
    queue_root = queue_root_for_repo(repo_root, settings.queue_directory)
    receiver = PacketReceiver(
        repository,
        queue_root,
        ctx.event_id,
        ignored_paths=tuple(ctx.args.require("sys-ignore-paths").value),
    )
    if settings.dry_run:
        packets = receiver.packets()
        logger.info(
            log_record(
                "kdeconnect.apply_preview",
                id=ctx.event_id,
                repo=repo_root,
                ready_packets=len(packets),
            )
        )
        return ApplyResult(pending=len(packets))
    with locked_git_repo(
        repo_root,
        wait_timeout_seconds=ctx.args.require(
            "sys-git-repo-lock-wait-timeout-seconds"
        ).value,
        retry_sleep_seconds=ctx.args.require(
            "sys-git-repo-lock-retry-sleep-seconds"
        ).value,
        stale_seconds=ctx.args.require("sys-git-repo-lock-stale-seconds").value,
        operating_system=system.operating_system,
        event_id=ctx.event_id,
    ):
        repository.require_idle()
        ensure_queue_excluded_in_repo(repository, settings.queue_directory)
        result = receiver.run()
    logger.info(
        log_record(
            "kdeconnect.apply_done",
            id=ctx.event_id,
            repo=repo_root,
            applied=result.applied,
            skipped=result.skipped,
            pending=result.pending,
        )
    )
    return result
