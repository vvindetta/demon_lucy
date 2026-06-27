from __future__ import annotations

import logging
import os
import shlex
from typing import Any, Optional

from demon_lucy.lib.args.line_edit import delete_args_from_string
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
    def _config_path(workspace_root: str) -> str:
        return os.path.join(workspace_root, ".lucy", "config")

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
    def _defaults_by_destination(system: System) -> dict[str, Any]:
        defaults: dict[str, Any] = {}
        for template_item in system.global_template:
            flag, _typ, default, _desc, _required = parse_template_item(template_item)
            defaults.setdefault(flag_to_dest(flag), default)
        return defaults

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

    @staticmethod
    def _module_name_set(values: object) -> set[str]:
        if not isinstance(values, list):
            return set()
        return {str(value).strip() for value in values if str(value).strip()}

    def _required_config_lines(self, workspace_root: str, system: System) -> list[str]:
        defaults = self._defaults_by_destination(system)
        module_names = self._module_names_for_config(system)
        lines = [f"--sys-watch-paths {self._quote(workspace_root)}\n"]
        if self._module_name_set(module_names) != self._module_name_set(
            defaults.get("sys_modules")
        ):
            lines.append(
                "--sys-modules "
                + " ".join(self._quote(name) for name in module_names)
                + "\n"
            )
        lines.append(
            "--archive-auto-pair "
            + " ".join(self._quote(value) for value in self._archive_pair_values)
            + "\n"
        )
        return lines

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
            "\n",
        ]
        lines.extend(self._required_config_lines(workspace_root, system))
        runtime_lines = self._runtime_config_lines(ctx, system)
        if runtime_lines:
            lines.append("\n")
            lines.extend(runtime_lines)
        return "".join(lines)

    @staticmethod
    def _config_summary_lines(config_text: str) -> list[str]:
        lines: list[str] = []
        for raw_line in config_text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            lines.append(line)
        return lines

    def _welcome_text(self, workspace_root: str, config_text: str) -> str:
        lines = [
            "# Welcome\n",
            "\n",
            "Demon Lucy initialized this workspace.\n",
            "\n",
            "Created:\n",
            "- `.lucy/config` - workspace config.\n",
            "- `now.md` - active note.\n",
            "- `.archive/past.md` - archive note.\n",
            "- `.status/` - status notes.\n",
            "\n",
            "Workspace:\n",
            f"- `{workspace_root}`\n",
            "\n",
            "Config:\n",
        ]
        config_lines = self._config_summary_lines(config_text)
        if config_lines:
            lines.extend(f"- `{line}`\n" for line in config_lines)
        else:
            lines.append("- No non-default config values.\n")
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

    def _write_success_to_trigger(
        self,
        ctx: Context,
        workspace_root: str,
        system: System,
    ) -> IgnoreMap:
        raw_lines = ctx.arg_lines.get("workspace_init") or []
        line_numbers: list[int] = []
        for raw_lineno in raw_lines:
            try:
                line_number = int(raw_lineno)
            except (TypeError, ValueError):
                continue
            if line_number not in line_numbers:
                line_numbers.append(line_number)
        if not line_numbers or not os.path.isfile(ctx.path):
            return {}

        try:
            with open(ctx.path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning(
                log_record(
                    "workspace.init_note_failed",
                    id=system.event_id,
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
                    id=system.event_id,
                    path=ctx.path,
                    workspace=workspace_root,
                    error=exc,
                )
            )
            return {}

        return {canonical_path(ctx.path): 1}

    def _apply(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        workspace_root = self._workspace_root(ctx)
        if workspace_root is None:
            return None

        archive_dir = os.path.join(workspace_root, ".archive")
        status_dir = os.path.join(workspace_root, ".status")
        config_dir = os.path.join(workspace_root, ".lucy")
        config_path = self._config_path(workspace_root)
        config_text = self._workspace_config_text(ctx, system, workspace_root)
        welcome_path = os.path.join(workspace_root, "welcome.md")
        paths_to_write = {
            config_path: config_text,
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
            os.makedirs(config_dir, exist_ok=True)
            for path, text in paths_to_write.items():
                if self._write_file_if_missing(path, text):
                    changed[canonical_path(path)] = 1
            with open(config_path, "r", encoding="utf-8") as handle:
                actual_config_text = handle.read()
            if self._write_file_if_missing(
                welcome_path,
                self._welcome_text(workspace_root, actual_config_text),
            ):
                changed[canonical_path(welcome_path)] = 1
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

        trigger_changed = self._write_success_to_trigger(ctx, workspace_root, system)
        changed.update(trigger_changed)
        logger.info(
            log_record(
                "workspace.init_done",
                id=system.event_id,
                path=ctx.path,
                workspace=workspace_root,
                changed_paths=len(changed),
                trigger_written=bool(trigger_changed),
            )
        )
        return changed or None

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx, system)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx, system)

    def opened(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx, system)
