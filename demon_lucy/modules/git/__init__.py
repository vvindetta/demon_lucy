from __future__ import annotations

import logging

from demon_lucy.lib.args.models import Template
from demon_lucy.lib.git_state import repo_process_lock_is_active
from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.path import (
    abs_expand_path,
    find_parent_git_repo,
    path_has_component,
)
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    ModuleResult,
    System,
)
from demon_lucy.modules.git.config import GIT_TEMPLATE
from demon_lucy.modules.git.helpers import to_str
from demon_lucy.modules.git.worker import process_event

logger = logging.getLogger(__name__)


class Git(AbstractModule):
    name: str = "git"
    priority: int = 50

    template: Template = GIT_TEMPLATE

    def opened(self, ctx: Context, system: System) -> ModuleResult | None:
        ctx_path = abs_expand_path(to_str(ctx.path))
        if path_has_component(ctx_path, ".git"):
            return None

        repo_root = find_parent_git_repo(to_str(ctx.path))
        if not repo_root:
            return None

        if ctx.args.require("git-sync-on-opened-disable").value:
            return None

        if repo_process_lock_is_active(
            repo_root,
            wait_timeout_seconds=max(
                0.0,
                ctx.args.require("sys-git-repo-lock-wait-timeout-seconds").value,
            ),
            stale_seconds=max(
                0.0, ctx.args.require("sys-git-repo-lock-stale-seconds").value
            ),
            operating_system=system.operating_system,
        ):
            logger.info(
                log_record(
                    "git.sync_skip",
                    id=ctx.event_id,
                    reason="repo_process_lock_active",
                    event="opened",
                    repo=repo_root,
                    path=ctx_path,
                )
            )
            return None

        process_event(
            self,
            repo_root=repo_root,
            event_type="opened",
            paths=[to_str(ctx.path)],
            args=ctx.args,
            operating_system=system.operating_system,
            run_in_background=ctx.run_mode != "oneshot",
        )
        return None

    def created(self, ctx: Context, system: System) -> ModuleResult | None:
        return self._handle(ctx, system, "created")

    def modified(self, ctx: Context, system: System) -> ModuleResult | None:
        return self._handle(ctx, system, "modified")

    def deleted(self, ctx: Context, system: System) -> ModuleResult | None:
        return self._handle(ctx, system, "deleted")

    def moved(self, ctx: Context, system: System) -> ModuleResult | None:
        return self._handle(ctx, system, "moved")

    def _handle(
        self, ctx: Context, system: System, event_type: str
    ) -> ModuleResult | None:
        event = ctx.event

        source_path_raw = to_str(getattr(event, "src_path", "") or "")
        destination_path_raw = getattr(event, "dest_path", None)
        destination_path_value = (
            to_str(destination_path_raw) if destination_path_raw is not None else ""
        )

        source_path = abs_expand_path(source_path_raw) if source_path_raw else ""
        destination_path = (
            abs_expand_path(destination_path_value) if destination_path_value else ""
        )

        if (source_path and path_has_component(source_path, ".git")) or (
            destination_path and path_has_component(destination_path, ".git")
        ):
            return None

        repo_root = find_parent_git_repo(to_str(ctx.path)) or find_parent_git_repo(
            destination_path or source_path
        )
        if not repo_root:
            return None

        paths_to_hint: list[str] = []
        if event_type != "moved":
            paths_to_hint = [to_str(ctx.path)]
        else:
            if source_path:
                paths_to_hint.append(source_path)
            if destination_path:
                paths_to_hint.append(destination_path)

        process_event(
            self,
            repo_root=repo_root,
            event_type=event_type,
            paths=paths_to_hint,
            args=ctx.args,
            operating_system=system.operating_system,
            run_in_background=ctx.run_mode != "oneshot",
        )
        return None
