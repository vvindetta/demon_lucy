from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from demon_lucy.lib.args.line_edit import delete_args_from_string
from demon_lucy.lib.args.parser import (
    ArgTemplate,
    flag_to_dest,
)
from demon_lucy.lib.notifications import safe_notify
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)


class Sys(AbstractModule):
    name: str = "sys"
    priority: int = 2

    template = [
        ArgTemplate(
            name="--mods",
            value_type=bool,
            default=False,
            description="Print loaded modules and their priorities.",
        ),
        ArgTemplate(
            name="--ping",
            value_type=bool,
            default=False,
            description="Health-check: sends notification and writes pong.",
            required=False,
        ),
        ArgTemplate(
            name="--config",
            value_type=bool,
            default=False,
            description="Print config values that differ from defaults (and where they were set).",
            required=False,
        ),
        ArgTemplate(
            name="--man",
            value_type=str,
            default=[],
            description="Print one argument with description (example: --man mods or --man --mods).",
            required=False,
        ),
        ArgTemplate(
            name="--help",
            value_type=bool,
            default=False,
            description="Print SysInfo commands help: --mods, --man, --config.",
            required=False,
        ),
        ArgTemplate(
            name="--event",
            value_type=bool,
            default=False,
            description="Print current filesystem event details.",
        ),
    ]

    @staticmethod
    def _type_name(type_value: Any) -> str:
        return getattr(type_value, "__name__", str(type_value))

    def _defaults_map(self, system: System) -> dict[str, Any]:
        defaults: dict[str, Any] = {}
        for item in system.global_template:
            defaults[flag_to_dest(item.name)] = item.default
        return defaults

    @staticmethod
    def _command_help_lines() -> List[str]:
        return [
            "* --mods: print loaded modules and their priorities\n",
            "* --ping: send notification and rewrite command line to ++pong!\n",
            "* --config: print config values that differ from defaults\n",
            "* --man <name>: print one argument with description (example: --man mods or --man --mods)\n",
            "* --event: print current filesystem event details\n",
        ]

    @staticmethod
    def _apply_ping_rewrite(
        file_lines: List[str],
        index: int,
        remove_flags: List[str],
    ) -> None:
        cleaned_line = delete_args_from_string(file_lines[index], remove_flags)
        file_lines[index : index + 1] = ["++pong!\n"]
        if cleaned_line.strip():
            file_lines[index + 1 : index + 1] = [cleaned_line]

    @staticmethod
    def _send_ping_notification(ctx: Context) -> None:
        safe_notify(
            "sys-ping",
            "++pong!",
            config=ctx.config,
            title="Demon Lucy ping",
            use_rare_mode=False,
        )

    @staticmethod
    def _normalize_arg_name(raw: str) -> str:
        text = (raw or "").strip()
        if not text:
            return ""
        return text.lstrip("-").strip().lower()

    @staticmethod
    def _flag_from_config_key(key: str) -> str:
        return "--" + key.replace("_", "-")

    @staticmethod
    def _type_from_config_value(value: Any) -> type:
        if isinstance(value, list):
            return str
        if value is None:
            return str
        return type(value)

    def _expand_man_requests(self, requested_names: List[str]) -> List[str]:
        expanded: List[str] = []
        for raw in requested_names or []:
            for chunk in str(raw).split("/"):
                normalized = self._normalize_arg_name(chunk)
                if normalized:
                    expanded.append(normalized)
        return expanded

    def _module_flags_by_request_name(
        self,
        system: System,
        config: dict[str, Any] | None = None,
    ) -> dict[str, set[str]]:
        mapping: dict[str, set[str]] = {}

        for module in system.modules:
            module_name = self._normalize_arg_name(getattr(module, "name", ""))
            if not module_name:
                continue

            module_keys = {module_name, module_name.replace("_", "-")}
            module_flags: set[str] = set()

            for template_item in getattr(module, "template", []) or []:
                module_flags.add(
                    self._normalize_arg_name(template_item.name.lstrip("-"))
                )
                module_flags.add(
                    self._normalize_arg_name(flag_to_dest(template_item.name))
                )

            for key in module_keys:
                mapping.setdefault(key, set()).update(module_flags)

        system_flags = self._system_flag_names(system, config)
        if system_flags:
            mapping["sys"] = set(system_flags)

        return mapping

    def _system_flag_names(
        self,
        system: System,
        config: dict[str, Any] | None = None,
    ) -> set[str]:
        flags: set[str] = set()
        for template_item in system.global_template:
            normalized_flag = self._normalize_arg_name(template_item.name.lstrip("-"))
            if not normalized_flag.startswith("sys-"):
                continue
            flags.add(normalized_flag)
            flags.add(self._normalize_arg_name(flag_to_dest(template_item.name)))
        for key in sorted((config or {}).keys()):
            if not key.startswith("sys_"):
                continue
            flags.add(self._normalize_arg_name(key))
            flags.add(self._normalize_arg_name(self._flag_from_config_key(key)))
        return flags

    def _manual_template_items(
        self,
        system: System,
        config: dict[str, Any] | None = None,
    ) -> list[ArgTemplate]:
        items: list[ArgTemplate] = []
        seen_destinations: set[str] = set()
        for template_item in system.global_template:
            destination = flag_to_dest(template_item.name)
            if destination in seen_destinations:
                continue
            seen_destinations.add(destination)
            items.append(template_item)
        for key, value in sorted((config or {}).items()):
            if not key.startswith("sys_"):
                continue
            if key in seen_destinations:
                continue
            seen_destinations.add(key)
            items.append(
                ArgTemplate(
                    name=self._flag_from_config_key(key),
                    value_type=self._type_from_config_value(value),
                    default=None,
                    description="System arg from runtime config.",
                )
            )
        return items

    def _man_one_lines(
        self,
        system: System,
        requested_names: List[str],
        config: dict[str, Any] | None = None,
    ) -> List[str]:
        requested = self._expand_man_requests(requested_names)
        if not requested:
            return ["* (missing name: use --man <name> or --man --flag)\n"]

        requested_set = set(requested)
        module_flags_map = self._module_flags_by_request_name(system, config)
        for request_name in list(requested_set):
            requested_set.update(module_flags_map.get(request_name, set()))
        matched: List[str] = []

        for item in self._manual_template_items(system, config):
            flag_name = item.name.lstrip("-").lower()
            dest_name = flag_to_dest(item.name).lower()
            if flag_name in requested_set or dest_name in requested_set:
                type_name = self._type_name(item.value_type)
                description = (item.description or "").strip()
                matched.append(
                    f"* {item.name}: {description} "
                    f"(type={type_name}, default={item.default})\n"
                )

        if matched:
            return matched

        return [f"* (unknown arg: {', '.join(requested)})\n"]

    def _man_lines(
        self,
        system: System,
        requests: List[str],
        config: dict[str, Any] | None = None,
    ) -> List[str]:
        return self._man_one_lines(system, requests, config)

    def _build_block(
        self,
        *,
        system: System,
        ctx: Context,
        selected_opts: set[str],
        path: str,
        man_requests: List[str],
    ) -> List[str]:
        ordered = ["mods", "ping", "help", "man", "config", "event"]
        title_parts = [name for name in ordered if name in selected_opts]
        title = "+".join(title_parts) if title_parts else "sys"

        lines: List[str] = []
        lines.append(f"--- {title} ---\n")

        if "event" in selected_opts:
            lines.append(f"time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        lines.append("\n")

        if "help" in selected_opts:
            lines.extend(self._command_help_lines())
            lines.append("\n")

        if "mods" in selected_opts:
            for module in system.modules:
                lines.append(f"* {module.name} ({getattr(module, 'priority', None)})\n")
            lines.append("\n")

        if "ping" in selected_opts:
            lines.append("* pong\n")
            lines.append("\n")

        if "man" in selected_opts:
            lines.extend(self._man_lines(system, man_requests, ctx.config))
            lines.append("\n")

        if "config" in selected_opts:
            defaults = self._defaults_map(system)
            printed_any = False

            for key in sorted(ctx.config.keys()):
                current_value = ctx.config[key]
                default_value = defaults.get(key, None)

                if key in defaults and current_value == default_value:
                    continue

                source = (
                    "file:" + ",".join(map(str, ctx.arg_lines.get(key, [])))
                    if key in ctx.arg_lines
                    else "config/default"
                )
                lines.append(
                    f"* {key} = {current_value} (default={default_value}, src={source})\n"
                )
                printed_any = True

            if not printed_any:
                lines.append("* (no differences from defaults)\n")

            lines.append("\n")

        if "event" in selected_opts:
            event = system.event
            lines.append(f"* type: {getattr(event, 'event_type', None)}\n")
            lines.append(f"* is_directory: {getattr(event, 'is_directory', None)}\n")
            lines.append(f"* src_path: {getattr(event, 'src_path', None)}\n")
            lines.append(f"* dest_path: {getattr(event, 'dest_path', None)}\n")
            lines.append(f"* ctx.path: {path}\n")
            lines.append("\n")

        return lines

    def _apply(self, *, ctx: Context, system: System) -> Optional[IgnoreMap]:
        line_to_opts: dict[int, set[str]] = {}
        line_to_remove_flags: dict[int, List[str]] = {}
        line_to_man_requests: dict[int, List[str]] = {}

        def add_option(lineno_1based: int, option_name: str, remove_flag: str) -> None:
            line_to_opts.setdefault(lineno_1based, set()).add(option_name)
            line_to_remove_flags.setdefault(lineno_1based, []).append(remove_flag)

        if ctx.config["mods"]:
            for lineno_1based in ctx.arg_lines.get("mods") or []:
                add_option(int(lineno_1based), "mods", "--mods")

        if ctx.config.get("ping"):
            for lineno_1based in ctx.arg_lines.get("ping") or []:
                add_option(int(lineno_1based), "ping", "--ping")

        if ctx.config["config"]:
            for lineno_1based in ctx.arg_lines.get("config") or []:
                add_option(int(lineno_1based), "config", "--config")

        if ctx.config["help"]:
            for lineno_1based in ctx.arg_lines.get("help") or []:
                add_option(int(lineno_1based), "help", "--help")

        if ctx.config["event"]:
            for lineno_1based in ctx.arg_lines.get("event") or []:
                add_option(int(lineno_1based), "event", "--event")

        man_lines = ctx.arg_lines.get("man") or []
        for man_value, lineno_1based in zip(ctx.config["man"], man_lines):
            lineno_int = int(lineno_1based)
            add_option(lineno_int, "man", "--man")
            if man_value.strip():
                line_to_man_requests.setdefault(lineno_int, []).append(
                    man_value.strip()
                )

        if not line_to_opts:
            return None

        if any("ping" in selected_opts for selected_opts in line_to_opts.values()):
            self._send_ping_notification(ctx)

        try:
            with open(ctx.path, "r", encoding="utf-8") as file_handle:
                file_lines = file_handle.readlines()
        except FileNotFoundError:
            file_lines = []

        if not file_lines:
            file_lines = ["\n"]

        for lineno_1based in sorted(line_to_opts.keys(), reverse=True):
            index = max(0, min(len(file_lines) - 1, lineno_1based - 1))
            selected_opts = line_to_opts[lineno_1based]
            remove_flags = line_to_remove_flags[lineno_1based]
            man_requests = line_to_man_requests.get(lineno_1based, [])

            if selected_opts == {"ping"}:
                self._apply_ping_rewrite(
                    file_lines=file_lines,
                    index=index,
                    remove_flags=remove_flags,
                )
                continue

            block = self._build_block(
                system=system,
                ctx=ctx,
                selected_opts=selected_opts,
                path=ctx.path,
                man_requests=man_requests,
            )

            if index == 0:
                cleaned_first_line = delete_args_from_string(
                    file_lines[0], remove_flags
                )
                if cleaned_first_line.strip() == "":
                    file_lines[0:1] = block
                    continue

                file_lines[0] = cleaned_first_line

                if file_lines[0].strip():
                    file_lines.insert(1, "\n")
                    insert_pos = 2
                else:
                    file_lines[0] = "\n"
                    insert_pos = 1

                file_lines[insert_pos:insert_pos] = ["---\n"] + block
                continue

            cleaned_line = delete_args_from_string(file_lines[index], remove_flags)
            file_lines[index : index + 1] = block
            if cleaned_line.strip():
                insert_at = index + len(block)
                file_lines[insert_at:insert_at] = [cleaned_line]

        with open(ctx.path, "w", encoding="utf-8") as file_handle:
            file_handle.writelines(file_lines)

        return {ctx.path: 1}

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)

    def deleted(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)
