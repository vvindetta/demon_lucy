import logging
import os
from typing import Dict, List

from watchdog.events import FileSystemEvent

from demon_lucy.lib import safe_notify
from demon_lucy.lib.args import (
    Template,
    flag_to_dest,
    get_args_from_file,
    merge_known_args,
    parse_template_item,
    parse_args,
)
from demon_lucy.lib.path import canonical_path
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
                "--modules-priority",
                str,
                [],
                "Override module execution order (lower runs first). "
                "Format: name=int. Example: --modules-priority banner=5 renamer=20 todo=30",
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
        priority_dict = self._parse_priority_list(self.config["modules_priority"])
        self.modules.sort(key=lambda m: priority_dict.get(m.name, m.priority))

    def _is_blacklisted_path(self, path: str, values: list[str]) -> bool:
        normalized_path = canonical_path(path)
        for value in values or []:
            raw_value = str(value).strip()
            if not raw_value:
                continue
            blacklisted_path = canonical_path(raw_value)
            if normalized_path == blacklisted_path:
                return True
            if normalized_path.startswith(blacklisted_path + os.sep):
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

    def run(self, path: str, event: FileSystemEvent) -> Dict[str, int] | None:
        if self._is_blacklisted_path(path, self.config["sys_ignore_paths"]):
            logger.debug("SKIPPED BLACKLISTED PATH: %s", path)
            return None

        def _update_config():
            known_args, _, arg_lines = get_args_from_file(
                path=path,
                template=self.template,
            )
            merged_known_args = merge_known_args(
                args=self.config, overwrite_args=known_args
            )
            return merged_known_args, arg_lines

        config, arg_lines = _update_config()

        ignore_paths: Dict[str, int] = {}

        for module in self.modules:
            if event.event_type not in module.__class__.__dict__:  # not from parent
                continue

            missing_required = self._module_missing_required_flags(module, config)
            if missing_required:
                missing_text = ", ".join(missing_required)
                message = f"Skipping module '{module.name}': missing required args: {missing_text}"
                logger.error(message)
                safe_notify(
                    f"module_missing_required:{module.name}",
                    message,
                    config=config,
                    use_rare_mode=True,
                )
                continue

            action = getattr(module, event.event_type)

            logger.info(f"STARTING: {module.name}")
            event_ignore = action(
                Context(
                    path=path,
                    config=config,
                    arg_lines=arg_lines,
                ),
                System(
                    event=event,
                    global_template=self.template,
                    modules=self.modules,
                    run_mode=self.run_mode,
                ),
            )
            logger.info(f"END: {module.name}")

            if event_ignore:
                for path, times in event_ignore.items():
                    if not times:
                        continue
                    ignore_paths[path] = ignore_paths.get(path, 0) + int(times)

                config, arg_lines = _update_config()

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
                    "Invalid --modules-priority arg. Example: --modules-priority banner=5 renamer=20 todo=30"
                )

            name, raw = item.split("=", 1)
            name = name.strip()
            raw = raw.strip()

            if not name:
                raise ValueError(f"Invalid --priority item '{item}': empty module name")

            try:
                pr = int(raw)
            except ValueError:
                raise ValueError(
                    f"Invalid --priority item '{item}': priority must be an integer"
                )

            priorities[name] = pr

        return priorities
