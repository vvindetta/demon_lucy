from __future__ import annotations

import logging
import platform
import threading
import time

from demon_lucy.lib.args.models import ParsedArgs, Template
from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.notifications import safe_notify
from demon_lucy.lib.path import (
    abs_expand_path,
    find_parent_git_repo,
    path_has_component,
)
from demon_lucy.lib.operating_system import OperatingSystem
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)
from demon_lucy.modules.git.worker import build_patch_packet, commit_dirty_tree
from demon_lucy.modules.kdeconnect_sync.config import KDECONNECT_SYNC_TEMPLATE
from demon_lucy.modules.kdeconnect_sync.queue import (
    ensure_queue_excluded_in_repo,
    is_queue_internal_path,
    outgoing_pc_to_phone_dir,
)
from demon_lucy.modules.kdeconnect_sync.transport import (
    transfer_packet_to_phone,
)

logger = logging.getLogger(__name__)

_COALESCE_GUARD = threading.Lock()
_COALESCE_TOKENS: dict[str, int] = {}


def _repo_sync_notify(
    *,
    repo_root: str,
    args: ParsedArgs,
    summary_text: str,
    details_text: str = "",
    action: str = "kdeconnect.sync_failed",
) -> None:
    logger.error(
        log_record(
            action,
            repo=repo_root,
            summary=summary_text,
            error=details_text[:1200] if details_text else None,
        )
    )
    message_text = f"Repository:\n{repo_root}\n\n{summary_text}"
    if details_text:
        message_text += f"\n\nDetails:\n{details_text[:1200]}"
    safe_notify(
        name=f"kdeconnect-sync:{repo_root}",
        message=message_text,
        args=args,
        use_rare_mode=True,
    )


def _transfer_error_requires_notification(error_text: str) -> bool:
    normalized = error_text.strip().lower()
    return normalized in {
        "empty kdeconnect device id",
        "empty kdeconnect remote root",
    }


def _author_device_name() -> str:
    host_name = platform.node().strip()
    if host_name:
        return host_name
    return "unknown-device"


