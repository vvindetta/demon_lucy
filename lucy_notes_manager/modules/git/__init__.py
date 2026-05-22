from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

from lucy_notes_manager.lib.args import Template
from lucy_notes_manager.lib.path import (
    abs_expand_path,
    find_parent_with,
    path_has_component,
)
from lucy_notes_manager.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)
from lucy_notes_manager.modules.git.config import (
    GIT_TEMPLATE,
)
from lucy_notes_manager.modules.git.helpers import to_str
from lucy_notes_manager.modules.git.helpers import format_path_for_commit_message
from lucy_notes_manager.modules.git.types import _RepoBatch
from lucy_notes_manager.modules.git.worker import process_event

logger = logging.getLogger(__name__)


class Git(AbstractModule):
    name: str = "git"
    priority: int = 50

    template: Template = GIT_TEMPLATE

    def __init__(self) -> None:
        super().__init__()

    def _build_commit_message(self, batch: _RepoBatch, changed_paths: list[str]) -> str:
        event_summary = batch.event_type or "change"

        file_names = [
            os.path.basename(format_path_for_commit_message(path_item))
            for path_item in changed_paths
            if path_item
        ]
        if not file_names and batch.hinted_paths:
            file_names = [
                os.path.basename(format_path_for_commit_message(path_item))
                for path_item in batch.hinted_paths
            ]

        shown_names = ", ".join(file_names[:8])
        if len(file_names) > 8:
            shown_names += f", +{len(file_names) - 8} more"

        message_text = f"{batch.base_message}: {event_summary}"
        if shown_names:
            message_text += f" {shown_names}"
        if batch.add_timestamp_to_message:
            message_text += f" [{datetime.now().strftime(batch.timestamp_format)}]"
        return message_text

    @staticmethod
    def _should_run_in_background(system: System) -> bool:
        return system.run_mode != "oneshot"

    def opened(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        ctx_path = (
            abs_expand_path(to_str(ctx.path)) if getattr(ctx, "path", None) else ""
        )
        if ctx_path and path_has_component(ctx_path, ".git"):
            return None

        repo_root = find_parent_with(to_str(ctx.path), ".git")
        if not repo_root:
            return None

        if ctx.config["git_sync_on_opened_disable"]:
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

        repo_root = find_parent_with(to_str(ctx.path), ".git") or find_parent_with(
            destination_path or source_path, ".git"
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
