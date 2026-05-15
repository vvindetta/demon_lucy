from __future__ import annotations

import logging
from typing import Sequence

from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileOpenedEvent,
    FileSystemEvent,
)

from lucy_notes_manager.lib.args import Template, setup_config_and_cli_args
from lucy_notes_manager.lib.path import abs_expand_path
from lucy_notes_manager.module_manager import ModuleManager
from lucy_notes_manager.modules.git import set_oneshot_mode
from lucy_notes_manager.runtime import (
    LUCY_STARTUP_TEMPLATE,
    configure_logging,
    normalize_name_list,
    select_lucy_modules,
)

ONESHOT_STARTUP_TEMPLATE: Template = LUCY_STARTUP_TEMPLATE + [
    (
        "--oneshot-event",
        str,
        "modified",
        "Single event to trigger once. Allowed: created modified moved deleted opened.",
    ),
    (
        "--oneshot-path",
        str,
        [],
        "One or more file or directory paths to process in one-shot mode.",
    ),
    (
        "--oneshot-src-path",
        str,
        "",
        "Source path for moved event.",
    ),
    (
        "--oneshot-dest-path",
        str,
        "",
        "Destination path for moved event.",
    ),
    (
        "--oneshot-include-modules",
        str,
        [],
        "Run only these modules by name. Example: --oneshot-include-modules git sys",
    ),
]

_ALLOWED_EVENTS = {"created", "modified", "moved", "deleted", "opened"}


def _select_modules(config: dict):
    modules = select_lucy_modules(
        include_experimental=config["sys_enable_experimental_modules"],
        include_names=config["oneshot_include_modules"],
    )

    return modules


def _build_event(event_name: str, src_path: str, dest_path: str = "") -> FileSystemEvent:
    factories: dict[str, type[FileSystemEvent]] = {
        "created": FileCreatedEvent,
        "modified": FileModifiedEvent,
        "deleted": FileDeletedEvent,
        "opened": FileOpenedEvent,
    }
    if event_name == "moved":
        return FileMovedEvent(src_path=src_path, dest_path=dest_path, is_synthetic=True)
    return factories[event_name](src_path=src_path, is_synthetic=True)


def _build_event_plan(config: dict) -> list[tuple[str, FileSystemEvent]]:
    raw_event = config.get("oneshot_event", "modified")
    event_values = normalize_name_list([raw_event])
    if not event_values:
        event_values = ["modified"]
    if len(event_values) != 1:
        raise ValueError(
            "Only one --oneshot-event is supported. Use one of: created modified moved deleted opened."
        )
    event_name = event_values[0]
    if event_name not in _ALLOWED_EVENTS:
        raise ValueError(f"Unsupported --oneshot-event value: {event_name}")

    target_paths = [abs_expand_path(path_item) for path_item in config["oneshot_path"]]
    moved_src = str(config["oneshot_src_path"]).strip()
    moved_dest = str(config["oneshot_dest_path"]).strip()

    if event_name == "moved":
        if not moved_src or not moved_dest:
            raise ValueError(
                "Moved event requires both --oneshot-src-path and --oneshot-dest-path."
            )
        moved_src_abs = abs_expand_path(moved_src)
        moved_dest_abs = abs_expand_path(moved_dest)
        return [
            (
                moved_dest_abs,
                _build_event(
                    event_name="moved",
                    src_path=moved_src_abs,
                    dest_path=moved_dest_abs,
                ),
            )
        ]

    if not target_paths:
        raise ValueError(
            "One-shot event requires --oneshot-path unless using moved event."
        )

    plan: list[tuple[str, FileSystemEvent]] = []
    for path_item in target_paths:
        plan.append(
            (
                path_item,
                _build_event(event_name=event_name, src_path=path_item),
            )
        )

    return plan


def run_oneshot(config: dict, unknown_args: Sequence[str]) -> int:
    configure_logging(config)
    set_oneshot_mode(True)
    try:
        modules = _select_modules(config)
        manager = ModuleManager(modules=modules, args=list(unknown_args))

        plan = _build_event_plan(config)
        for path_value, event in plan:
            logging.info("ONESHOT EVENT: %s %s", event.event_type, path_value)
            manager.run(path=path_value, event=event)

        logging.info("ONESHOT DONE: events=%d modules=%d", len(plan), len(modules))
        return 0
    finally:
        set_oneshot_mode(False)


def main() -> int:
    config, unknown_args = setup_config_and_cli_args(template=ONESHOT_STARTUP_TEMPLATE)
    try:
        return run_oneshot(config=config, unknown_args=unknown_args)
    except ValueError as exc:
        logging.basicConfig(level=logging.ERROR, force=True)
        logging.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
