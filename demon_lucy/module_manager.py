import logging
import os
import time
from typing import Dict, List

from watchdog.events import FileSystemEvent

from demon_lucy.lib.logfmt import ignore_summary, log_record, next_event_id
from demon_lucy.lib.notifications import safe_notify
from demon_lucy.lib.args.parser import (
    Template,
    flag_to_dest,
    get_args_from_file,
    merge_known_args,
    parse_template_item,
    parse_args,
)
from demon_lucy.lib.path import canonical_path, path_is_inside
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    RunMode,
    System,
)

logger = logging.getLogger(__name__)


class ModuleManager:
    def __init__(
        self,
        modules: List[AbstractModule],
        args,
        system_config: dict,
        run_mode: RunMode = "daemon",
    ):
        self.modules = modules
        self.run_mode: RunMode = run_mode
        self.template: Template = [
            (
                "--sys-modules-priority",
                str,
                [],
                "Override module execution order (lower runs first). "
                "Format: name=int. Example: --sys-modules-priority banner=5 renamer=20 todo=30",
                False,
            ),
        ]

        for module in self.modules:
            self.template.extend(module.template)

        template_defaults, _ = parse_args(args=[], template=self.template)
        inherited_system_config = dict(system_config)
        explicit_args, _ = parse_args(
            args=args,
            template=self.template,
            include_defaults=False,
        )
        self.config = merge_known_args(
            args=template_defaults,
            overwrite_args=inherited_system_config,
        )
        self.config = merge_known_args(
            args=self.config,
            overwrite_args=explicit_args,
        )
        priority_dict = self._parse_priority_list(self.config["sys_modules_priority"])
        self.modules.sort(key=lambda m: priority_dict.get(m.name, m.priority))

    def _is_blacklisted_path(self, path: str, values: list[str]) -> bool:
        for value in values or []:
            raw_value = str(value).strip()
            if not raw_value:
                continue
            if path_is_inside(path, raw_value):
                return True
        return False

    def _module_missing_required_flags(
        self,
        module: AbstractModule,
        config: dict,
    ) -> list[str]:
        missing_flags: list[str] = []
        for item in module.template:
            flag, _typ, _default, _desc, required = parse_template_item(item)
            if not required:
                continue
            dest = flag_to_dest(flag)
            value = config.get(dest)
            if value is None:
                missing_flags.append(flag)
                continue
            if isinstance(value, str) and not value.strip():
                missing_flags.append(flag)
                continue
            if isinstance(value, list) and len(value) == 0:
                missing_flags.append(flag)
        return missing_flags

    def _next_context_path(
        self,
        current_path: str,
        event_ignore: Dict[str, int] | None,
    ) -> str:
        if not event_ignore or os.path.exists(current_path):
            return current_path

        current_abs = canonical_path(current_path)
        candidates: list[str] = []
        for path_value in event_ignore:
            candidate_path = canonical_path(path_value)
            if candidate_path == current_abs:
                continue
            if not os.path.exists(candidate_path) or os.path.isdir(candidate_path):
                continue
            candidates.append(candidate_path)
        if len(candidates) != 1:
            return current_path
        return candidates[0]

    def run(
        self,
        path: str,
        event: FileSystemEvent,
        event_id: str | None = None,
    ) -> Dict[str, int] | None:
        event_id = event_id or next_event_id()
        event_type = str(event.event_type)

        if self._is_blacklisted_path(path, self.config["sys_ignore_paths"]):
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

        current_path = canonical_path(path)

        def _update_config(config_path: str):
            known_args, _, arg_lines = get_args_from_file(
                path=config_path,
                template=self.template,
            )
            merged_known_args = merge_known_args(
                args=self.config, overwrite_args=known_args
            )
            return merged_known_args, arg_lines

        config, arg_lines = _update_config(current_path)

        ignore_paths: Dict[str, int] = {}

        for module in self.modules:
            if event.event_type not in module.__class__.__dict__:  # not from parent
                continue

            missing_required = self._module_missing_required_flags(module, config)
            if missing_required:
                missing_text = ", ".join(missing_required)
                message = f"Skipping module '{module.name}': missing required args: {missing_text}"
                logger.error(
                    log_record(
                        "module.skip",
                        id=event_id,
                        module=module.name,
                        reason="missing_required_args",
                        missing=missing_text,
                        event=event_type,
                        path=current_path,
                    )
                )
                safe_notify(
                    f"module_missing_required:{module.name}",
                    message,
                    config=config,
                    use_rare_mode=True,
                )
                continue

            action = getattr(module, event.event_type)

            logger.info(
                log_record(
                    "module.start",
                    id=event_id,
                    module=module.name,
                    event=event_type,
                    path=current_path,
                )
            )
            started_at = time.monotonic()
            try:
                event_ignore = action(
                    Context(
                        path=current_path,
                        config=config,
                        arg_lines=arg_lines,
                    ),
                    System(
                        event=event,
                        global_template=self.template,
                        modules=self.modules,
                        run_mode=self.run_mode,
                        event_id=event_id,
                    ),
                )
            except Exception:
                logger.exception(
                    log_record(
                        "module.error",
                        id=event_id,
                        module=module.name,
                        event=event_type,
                        path=current_path,
                        duration_ms=(time.monotonic() - started_at) * 1000.0,
                    )
                )
                raise

            changed_paths_count, changed_events_count = ignore_summary(event_ignore)
            logger.info(
                log_record(
                    "module.done",
                    id=event_id,
                    module=module.name,
                    event=event_type,
                    path=current_path,
                    changed_paths=changed_paths_count,
                    changed_events=changed_events_count,
                    duration_ms=(time.monotonic() - started_at) * 1000.0,
                )
            )

            if event_ignore:
                for changed_path, times in event_ignore.items():
                    if not times:
                        continue
                    ignore_paths[changed_path] = ignore_paths.get(
                        changed_path, 0
                    ) + int(times)

                current_path = self._next_context_path(current_path, event_ignore)
                config, arg_lines = _update_config(current_path)

        return ignore_paths or None

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
