from __future__ import annotations

import logging
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

from demon_lucy.modules.kdeconnect_sync.git_packets import (
    GitPacketError,
    GitPacketRepository,
)
from demon_lucy.lib.git_state import GitRepoBusyError, locked_git_repo
from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.notifications import safe_notify
from demon_lucy.lib.operating_system import OperatingSystem
from demon_lucy.lib.path import path_inside_no_symlinks
from demon_lucy.modules.abstract_module import Context
from demon_lucy.modules.kdeconnect_sync.config import SyncSettings
from demon_lucy.modules.kdeconnect_sync.queue import (
    destination_key,
    ensure_queue_excluded_in_repo,
    pending_packets,
    publish_packet,
    queue_root_for_repo,
)
from demon_lucy.modules.kdeconnect_sync.transport import (
    TransferStatus,
    transfer_packet_to_phone,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncRequest:
    repo_root: str
    context: Context
    settings: SyncSettings
    operating_system: OperatingSystem


def report_failure(
    repo: str, context: Context, error: str, *, reason: str, exc_info: bool = False
) -> None:
    logger.error(
        log_record(
            "kdeconnect.sync_failed",
            id=context.event_id,
            repo=repo,
            reason=reason,
            error=error[:1200],
        ),
        exc_info=exc_info,
    )
    safe_notify(
        name=f"kdeconnect-sync:{repo}",
        message=f"KDE Connect sync failed for {repo}: {error[:1200]}",
        args=context.args,
        use_rare_mode=True,
    )


def sync_repo(request: SyncRequest) -> bool:
    """Run one durable sender cycle. True requests a later retry."""
    repo = request.repo_root
    ctx = request.context
    settings = request.settings
    repository = GitPacketRepository(repo, settings.timeout_seconds)
    key = destination_key(settings)
    reference = f"refs/worktree/demon-lucy/kdeconnect/{key}"
    first_ref, queued_ref, sent_ref = (
        f"{reference}/{name}" for name in ("first", "queued", "sent")
    )
    lock_options = {
        "wait_timeout_seconds": ctx.args.require(
            "sys-git-repo-lock-wait-timeout-seconds"
        ).value,
        "retry_sleep_seconds": ctx.args.require(
            "sys-git-repo-lock-retry-sleep-seconds"
        ).value,
        "stale_seconds": ctx.args.require("sys-git-repo-lock-stale-seconds").value,
        "operating_system": request.operating_system,
        "event_id": ctx.event_id,
    }
    try:
        root = queue_root_for_repo(repo, settings.queue_directory)
        directory = path_inside_no_symlinks(root, f"outgoing_pc_to_phone/{key}")
        if settings.dry_run:
            dirty = bool(repository.run(["status", "--porcelain=v1", "-z"]))
            logger.info(
                log_record(
                    "kdeconnect.preview",
                    id=ctx.event_id,
                    repo=repo,
                    dirty=dirty,
                    device=settings.device_id,
                )
            )
            return False

        with locked_git_repo(repo, **lock_options):
            ensure_queue_excluded_in_repo(repository, settings.queue_directory)
            head = repository.commit_dirty_tree(message=settings.commit_message)
            if not head:
                logger.info(
                    log_record(
                        "kdeconnect.sync_skip",
                        id=ctx.event_id,
                        repo=repo,
                        reason="empty_repository",
                    )
                )
                return False
            Path(directory).mkdir(parents=True, exist_ok=True)
            first = repository.read_ref(first_ref)
            if not first:
                first = head
                repository.update_ref(first_ref, first)
            queued = repository.read_ref(queued_ref)
            commits = repository.commits_after(queued or first, head)
            if not queued:
                commits.insert(0, first)
            for commit in commits:
                patch = repository.patch_for_commit(commit)
                packet = publish_packet(directory, patch, platform.node())
                repository.update_ref(queued_ref, commit)
                queued = commit
                logger.info(
                    log_record(
                        "kdeconnect.packet_queued",
                        id=ctx.event_id,
                        repo=repo,
                        patch_id=packet.commit,
                        changed_paths=len(patch.paths),
                    )
                )
            sent = repository.read_ref(sent_ref)
            packets = pending_packets(directory, queued, sent, first)

        # Device IO never holds the lock used by the Git sync module.
        for packet in packets:
            result = transfer_packet_to_phone(settings=settings, packet=packet)
            if result.status is TransferStatus.ERROR:
                report_failure(repo, ctx, result.error_text, reason="transfer_failed")
                return False
            if result.status is TransferStatus.RETRY:
                logger.warning(
                    log_record(
                        "kdeconnect.transfer_wait",
                        id=ctx.event_id,
                        repo=repo,
                        patch_id=packet.commit,
                        error=result.error_text[:1200],
                        reason="pending_transfer",
                        retry_seconds=settings.retry_seconds,
                    )
                )
                return True
            with locked_git_repo(repo, **lock_options):
                # Another daemon/oneshot must not move the sent cursor backwards.
                if repository.read_ref(sent_ref) != sent:
                    raise GitRepoBusyError(
                        "another sender advanced the delivery cursor"
                    )
                repository.update_ref(sent_ref, packet.commit, expected=sent)
                sent = packet.commit
            logger.info(
                log_record(
                    "kdeconnect.packet_sent",
                    id=ctx.event_id,
                    repo=repo,
                    patch_id=packet.commit,
                    dest=result.remote_incoming_dir,
                )
            )
        if not packets:
            logger.info(
                log_record(
                    "kdeconnect.sync_skip",
                    id=ctx.event_id,
                    repo=repo,
                    reason="queue_empty",
                )
            )
        return False
    except GitRepoBusyError as exc:
        logger.info(
            log_record(
                "kdeconnect.sync_wait",
                id=ctx.event_id,
                repo=repo,
                reason="repo_busy",
                error=str(exc)[:1200],
                retry_seconds=settings.retry_seconds,
            )
        )
        return True
    except subprocess.TimeoutExpired as exc:
        logger.warning(
            log_record(
                "kdeconnect.sync_wait",
                id=ctx.event_id,
                repo=repo,
                reason="command_timeout",
                error=exc,
            )
        )
        return True
    except (GitPacketError, OSError, ValueError) as exc:
        report_failure(repo, ctx, str(exc), reason="queue_failed")
        return False
