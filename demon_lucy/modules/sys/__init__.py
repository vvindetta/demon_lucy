from __future__ import annotations

import time
from datetime import datetime
from typing import Any, List

from demon_lucy.lib.args.line_edit import delete_args_from_string
from demon_lucy.lib.args.models import ArgSource, KnownArg
from demon_lucy.lib.notifications import safe_notify
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    ModuleResult,
    System,
)
from demon_lucy.modules.sys.neofetch import git_sync_age_text, neofetch_lines


class Sys(AbstractModule):
    name: str = "sys"
    priority: int = 2

    template = [
        KnownArg(
            name="neofetch",
            value_type=bool,
            default=False,
            description="Print Demon Lucy runtime information.",
        ),
        KnownArg(
            name="mods",
            value_type=bool,
            default=False,
            description="Print loaded modules and their priorities.",
        ),
        KnownArg(
            name="ping",
            value_type=bool,
            default=False,
            description="Health-check: sends notification and writes pong.",
        ),
        KnownArg(
            name="config",
            value_type=bool,
            default=False,
            description="Print config values that differ from defaults (and where they were set).",
        ),
        KnownArg(
            name="man",
            value_type=str,
            default=[],
            description="Print one argument with description (example: --man mods or --man --mods).",
        ),
        KnownArg(
            name="help",
            value_type=bool,
            default=False,
            description="Print Sys module command help.",
        ),
        KnownArg(
            name="event",
            value_type=bool,
            default=False,
            description="Print current filesystem event details.",
        ),
    ]

    @staticmethod
    def _type_name(type_value: Any) -> str:
        return getattr(type_value, "__name__", str(type_value))

    @staticmethod
    def _command_help_lines() -> List[str]:
        return [
            "* --mods: print loaded modules and their priorities\n",
            "* --ping: send notification and rewrite command line to ++pong!\n",
            "* --config: print config values that differ from defaults\n",
            "* --man <name>: print one argument with description (example: --man mods or --man --mods)\n",
            "* --event: print current filesystem event details\n",
            "* --neofetch: print Demon Lucy runtime information\n",
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
            args=ctx.args,
            title="Demon Lucy ping",
            use_rare_mode=False,
        )

    @staticmethod
    def _normalize_arg_name(raw: str) -> str:
        text = raw.strip()
        if not text:
            return ""
        return text.lstrip("-").strip().lower()

    def _expand_man_requests(self, requested_names: List[str]) -> List[str]:
        expanded: List[str] = []
        for raw in requested_names:
            for chunk in raw.split("/"):
                normalized = self._normalize_arg_name(chunk)
                if normalized:
                    expanded.append(normalized)
        return expanded

    def _module_flags_by_request_name(
        self,
        system: System,
    ) -> dict[str, set[str]]:
        mapping: dict[str, set[str]] = {}

        for module in system.modules:
            module_name = self._normalize_arg_name(module.name)
            if not module_name:
                continue

            module_keys = {module_name, module_name.replace("_", "-")}
            module_flags = {
                self._normalize_arg_name(template_item.name)
                for template_item in module.template
            }

            for key in module_keys:
                mapping.setdefault(key, set()).update(module_flags)

        system_flags = self._system_flag_names(system)
        if system_flags:
            mapping["sys"] = set(system_flags)

        return mapping

    def _system_flag_names(
        self,
        system: System,
    ) -> set[str]:
        return {
            template_item.name
            for template_item in system.global_template
            if template_item.name.startswith("sys-")
        }

    def _man_one_lines(
        self,
        system: System,
        requested_names: List[str],
    ) -> List[str]:
        requested = self._expand_man_requests(requested_names)
        if not requested:
            return ["* (missing name: use --man <name> or --man --flag)\n"]

        requested_set = set(requested)
        module_flags_map = self._module_flags_by_request_name(system)
        for request_name in list(requested_set):
            requested_set.update(module_flags_map.get(request_name, set()))
        matched: List[str] = []

        for item in system.global_template:
            if item.name.lower() in requested_set:
                type_name = self._type_name(item.value_type)
                description = (item.description or "").strip()
                matched.append(
                    f"* --{item.name}: {description} "
                    f"(type={type_name}, default={item.default})\n"
                )

        if matched:
            return matched

        return [f"* (unknown arg: {', '.join(requested)})\n"]

    def _build_block(
        self,
        *,
        system: System,
        ctx: Context,
        selected_opts: set[str],
        path: str,
        man_requests: List[str],
    ) -> List[str]:
        ordered = ["neofetch", "mods", "ping", "help", "man", "config", "event"]
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

        if "neofetch" in selected_opts:
            watch_paths = ctx.args.require("sys-watch-paths").value
            lines.extend(
                neofetch_lines(
                    run_mode=ctx.run_mode,
                    operating_system=system.operating_system,
                    module_count=len(system.modules),
                    watch_path_count=len(watch_paths),
                    opened_events_disabled=ctx.args.require(
                        "sys-disable-opened-events"
                    ).value,
                    git_sync_age=git_sync_age_text(ctx.path),
                    runtime_uptime_seconds=max(
                        0.0,
                        time.monotonic() - system.runtime_started_at_monotonic,
                    ),
                )
            )
            lines.append("\n")

        if "mods" in selected_opts:
            for module in system.modules:
                lines.append(f"* {module.name} ({getattr(module, 'priority', None)})\n")
            lines.append("\n")

        if "ping" in selected_opts:
            lines.append("* pong\n")
            lines.append("\n")

        if "man" in selected_opts:
            lines.extend(self._man_one_lines(system, man_requests))
            lines.append("\n")

        if "config" in selected_opts:
            printed_any = False

            for argument in sorted(ctx.args.known, key=lambda item: item.name):
                if argument.value == argument.default:
                    continue

                source = argument.source.value if argument.source else "unknown"
                if argument.source is ArgSource.FILE:
                    source += ":" + ",".join(map(str, argument.lines))
                lines.append(
                    f"* {argument.name} = {argument.value} "
                    f"(default={argument.default}, src={source})\n"
                )
                printed_any = True

            if not printed_any:
                lines.append("* (no differences from defaults)\n")

            lines.append("\n")

        if "event" in selected_opts:
            event = ctx.event
            lines.append(f"* type: {getattr(event, 'event_type', None)}\n")
            lines.append(f"* is_directory: {getattr(event, 'is_directory', None)}\n")
            lines.append(f"* src_path: {getattr(event, 'src_path', None)}\n")
            lines.append(f"* dest_path: {getattr(event, 'dest_path', None)}\n")
            lines.append(f"* ctx.path: {path}\n")
            lines.append("\n")

        return lines

    def _apply(self, *, ctx: Context, system: System) -> dict[str, int] | None:
        line_to_opts: dict[int, set[str]] = {}
        line_to_remove_flags: dict[int, List[str]] = {}
        line_to_man_requests: dict[int, List[str]] = {}

        def add_option(lineno_1based: int, option_name: str, remove_flag: str) -> None:
            line_to_opts.setdefault(lineno_1based, set()).add(option_name)
            line_to_remove_flags.setdefault(lineno_1based, []).append(remove_flag)

        if ctx.args.require("neofetch").value:
            for line_number in ctx.args.require("neofetch").lines:
                add_option(line_number, "neofetch", "--neofetch")

        if ctx.args.require("mods").value:
            for line_number in ctx.args.require("mods").lines:
                add_option(line_number, "mods", "--mods")

        if ctx.args.require("ping").value:
            for line_number in ctx.args.require("ping").lines:
                add_option(line_number, "ping", "--ping")

        if ctx.args.require("config").value:
            for line_number in ctx.args.require("config").lines:
                add_option(line_number, "config", "--config")

        if ctx.args.require("help").value:
            for line_number in ctx.args.require("help").lines:
                add_option(line_number, "help", "--help")

        if ctx.args.require("event").value:
            for line_number in ctx.args.require("event").lines:
                add_option(line_number, "event", "--event")

        man_arg = ctx.args.require("man")
        for man_value, line_number in zip(man_arg.value, man_arg.lines):
            add_option(line_number, "man", "--man")
            if man_value.strip():
                line_to_man_requests.setdefault(line_number, []).append(
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

    def created(self, ctx: Context, system: System) -> ModuleResult | None:
        changed = self._apply(ctx=ctx, system=system)
        return ModuleResult(context=ctx, changed=changed) if changed else None

    def modified(self, ctx: Context, system: System) -> ModuleResult | None:
        changed = self._apply(ctx=ctx, system=system)
        return ModuleResult(context=ctx, changed=changed) if changed else None

    def moved(self, ctx: Context, system: System) -> ModuleResult | None:
        changed = self._apply(ctx=ctx, system=system)
        return ModuleResult(context=ctx, changed=changed) if changed else None
