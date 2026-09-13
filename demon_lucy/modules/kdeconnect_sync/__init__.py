from __future__ import annotations

import logging
import os
import subprocess
import threading
import time

from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.git_state import GitRepoBusyError
from demon_lucy.lib.path import (
    find_parent_git_repo,
    path_has_component,
    path_inside_no_symlinks,
)
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    ModuleResult,
    System,
)
from demon_lucy.modules.kdeconnect_sync.config import (
    KDECONNECT_SYNC_TEMPLATE,
    SyncSettings,
)
from demon_lucy.modules.kdeconnect_sync.git_packets import GitPacketError
from demon_lucy.modules.kdeconnect_sync.queue import (
    is_queue_internal_path,
    queue_root_for_repo,
)
from demon_lucy.modules.kdeconnect_sync.receiver import apply_incoming
from demon_lucy.modules.kdeconnect_sync.worker import (
    SyncRequest,
    report_failure,
    sync_repo,
)

logger = logging.getLogger(__name__)


class KdeconnectSync(AbstractModule):
    name = "kdeconnect_sync"
    priority = 49
    template = KDECONNECT_SYNC_TEMPLATE

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._pending: dict[str, tuple[SyncRequest, float]] = {}
        self._active: set[str] = set()

    def created(self, ctx: Context, system: System) -> ModuleResult | None:
        self._handle(ctx, system)
        return None

    def modified(self, ctx: Context, system: System) -> ModuleResult | None:
        self._handle(ctx, system)
        return None

    def moved(self, ctx: Context, system: System) -> ModuleResult | None:
        self._handle(ctx, system)
        return None

    def deleted(self, ctx: Context, system: System) -> ModuleResult | None:
        self._handle(ctx, system)
        return None

    def cli(self, ctx: Context, system: System) -> ModuleResult | None:
        self._handle(ctx, system)
        return None

    def _handle(self, ctx: Context, system: System) -> None:
        apply_requested = (
            ctx.run_mode != "daemon" and ctx.args.require("kdeconnect-apply").value
        )
        repo = find_parent_git_repo(ctx.path)
        if ctx.event is not None and ctx.event.event_type in {
            "created",
            "modified",
            "moved",
        }:
            event_path = os.fsdecode(
                ctx.event.dest_path
                if ctx.event.event_type == "moved"
                else ctx.event.src_path
            )
            name = os.path.basename(event_path)
            if (
                not ctx.event.is_directory
                and name.endswith(".json")
                and not name.startswith(".")
            ):
                repo = find_parent_git_repo(event_path)
                if repo is not None:
                    try:
                        queue = queue_root_for_repo(
                            repo, ctx.args.require("kdeconnect-patch-queue-dir").value
                        )
                        incoming = path_inside_no_symlinks(
                            queue, "incoming_pc_to_phone"
                        )
                        # The sender publishes this ready marker after the complete patch.
                        # Receiving runs on Lucy's existing event thread, not a worker.
                        apply_requested |= (
                            os.path.dirname(os.path.abspath(event_path)) == incoming
                        )
                    except (OSError, ValueError) as exc:
                        report_failure(repo, ctx, str(exc), reason="invalid_path")
                        return
        if apply_requested:
            try:
                if repo is None:
                    raise ValueError(
                        "--kdeconnect-apply requires a local Git repository"
                    )
                apply_incoming(repo, ctx, system)
            except (GitRepoBusyError, subprocess.TimeoutExpired) as exc:
                logger.warning(
                    log_record(
                        "kdeconnect.apply_wait",
                        id=ctx.event_id,
                        repo=repo,
                        reason="retry_on_next_packet_or_manual_apply",
                        error=exc,
                    )
                )
            except (GitPacketError, OSError, ValueError) as exc:
                report_failure(repo or ctx.path, ctx, str(exc), reason="apply_failed")
                if ctx.run_mode != "daemon":
                    raise
            return
        if not ctx.args.require("kdeconnect-sync").value:
            return
        paths = {ctx.path}
        if ctx.event is not None:
            paths.update(
                os.fsdecode(value)
                for value in (
                    ctx.event.src_path,
                    getattr(ctx.event, "dest_path", ""),
                )
                if value
            )
        paths = {path for path in paths if not path_has_component(path, ".git")}
        roots = {root for path in paths if (root := find_parent_git_repo(path))}
        if not roots:
            logger.info(
                log_record(
                    "kdeconnect.sync_skip",
                    id=ctx.event_id,
                    path=ctx.path,
                    reason="not_a_repository",
                )
            )
            return
        try:
            settings = SyncSettings.from_args(ctx.args)
        except ValueError as exc:
            report_failure(sorted(roots)[0], ctx, str(exc), reason="invalid_config")
            return
        for repo_root in sorted(roots):
            try:
                if all(
                    is_queue_internal_path(path, repo_root, settings.queue_directory)
                    for path in paths
                ):
                    continue
                request = SyncRequest(repo_root, ctx, settings, system.operating_system)
                if ctx.run_mode != "daemon":
                    if sync_repo(request):
                        logger.info(
                            log_record(
                                "kdeconnect.sync_pending",
                                id=ctx.event_id,
                                repo=repo_root,
                                reason="retry_on_next_run",
                            )
                        )
                else:
                    self._schedule(request)
            except (OSError, ValueError) as exc:
                report_failure(repo_root, ctx, str(exc), reason="invalid_path")

    def _schedule(self, request: SyncRequest) -> None:
        with self._condition:
            self._pending[request.repo_root] = (
                request,
                time.monotonic() + request.settings.coalesce_seconds,
            )
            if request.repo_root not in self._active:
                self._active.add(request.repo_root)
                threading.Thread(
                    target=self._work,
                    args=(request.repo_root,),
                    name="lucy-kdeconnect-sync",
                    daemon=True,
                ).start()
            self._condition.notify_all()

    def _work(self, repo_root: str) -> None:
        while True:
            with self._condition:
                request, deadline = self._pending[repo_root]
                delay = deadline - time.monotonic()
                if delay > 0:
                    self._condition.wait(timeout=delay)
                    continue
                del self._pending[repo_root]
            try:
                retry = sync_repo(request)
            except Exception as exc:
                report_failure(
                    repo_root,
                    request.context,
                    str(exc),
                    reason="worker_exception",
                    exc_info=True,
                )
                retry = False
            with self._condition:
                if repo_root not in self._pending:
                    if retry:
                        self._pending[repo_root] = (
                            request,
                            time.monotonic() + request.settings.retry_seconds,
                        )
                    else:
                        self._active.remove(repo_root)
                        self._condition.notify_all()
                        return
