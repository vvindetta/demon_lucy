from __future__ import annotations

import shlex
from typing import Any

from demon_lucy.lib.args.parser import flag_to_dest, parse_template_item
from demon_lucy.modules.abstract_module import Context, System


class WorkspaceConfig:
    archive_pair_values = ["now.md", ".archive/past.md", "10", "text"]

    @staticmethod
    def quote(value: object) -> str:
        return shlex.quote(str(value))

    @staticmethod
    def flag_from_config_key(key: str) -> str:
        return "--" + key.replace("_", "-")

    @staticmethod
    def defaults_by_destination(system: System) -> dict[str, Any]:
        defaults: dict[str, Any] = {}
        for template_item in system.global_template:
            flag, _typ, default, _desc, _required = parse_template_item(template_item)
            defaults.setdefault(flag_to_dest(flag), default)
        return defaults

    @staticmethod
    def default_for_config_value(value: Any) -> Any:
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

    @staticmethod
    def module_name_set(values: object) -> set[str]:
        if not isinstance(values, list):
            return set()
        return {str(value).strip() for value in values if str(value).strip()}

    def module_names(self, system: System) -> list[str]:
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

    def required_lines(self, workspace_root: str, system: System) -> list[str]:
        defaults = self.defaults_by_destination(system)
        module_names = self.module_names(system)
        lines = [f"--sys-watch-paths {self.quote(workspace_root)}\n"]
        if self.module_name_set(module_names) != self.module_name_set(
            defaults.get("sys_modules")
        ):
            lines.append(
                "--sys-modules "
                + " ".join(self.quote(name) for name in module_names)
                + "\n"
            )
        lines.append(
            "--archive-auto-pair "
            + " ".join(self.quote(value) for value in self.archive_pair_values)
            + "\n"
        )
        return lines

    def template_items(
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
                    self.flag_from_config_key(key),
                    type(value),
                    self.default_for_config_value(value),
                    "",
                    False,
                )
            )
        return items

    def render_line(
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
            return flag + " " + " ".join(self.quote(item) for item in value) + "\n"

        if isinstance(value, str) and not value:
            return None

        return f"{flag} {self.quote(value)}\n"

    def runtime_lines(self, ctx: Context, system: System) -> list[str]:
        forced_destinations = {
            "sys_config_path",
            "sys_watch_paths",
            "sys_modules",
            "archive_auto_pair",
            "workspace_init",
        }
        lines: list[str] = []

        for flag, _typ, default, _desc, _required in self.template_items(ctx, system):
            destination = flag_to_dest(flag)
            if destination in forced_destinations:
                continue
            if destination in ctx.arg_lines:
                continue
            if flag.startswith("--oneshot-"):
                continue
            if destination not in ctx.config:
                continue

            line = self.render_line(
                flag=flag,
                value=ctx.config[destination],
                default=default,
            )
            if line is not None:
                lines.append(line)

        return lines

    def lines(self, ctx: Context, system: System, workspace_root: str) -> str:
        lines: list[str] = []
        lines.extend(self.required_lines(workspace_root, system))
        runtime_lines = self.runtime_lines(ctx, system)
        if runtime_lines:
            lines.append("\n")
            lines.extend(runtime_lines)
        return "".join(lines)
