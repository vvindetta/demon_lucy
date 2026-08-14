from __future__ import annotations

import logging
import os
import shutil
import subprocess

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
from demon_lucy.modules.workspace.systemd_setup import (
    render_systemd_setup,
    systemd_setup_supported,
)
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
    def _lucy_home() -> str:
        return canonical_path(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

    def _template_values(
        self,
        workspace_root: str,
        config_lines: str,
        *,
        include_systemd_setup: bool,
    ) -> dict[str, str]:
        return {
            "CONFIG_LINES": config_lines,
            "CONFIG_PATH": self._config_path(workspace_root),
            "LUCY_HOME": self._lucy_home(),
            "SETUP_LINE": (
                "- `setup-systemd/` - systemd service files generated "
                "for this workspace.\n"
                if include_systemd_setup
                else ""
            ),
            "WORKSPACE_ROOT": workspace_root,
        }

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

    @staticmethod
    def _init_git(*, workspace_root: str, event_id: str) -> None:
        if os.path.exists(os.path.join(workspace_root, ".git")):
            return

        git_bin = shutil.which("git")
        if not git_bin:
            logger.warning(
                log_record(
                    "workspace.git_missing",
                    id=event_id,
                    workspace=workspace_root,
                )
            )
            return

        try:
            result = subprocess.run(
                [git_bin, "init"],
                cwd=workspace_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning(
                log_record(
                    "workspace.git_init_failed",
                    id=event_id,
                    workspace=workspace_root,
                    error=exc,
                )
            )
            return

        if result.returncode != 0:
            error_text = (result.stderr or result.stdout or "").strip()
            logger.warning(
                log_record(
                    "workspace.git_init_failed",
                    id=event_id,
                    workspace=workspace_root,
                    status=result.returncode,
                    error=error_text[:300],
                )
            )
            return

        logger.info(
            log_record(
                "workspace.git_initialized",
                id=event_id,
                workspace=workspace_root,
            )
        )

    def _apply(
        self,
        ctx: Context,
        system: System,
        *,
        write_trigger: bool,
    ) -> ModuleResult | None:
        workspace_root = self._workspace_root(ctx)
        if workspace_root is None:
            return None

        include_systemd_setup = systemd_setup_supported()
        config_lines = self._config_builder.lines(ctx, system, workspace_root)
        template_values = self._template_values(
            workspace_root,
            config_lines,
            include_systemd_setup=include_systemd_setup,
        )
        welcome_path = os.path.join(workspace_root, "welcome.md")

        changed: dict[str, int] = {}
        try:
            os.makedirs(workspace_root, exist_ok=True)
            changed.update(
                self._workspace_template.copy_files(
                    workspace_root,
                    template_values,
                    skip={"welcome.md"},
                )
            )
            if include_systemd_setup:
                for relative_path, text in render_systemd_setup(
                    lucy_home=self._lucy_home(),
                    workspace_root=workspace_root,
                    config_path=self._config_path(workspace_root),
                ).items():
                    setup_path = os.path.join(workspace_root, relative_path)
                    if self._workspace_template.write_file_if_missing(
                        setup_path,
                        text,
                    ):
                        changed[canonical_path(setup_path)] = 1
            if self._workspace_template.write_file_if_missing(
                welcome_path,
                self._workspace_template.welcome_text(template_values),
            ):
                changed[canonical_path(welcome_path)] = 1
            self._init_git(
                workspace_root=workspace_root,
                event_id=ctx.event_id,
            )
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

        trigger_changed = (
            self._write_success_to_trigger(ctx, workspace_root) if write_trigger else {}
        )
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
        return self._apply(ctx, system, write_trigger=True)

    def modified(self, ctx: Context, system: System) -> ModuleResult | None:
        return self._apply(ctx, system, write_trigger=True)

    def opened(self, ctx: Context, system: System) -> ModuleResult | None:
        return self._apply(ctx, system, write_trigger=True)

    def cli(self, ctx: Context, system: System) -> ModuleResult | None:
        return self._apply(ctx, system, write_trigger=False)
