from __future__ import annotations

import logging
import os
import shlex
from typing import Any, Optional

from demon_lucy.lib.args.parser import Template, flag_to_dest, parse_template_item
from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.notifications import safe_notify
from demon_lucy.lib.path import canonical_path
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)

logger = logging.getLogger(__name__)


class Workspace(AbstractModule):
    name: str = "workspace"
    priority: int = 1

    template: Template = [
        (
            "--workspace-init",
            str,
            "",
            "Initialize a Lucy workspace at the given directory path.",
            False,
        ),
    ]

    _status_animation_text = '--status-animation "-- ---- --" "-<( ✷ )>-" "-< --- >-"\n'
    _status_sync_text = '--status git update --status-prefix "Sync: "\n'
    _archive_pair_values = ["now.md", ".archive/past.md", "10", "text"]

    @staticmethod
    def _base_dir(ctx: Context) -> str:
        if os.path.isdir(ctx.path):
            return ctx.path
        return os.path.dirname(ctx.path)

    def _workspace_root(self, ctx: Context) -> str | None:
        raw_value = str(ctx.config.get("workspace_init") or "").strip()
        if not raw_value:
            return None

        expanded = os.path.expanduser(raw_value)
        if os.path.isabs(expanded):
            return canonical_path(expanded)
        return canonical_path(os.path.join(self._base_dir(ctx), expanded))

    @staticmethod
    def _quote(value: object) -> str:
        return shlex.quote(str(value))

    @staticmethod
    def _flag_from_config_key(key: str) -> str:
        return "--" + key.replace("_", "-")

    @staticmethod
    def _default_for_config_value(value: Any) -> Any:
        if isinstance(value, bool):
            return False
        if isinstance(value, list):
            return []
        if isinstance(value, str):
            return ""
        if isinstance(value, int):
            return 0
        if isinstance(value, float):
            return 0.0
        return None

    def _module_names_for_config(self, system: System) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()

        def add(name: str) -> None:
            normalized = str(name).strip()
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            names.append(normalized)

        for module in system.modules:
            add(getattr(module, "name", ""))
        for required_name in ("workspace", "archive", "status"):
            add(required_name)
        return names

    def _required_config_lines(self, workspace_root: str, system: System) -> list[str]:
        config_path = os.path.join(workspace_root, ".lucy")
        module_names = self._module_names_for_config(system)
        return [
            f"--sys-config-path {self._quote(config_path)}\n",
            f"--sys-watch-paths {self._quote(workspace_root)}\n",
            "--sys-modules "
            + " ".join(self._quote(name) for name in module_names)
            + "\n",
            "--archive-auto-pair "
            + " ".join(self._quote(value) for value in self._archive_pair_values)
            + "\n",
        ]

    def _template_items(
        self,
        ctx: Context,
        system: System,
    ) -> list[tuple[str, type, Any, str, bool]]:
        items: list[tuple[str, type, Any, str, bool]] = []
        seen_destinations: set[str] = set()
        for template_item in system.global_template:
            flag, typ, default, desc, required = parse_template_item(template_item)
            destination = flag_to_dest(flag)
            if destination in seen_destinations:
                continue
            seen_destinations.add(destination)
            items.append((flag, typ, default, desc, required))
        for key, value in sorted(ctx.config.items()):
            if not key.startswith("sys_"):
                continue
            if key in seen_destinations:
                continue
            seen_destinations.add(key)
            items.append(
                (
                    self._flag_from_config_key(key),
                    type(value),
                    self._default_for_config_value(value),
                    "",
                    False,
                )
            )
        return items

    def _render_config_line(
        self,
        *,
        flag: str,
        value: Any,
        default: Any,
    ) -> str | None:
        if value == default:
            return None
        if value is None:
            return None

        if isinstance(value, bool):
            return f"{flag}\n" if value else None

        if isinstance(value, list):
            if not value:
                return f"{flag}\n" if value != default else None
            return flag + " " + " ".join(self._quote(item) for item in value) + "\n"

        if isinstance(value, str) and not value:
            return None

        return f"{flag} {self._quote(value)}\n"

    def _runtime_config_lines(self, ctx: Context, system: System) -> list[str]:
        forced_destinations = {
            "sys_config_path",
            "sys_watch_paths",
            "sys_modules",
            "archive_auto_pair",
            "workspace_init",
        }
        lines: list[str] = []

        for flag, _typ, default, _desc, _required in self._template_items(ctx, system):
            destination = flag_to_dest(flag)
            if destination in forced_destinations:
                continue
            if destination in ctx.arg_lines:
                continue
            if flag.startswith("--oneshot-"):
                continue
            if destination not in ctx.config:
                continue

            line = self._render_config_line(
                flag=flag,
                value=ctx.config[destination],
                default=default,
            )
            if line is not None:
                lines.append(line)

        return lines

    def _workspace_config_text(
        self,
        ctx: Context,
        system: System,
        workspace_root: str,
    ) -> str:
        lines = [
            "# Generated by Demon Lucy workspace init.\n",
            "# Run with: python3 main_daemon.py --sys-config-path "
            + self._quote(os.path.join(workspace_root, ".lucy"))
            + "\n",
            "\n",
        ]
        lines.extend(self._required_config_lines(workspace_root, system))
        runtime_lines = self._runtime_config_lines(ctx, system)
        if runtime_lines:
            lines.append("\n")
            lines.extend(runtime_lines)
        return "".join(lines)

    @staticmethod
    def _write_file_if_missing(path: str, text: str) -> bool:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "x", encoding="utf-8") as handle:
                handle.write(text)
            return True
        except FileExistsError:
            return False

    def _apply(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        workspace_root = self._workspace_root(ctx)
        if workspace_root is None:
            return None

        archive_dir = os.path.join(workspace_root, ".archive")
        status_dir = os.path.join(workspace_root, ".status")
        config_path = os.path.join(workspace_root, ".lucy")
        paths_to_write = {
            config_path: self._workspace_config_text(ctx, system, workspace_root),
            os.path.join(status_dir, "workspace-animation.md"): (
                self._status_animation_text
            ),
            os.path.join(status_dir, "workspace-sync.md"): self._status_sync_text,
            os.path.join(workspace_root, "now.md"): "",
            os.path.join(archive_dir, "past.md"): "",
        }

        changed: IgnoreMap = {}
        try:
            os.makedirs(archive_dir, exist_ok=True)
            os.makedirs(status_dir, exist_ok=True)
            for path, text in paths_to_write.items():
                if self._write_file_if_missing(path, text):
                    changed[canonical_path(path)] = 1
        except OSError as exc:
            logger.error(
                log_record(
                    "workspace.init_failed",
                    id=system.event_id,
                    path=ctx.path,
                    workspace=workspace_root,
                    error=exc,
                )
            )
            safe_notify(
                f"workspace-init:{workspace_root}",
                f"Workspace init failed: {exc}",
                config=ctx.config,
                use_rare_mode=True,
            )
            return None

        logger.info(
            log_record(
                "workspace.init",
                id=system.event_id,
                path=ctx.path,
                workspace=workspace_root,
                changed_paths=len(changed),
            )
        )
        return changed or None

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx, system)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx, system)

    def opened(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx, system)
