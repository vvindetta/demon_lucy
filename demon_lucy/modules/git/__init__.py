from __future__ import annotations

import logging
from typing import Optional

from demon_lucy.lib.args.parser import Template
from demon_lucy.lib.path import (
    abs_expand_path,
    find_parent_git_repo,
    path_has_component,
)
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)
from demon_lucy.modules.git.config import (
    GIT_TEMPLATE,
)
from demon_lucy.modules.git.commit_message import build_commit_message
from demon_lucy.modules.git.helpers import to_str
from demon_lucy.modules.git.types import _RepoBatch
from demon_lucy.modules.git.worker import process_event, repo_process_lock_is_active

logger = logging.getLogger(__name__)


class Git(AbstractModule):
    name: str = "git"
    priority: int = 50

    template: Template = GIT_TEMPLATE

    def __init__(self) -> None:
        super().__init__()

    def _build_commit_message(self, batch: _RepoBatch, changed_paths: list[str]) -> str:
        return build_commit_message(batch, changed_paths).as_text()

    @staticmethod
    def _should_run_in_background(system: System) -> bool:
        return system.run_mode != "oneshot"

    def opened(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        ctx_path = (
            abs_expand_path(to_str(ctx.path)) if getattr(ctx, "path", None) else ""
        )
        if ctx_path and path_has_component(ctx_path, ".git"):
            return None

        repo_root = find_parent_git_repo(to_str(ctx.path))
        if not repo_root:
            return None

        if ctx.config["git_sync_on_opened_disable"]:
            return None

        if repo_process_lock_is_active(repo_root):
            logger.debug(
                "skipping opened git sync while repo process lock is active | repo=%s",
                repo_root,
            )
            return None

        process_event(
            self,
            repo_root=repo_root,
            event_type="opened",
            paths=[to_str(ctx.path)],
            config_snapshot=ctx.config,
            run_in_background=self._should_run_in_background(system),
        )
        return None

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx, system, "created")

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx, system, "modified")

    def deleted(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx, system, "deleted")

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx, system, "moved")

    def _handle(
        self, ctx: Context, system: System, event_type: str
    ) -> Optional[IgnoreMap]:
        event = system.event

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
            config_snapshot=ctx.config,
            run_in_background=self._should_run_in_background(system),
        )
        return None
