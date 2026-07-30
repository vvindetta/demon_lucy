from __future__ import annotations

import shlex
from typing import Any

from demon_lucy.lib.args.models import ArgSource
from demon_lucy.modules.abstract_module import Context, System


class WorkspaceConfig:
    archive_pair_values = ["now.md", ".archive/past.md", "10", "text"]

    @staticmethod
    def quote(value: object) -> str:
        return shlex.quote(str(value))

    @staticmethod
    def defaults_by_name(system: System) -> dict[str, Any]:
        return {
            template_item.name: template_item.default
            for template_item in system.global_template
        }

    @staticmethod
    def module_name_set(values: list[str]) -> set[str]:
        return {value.strip() for value in values if value.strip()}

    def module_names(self, system: System) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()

        def add(name: str) -> None:
            normalized = name.strip()
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            names.append(normalized)

        for module in system.modules:
            add(module.name)
        for required_name in ("workspace", "archive", "status"):
            add(required_name)
        return names

    def required_lines(self, workspace_root: str, system: System) -> list[str]:
        defaults = self.defaults_by_name(system)
        module_names = self.module_names(system)
        lines = [f"--sys-watch-paths {self.quote(workspace_root)}\n"]
        if self.module_name_set(module_names) != self.module_name_set(
            defaults["sys-modules"]
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
        forced_names = {
            "sys-config-path",
            "sys-watch-paths",
            "sys-modules",
            "archive-auto-pair",
            "workspace-init",
        }
        lines: list[str] = []

        for argument in ctx.args.known:
            if argument.name in forced_names:
                continue
            if argument.source is ArgSource.FILE:
                continue
            if argument.name.startswith("oneshot-"):
                continue

            line = self.render_line(
                flag=f"--{argument.name}",
                value=argument.value,
                default=argument.default,
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
