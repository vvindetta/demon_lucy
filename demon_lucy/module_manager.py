import logging
import os
import time
from dataclasses import replace
from typing import Dict, List

from watchdog.events import FileSystemEvent

from demon_lucy.lib.args.models import (
    ArgSource,
    KnownArg,
    ParsedArgs,
    Template,
)
from demon_lucy.lib.args.parser import (
    parse_args,
    resolve_unknown_args,
)
from demon_lucy.lib.args.sources import parse_note_args
from demon_lucy.lib.logfmt import ignore_summary, log_record, next_event_id
from demon_lucy.lib.notifications import safe_notify
from demon_lucy.lib.path import canonical_path, path_is_inside
from demon_lucy.lib.operating_system import (
    OperatingSystem,
    detect_operating_system,
)
from demon_lucy.lib.text_file import write_text_atomic
from demon_lucy.lib.dynamic_blocks.model import DynamicBlockRenderer
from demon_lucy.lib.dynamic_blocks.refresh import refresh_dynamic_blocks
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    ModuleResult,
    RunMode,
    System,
)
from demon_lucy.runtime import DEMON_LUCY_STARTUP_TEMPLATE

logger = logging.getLogger(__name__)


class ModuleManager:
    def __init__(
        self,
        modules: List[AbstractModule],
        startup_args: ParsedArgs,
        run_mode: RunMode = "daemon",
    ):
        self.modules = modules
        self.run_mode: RunMode = run_mode
        self.operating_system: OperatingSystem = detect_operating_system()
        self.runtime_started_at_monotonic = time.monotonic()
        self.dynamic_block_renderers = self._collect_dynamic_block_renderers(
            self.modules
        )
        self.template: Template = [
            KnownArg(
                name="sys-modules-priority",
                value_type=str,
                default=[],
                description="Override module execution order (lower runs first). "
                "Format: name=int. Example: --sys-modules-priority banner=5 renamer=20 todo=30",
            ),
        ]
        self.template.extend(DEMON_LUCY_STARTUP_TEMPLATE)

        for module in self.modules:
            self.template.extend(module.template)

        template_defaults = parse_args(
            args=[],
            template=self.template,
        )
        config_module_args = resolve_unknown_args(
            args=startup_args.unknown_from(ArgSource.CONFIG),
            template=self.template,
        )
        explicit_args = resolve_unknown_args(
            args=startup_args.unknown_from(ArgSource.CLI),
            template=self.template,
        )
        merged_args = template_defaults.merged_with(
            ParsedArgs(
                known=startup_args.known,
            ),
        )
        merged_args = merged_args.merged_with(config_module_args)
        merged_args = merged_args.merged_with(explicit_args)
        self.args = merged_args
        priority_dict = self._parse_priority_list(
            self.args.require("sys-modules-priority").value
        )
        self.modules.sort(key=lambda m: priority_dict.get(m.name, m.priority))

    @staticmethod
    def _merge_changes(
        target: dict[str, int],
        item: dict[str, int],
    ) -> None:
        if not item:
            return
        for changed_path, times in item.items():
            if not times:
                continue
            target[changed_path] = target.get(changed_path, 0) + int(times)

    @staticmethod
    def _collect_dynamic_block_renderers(
        modules: List[AbstractModule],
    ) -> dict[str, DynamicBlockRenderer]:
        renderers: dict[str, DynamicBlockRenderer] = {}
        owners: dict[str, str] = {}
        for module in modules:
            for arg, renderer in module.dynamic_block_renderers.items():
                if arg in renderers:
                    raise ValueError(
                        f"Duplicate dynamic block arg '{arg}' in modules "
                        f"'{owners[arg]}' and '{module.name}'"
                    )
                renderers[arg] = renderer
                owners[arg] = module.name
        return renderers

    def _refresh_dynamic_blocks(
        self,
        *,
        path: str,
        event_id: str,
        event_type: str,
        args: ParsedArgs,
    ) -> bool:
        if event_type not in {"created", "modified", "moved"}:
            return False
        if not self.dynamic_block_renderers:
            return False
        if not os.path.isfile(path) or os.path.islink(path):
            return False

        try:
            with open(path, "r", encoding="utf-8", newline="") as handle:
                text = handle.read()
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning(
                log_record(
                    "dynamic_block.refresh_skipped",
                    id=event_id,
                    path=path,
                    reason="file_unreadable",
                    error=exc,
                )
            )
            return False

        try:
            refreshed, changed_blocks = refresh_dynamic_blocks(
                text=text,
                target_path=path,
                renderers=self.dynamic_block_renderers,
                args=args,
                event_id=event_id,
            )
        except ValueError as exc:
            logger.warning(
                log_record(
                    "dynamic_block.parse_failed",
                    id=event_id,
                    path=path,
                    reason="invalid_structure",
                    error=exc,
                )
            )
            return False
        if changed_blocks == 0:
            return False

        try:
            write_text_atomic(path, refreshed)
        except OSError as exc:
            logger.error(
                log_record(
                    "dynamic_block.write_failed",
                    id=event_id,
                    path=path,
                    error=exc,
                )
            )
            safe_notify(
                f"dynamic-block-write:{path}",
                f"Dynamic block write failed for {path}: {exc}",
                args=args,
                use_rare_mode=True,
            )
            return False

        logger.info(
            log_record(
                "dynamic_block.refresh_done",
                id=event_id,
                path=path,
                changed=changed_blocks,
            )
        )
        return True

    def _is_blacklisted_path(self, path: str, values: list[str]) -> bool:
        for value in values:
            raw_value = value.strip()
            if not raw_value:
                continue
            if path_is_inside(path, raw_value):
                return True
        return False

    def _module_missing_required_flags(
        self,
        module: AbstractModule,
        parsed_args: ParsedArgs,
    ) -> list[str]:
        return [
            item.name
            for item in module.template
            if item.required and not parsed_args.require(item.name).value
        ]

    def run(
        self,
        path: str,
        event: FileSystemEvent,
        event_id: str | None = None,
    ) -> Dict[str, int] | None:
        event_id = event_id or next_event_id()
        event_type = str(event.event_type)

        if self._is_blacklisted_path(
            path,
            self.args.require("sys-ignore-paths").value,
        ):
            logger.info(
                log_record(
                    "event.skip",
                    id=event_id,
                    reason="ignored_path",
                    event=event_type,
                    path=path,
                )
            )
            return None

        current_context = Context(
            path=canonical_path(path),
            args=self.args,
            run_mode=self.run_mode,
            event_id=event_id,
            event=event,
        )

        def _update_args(config_path: str) -> ParsedArgs:
            file_args = parse_note_args(
                path=config_path,
                template=self.template,
            )
            return self.args.merged_with(file_args)

        current_context = replace(
            current_context,
            args=_update_args(current_context.path),
        )

        ignore_paths: Dict[str, int] = {}

        for module in self.modules:
            action = getattr(type(module), event_type)
            if action is getattr(AbstractModule, event_type):
                continue

            missing_required = self._module_missing_required_flags(
                module,
                current_context.args,
            )
            if missing_required:
                missing_text = ", ".join(f"--{name}" for name in missing_required)
                message = f"Skipping module '{module.name}': missing required args: {missing_text}"
                logger.error(
                    log_record(
                        "module.skip",
                        id=event_id,
                        module=module.name,
                        reason="missing_required_args",
                        missing=missing_text,
                        event=event_type,
                        path=current_context.path,
                    )
                )
                safe_notify(
                    f"module_missing_required:{module.name}",
                    message,
                    args=current_context.args,
                    use_rare_mode=True,
                )
                continue

            logger.info(
                log_record(
                    "module.start",
                    id=event_id,
                    module=module.name,
                    event=event_type,
                    path=current_context.path,
                )
            )
            started_at = time.monotonic()
            try:
                module_result: ModuleResult | None = action(
                    module,
                    current_context,
                    System(
                        global_template=self.template,
                        modules=self.modules,
                        operating_system=self.operating_system,
                        runtime_started_at_monotonic=self.runtime_started_at_monotonic,
                    ),
                )
            except Exception:
                logger.exception(
                    log_record(
                        "module.error",
                        id=event_id,
                        module=module.name,
                        event=event_type,
                        path=current_context.path,
                        duration_ms=(time.monotonic() - started_at) * 1000.0,
                    )
                )
                raise

            changed = module_result.changed if module_result is not None else {}
            changed_paths_count, changed_events_count = ignore_summary(changed)
            logger.info(
                log_record(
                    "module.done",
                    id=event_id,
                    module=module.name,
                    event=event_type,
                    path=current_context.path,
                    changed_paths=changed_paths_count,
                    changed_events=changed_events_count,
                    duration_ms=(time.monotonic() - started_at) * 1000.0,
                )
            )

            if module_result is not None:
                self._merge_changes(ignore_paths, module_result.changed)
                result_context = module_result.context
                current_context = replace(
                    result_context,
                    path=canonical_path(result_context.path),
                    args=_update_args(result_context.path),
                )

        if self._refresh_dynamic_blocks(
            path=current_context.path,
            event_id=event_id,
            event_type=event_type,
            args=current_context.args,
        ):
            ignore_paths[current_context.path] = (
                ignore_paths.get(current_context.path, 0) + 1
            )

        return ignore_paths or None

    def run_cli(
        self,
        event_id: str | None = None,
    ) -> tuple[dict[str, int] | None, int]:
        unknown_args = self.args.unknown_from(ArgSource.CLI)
        if unknown_args:
            raise ValueError(
                "Unknown CLI arguments: "
                + " ".join(argument.token for argument in unknown_args)
            )

        event_id = event_id or next_event_id()
        cwd = canonical_path(os.getcwd())
        current_context = Context(
            path=cwd,
            args=self.args,
            run_mode="cli",
            event_id=event_id,
        )
        ignore_paths: dict[str, int] = {}
        modules_run = 0

        for module in self.modules:
            if type(module).cli is AbstractModule.cli:
                continue

            cli_args = [
                argument
                for item in module.template
                if (argument := current_context.args.find(item.name)) is not None
                and argument.source is ArgSource.CLI
            ]
            if not cli_args:
                continue

            missing_required = self._module_missing_required_flags(
                module,
                current_context.args,
            )
            if missing_required:
                missing_text = ", ".join(f"--{name}" for name in missing_required)
                logger.error(
                    log_record(
                        "module.skip",
                        id=event_id,
                        module=module.name,
                        mode="cli",
                        reason="missing_required_args",
                        missing=missing_text,
                        path=current_context.path,
                    )
                )
                safe_notify(
                    f"module_missing_required:{module.name}",
                    f"Skipping module '{module.name}': "
                    f"missing required args: {missing_text}",
                    args=current_context.args,
                    use_rare_mode=True,
                )
                continue

            logger.info(
                log_record(
                    "module.start",
                    id=event_id,
                    module=module.name,
                    mode="cli",
                    args=[argument.name for argument in cli_args],
                    path=current_context.path,
                )
            )
            started_at = time.monotonic()
            modules_run += 1
            try:
                module_result = module.cli(
                    current_context,
                    System(
                        global_template=self.template,
                        modules=self.modules,
                        operating_system=self.operating_system,
                        runtime_started_at_monotonic=self.runtime_started_at_monotonic,
                    ),
                )
            except Exception:
                logger.exception(
                    log_record(
                        "module.error",
                        id=event_id,
                        module=module.name,
                        mode="cli",
                        path=current_context.path,
                        duration_ms=(time.monotonic() - started_at) * 1000.0,
                    )
                )
                raise

            changed = module_result.changed if module_result is not None else {}
            changed_paths_count, changed_events_count = ignore_summary(changed)
            logger.info(
                log_record(
                    "module.done",
                    id=event_id,
                    module=module.name,
                    mode="cli",
                    path=current_context.path,
                    changed_paths=changed_paths_count,
                    changed_events=changed_events_count,
                    duration_ms=(time.monotonic() - started_at) * 1000.0,
                )
            )
            if module_result is not None:
                self._merge_changes(ignore_paths, module_result.changed)
                current_context = replace(
                    module_result.context,
                    path=canonical_path(module_result.context.path),
                )

        return ignore_paths or None, modules_run

    def _parse_priority_list(self, values: List[str]) -> Dict[str, int]:
        """
        Values example: ["banner=5", "renamer=20", "todo=30"]

        Returns: {"banner": 5, "renamer": 20, "todo": 30}
        """
        priorities: Dict[str, int] = {}

        if not values:
            return priorities

        for item in values:
            if "=" not in item:
                raise ValueError(
                    "Invalid --sys-modules-priority arg. Example: --sys-modules-priority banner=5 renamer=20 todo=30"
                )

            name, raw = item.split("=", 1)
            name = name.strip()
            raw = raw.strip()

            if not name:
                raise ValueError(
                    f"Invalid --sys-modules-priority item '{item}': empty module name"
                )

            try:
                pr = int(raw)
            except ValueError:
                raise ValueError(
                    f"Invalid --sys-modules-priority item '{item}': priority must be an integer"
                )

            priorities[name] = pr

        return priorities
