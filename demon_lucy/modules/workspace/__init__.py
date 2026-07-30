from __future__ import annotations

import logging
import os

from demon_lucy.lib.args.line_edit import delete_args_from_string
from demon_lucy.lib.args.models import KnownArg, Template
from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.notifications import safe_notify
from demon_lucy.lib.path import canonical_path
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    ModuleResult,
    System,
)
from demon_lucy.modules.workspace.config import WorkspaceConfig
from demon_lucy.modules.workspace.template import (
    DEFAULT_TEMPLATE_DIR,
    WorkspaceTemplate,
)

logger = logging.getLogger(__name__)


class Workspace(AbstractModule):
    name: str = "workspace"
    priority: int = 5

    template: Template = [
        KnownArg(
            name="workspace-init",
            value_type=str,
            default="",
            description="Initialize a Lucy workspace at the given directory path.",
        ),
    ]

    _template_dir = DEFAULT_TEMPLATE_DIR
    _config_builder = WorkspaceConfig()
    _workspace_template = WorkspaceTemplate()

    @staticmethod
    def _config_path(workspace_root: str) -> str:
        return os.path.join(workspace_root, ".lucy", "config.txt")

    @staticmethod
    def _base_dir(ctx: Context) -> str:
        if os.path.isdir(ctx.path):
            return ctx.path
        return os.path.dirname(ctx.path)

    def _workspace_root(self, ctx: Context) -> str | None:
        raw_value = ctx.args.require("workspace-init").value.strip()
        if not raw_value:
            return None

        expanded = os.path.expanduser(raw_value)
        if os.path.isabs(expanded):
            return canonical_path(expanded)
        return canonical_path(os.path.join(self._base_dir(ctx), expanded))

    def _write_success_to_trigger(
        self,
        ctx: Context,
        workspace_root: str,
    ) -> dict[str, int]:
        line_numbers = ctx.args.require("workspace-init").lines
        if not line_numbers or not os.path.isfile(ctx.path):
            return {}

        try:
            with open(ctx.path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning(
                log_record(
                    "workspace.init_note_failed",
                    id=ctx.event_id,
                    path=ctx.path,
                    workspace=workspace_root,
                    error=exc,
                )
            )
            return {}

        success_line = f"workspace init ok: {workspace_root}\n"
        changed = False
        for line_number in sorted(line_numbers, reverse=True):
            index = line_number - 1
            if index < 0 or index >= len(lines):
                continue
            original_line = lines[index]
            if "--workspace-init" not in original_line:
                continue

            cleaned_line = delete_args_from_string(
                original_line,
                ["--workspace-init"],
            )
            replacement = [success_line]
            if cleaned_line.strip():
                replacement = [cleaned_line, success_line]
            if lines[index : index + 1] == replacement:
                continue
            lines[index : index + 1] = replacement
            changed = True

        if not changed:
            return {}

        try:
            with open(ctx.path, "w", encoding="utf-8") as handle:
                handle.writelines(lines)
        except OSError as exc:
            logger.warning(
                log_record(
                    "workspace.init_note_failed",
                    id=ctx.event_id,
                    path=ctx.path,
                    workspace=workspace_root,
                    error=exc,
                )
            )
            return {}

        return {canonical_path(ctx.path): 1}

    def _apply(self, ctx: Context, system: System) -> ModuleResult | None:
        workspace_root = self._workspace_root(ctx)
        if workspace_root is None:
            return None

        config_path = self._config_path(workspace_root)
        config_lines = self._config_builder.lines(ctx, system, workspace_root)
        welcome_path = os.path.join(workspace_root, "welcome.md")

        changed: dict[str, int] = {}
        try:
            os.makedirs(workspace_root, exist_ok=True)
            changed.update(
                self._workspace_template.copy_files(
                    workspace_root,
                    {
                        "CONFIG_LINES": config_lines,
                    },
                    skip={"welcome.md"},
                )
            )
            with open(config_path, "r", encoding="utf-8") as handle:
                actual_config_text = handle.read()
            if self._workspace_template.write_file_if_missing(
                welcome_path,
                self._workspace_template.welcome_text(
                    workspace_root,
                    actual_config_text,
                ),
            ):
                changed[canonical_path(welcome_path)] = 1
        except OSError as exc:
            logger.error(
                log_record(
                    "workspace.init_failed",
                    id=ctx.event_id,
                    path=ctx.path,
                    workspace=workspace_root,
                    error=exc,
                )
            )
            safe_notify(
                f"workspace-init:{workspace_root}",
                f"Workspace init failed: {exc}",
                args=ctx.args,
                use_rare_mode=True,
            )
            return None

        trigger_changed = self._write_success_to_trigger(ctx, workspace_root)
        changed.update(trigger_changed)
        logger.info(
            log_record(
                "workspace.init_done",
                id=ctx.event_id,
                path=ctx.path,
                workspace=workspace_root,
                changed_paths=len(changed),
                trigger_written=bool(trigger_changed),
            )
        )
        return ModuleResult(context=ctx, changed=changed) if changed else None

    def created(self, ctx: Context, system: System) -> ModuleResult | None:
        return self._apply(ctx, system)

    def modified(self, ctx: Context, system: System) -> ModuleResult | None:
        return self._apply(ctx, system)

    def opened(self, ctx: Context, system: System) -> ModuleResult | None:
        return self._apply(ctx, system)

    def cli(self, ctx: Context, system: System) -> ModuleResult | None:
        return self._apply(ctx, system)