class KdeconnectSync(AbstractModule):
    name: str = "kdeconnect_sync"
    priority: int = 49
    template: Template = KDECONNECT_SYNC_TEMPLATE

    def created(self, ctx: Context, system: System) -> IgnoreMap | None:
        return self._handle(ctx, system, "created")

    def modified(self, ctx: Context, system: System) -> IgnoreMap | None:
        return self._handle(ctx, system, "modified")

    def moved(self, ctx: Context, system: System) -> IgnoreMap | None:
        return self._handle(ctx, system, "moved")

    def deleted(self, ctx: Context, system: System) -> IgnoreMap | None:
        return self._handle(ctx, system, "deleted")

    def _handle(
        self, ctx: Context, system: System, event_type: str
    ) -> IgnoreMap | None:
        if not ctx.args.require("kdeconnect-sync").value:
            return None

        event = ctx.event
        if event is None:
            return None
        src_path = abs_expand_path(event.src_path)
        dest_path_value = getattr(event, "dest_path", "")
        dest_path = abs_expand_path(dest_path_value) if dest_path_value else ""

        if (src_path and path_has_component(src_path, ".git")) or (
            dest_path and path_has_component(dest_path, ".git")
        ):
            return None

        repo_root = find_parent_git_repo(ctx.path) or find_parent_git_repo(
            dest_path or src_path
        )
        if not repo_root:
            return None

        queue_dir_name = ctx.args.require("kdeconnect-patch-queue-dir").value.strip()
        if not queue_dir_name:
            _repo_sync_notify(
                repo_root=repo_root,
                args=ctx.args,
                summary_text="kdeconnect patch queue path is empty.",
            )
            return None

        for path_text in [src_path, dest_path]:
            if path_text and is_queue_internal_path(
                path_text, repo_root, queue_dir_name
            ):
                return None

        if ctx.run_mode == "oneshot":
            self._run_repo_sync(
                repo_root=repo_root,
                event_type=event_type,
                trigger_paths=[path for path in [src_path, dest_path] if path],
                args=ctx.args,
                operating_system=system.operating_system,
            )
            return None

        self._schedule_repo_sync(
            repo_root=repo_root,
            event_type=event_type,
            trigger_paths=[path for path in [src_path, dest_path] if path],
            args=ctx.args,
            operating_system=system.operating_system,
        )
        return None

    def _schedule_repo_sync(
        self,
        *,
        repo_root: str,
        event_type: str,
        trigger_paths: list[str],
        args: ParsedArgs,
        operating_system: OperatingSystem,
    ) -> None:
        token = time.monotonic_ns()
        with _COALESCE_GUARD:
            _COALESCE_TOKENS[repo_root] = token

        def _run_after_delay() -> None:
            delay_seconds = (
                max(
                    0,
                    args.require("kdeconnect-patch-coalesce-milliseconds").value,
                )
                / 1000
            )
            if delay_seconds > 0.0:
                time.sleep(delay_seconds)
            with _COALESCE_GUARD:
                if _COALESCE_TOKENS.get(repo_root) != token:
                    return
            self._run_repo_sync(
                repo_root=repo_root,
                event_type=event_type,
                trigger_paths=trigger_paths,
                args=args,
                operating_system=operating_system,
            )

        threading.Thread(target=_run_after_delay, daemon=True).start()

    def _run_repo_sync(
        self,
        *,
        repo_root: str,
        event_type: str,
        trigger_paths: list[str],
        args: ParsedArgs,
        operating_system: OperatingSystem,
    ) -> None:
        try:
            ensure_queue_excluded_in_repo(
                repo_root=repo_root,
                queue_dir_name=args.require("kdeconnect-patch-queue-dir").value,
            )
        except OSError as exc:
            _repo_sync_notify(
                repo_root=repo_root,
                args=args,
                summary_text="failed to configure local git exclude for patch queue.",
                details_text=str(exc),
            )
            return

        commit_result = commit_dirty_tree(
            self,
            repo_root=repo_root,
            event_type=event_type,
            paths=trigger_paths,
            args=args,
            operating_system=operating_system,
        )
        if commit_result.status == "noop":
            return
        if commit_result.status == "busy":
            logger.info(
                log_record(
                    "kdeconnect.sync_skip",
                    reason="repo_busy",
                    stage="commit",
                    repo=repo_root,
                )
            )
            return
        if commit_result.status != "committed":
            _repo_sync_notify(
                repo_root=repo_root,
                args=args,
                summary_text="failed to create commit for patch sync.",
                details_text=commit_result.error_text,
            )
            return

        queue_dir_name = args.require("kdeconnect-patch-queue-dir").value
        outgoing_dir = outgoing_pc_to_phone_dir(
            repo_root=repo_root, queue_dir_name=queue_dir_name
        )
        packet_result = build_patch_packet(
            self,
            repo_root=repo_root,
            commit_sha=commit_result.commit_sha,
            changed_paths=list(commit_result.changed_paths),
            queue_dir=outgoing_dir,
            author_device=_author_device_name(),
            args=args,
            operating_system=operating_system,
        )
        if packet_result.status == "busy":
            logger.info(
                log_record(
                    "kdeconnect.sync_skip",
                    reason="repo_busy",
                    stage="build_patch",
                    repo=repo_root,
                )
            )
            return
        if packet_result.status != "built":
            _repo_sync_notify(
                repo_root=repo_root,
                args=args,
                summary_text="failed to build patch packet.",
                details_text=packet_result.error_text,
            )
            return

        if args.require("kdeconnect-dry-run").value:
            return

        transfer_result = transfer_packet_to_phone(
            device_id=args.require("kdeconnect-device-id").value.strip(),
            remote_root=args.require("kdeconnect-remote-root").value.strip(),
            queue_dir_name=queue_dir_name,
            packet_paths=[packet_result.patch_path, packet_result.metadata_path],
            timeout_seconds=args.require("kdeconnect-command-timeout-seconds").value,
            mount_retry_seconds=args.require("kdeconnect-mount-retry-seconds").value,
            max_retries=args.require("kdeconnect-patch-max-retries").value,
        )
        if transfer_result.status == "sent":
            logger.info(
                log_record(
                    "kdeconnect.packet_sent",
                    repo=repo_root,
                    patch_id=packet_result.patch_id,
                    remote_dir=transfer_result.remote_incoming_dir,
                )
            )
            return

        if _transfer_error_requires_notification(transfer_result.error_text):
            _repo_sync_notify(
                repo_root=repo_root,
                args=args,
                summary_text="kdeconnect transfer is misconfigured.",
                details_text=transfer_result.error_text,
                action="kdeconnect.transfer_failed",
            )
            return

        logger.warning(
            log_record(
                "kdeconnect.transfer_failed",
                repo=repo_root,
                error=transfer_result.error_text[:1200],
            )
        )
