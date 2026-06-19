from __future__ import annotations

import logging
import sys
import time
from typing import Sequence

from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileOpenedEvent,
    FileSystemEvent,
)

from demon_lucy.lib.args.parser import Template, parse_args, setup_config_and_cli_args
from demon_lucy.lib.logfmt import (
    event_paths,
    ignore_summary,
    log_record,
    next_event_id,
)
from demon_lucy.lib.path import abs_expand_path
from demon_lucy.module_manager import ModuleManager
from demon_lucy.runtime import (
    DEMON_LUCY_STARTUP_TEMPLATE,
    configure_logging,
    log_startup_message,
    normalize_name_list,
    run_config_migrations,
    select_demon_lucy_modules,
)

ONESHOT_STARTUP_TEMPLATE: Template = DEMON_LUCY_STARTUP_TEMPLATE + [
    (
        "--oneshot-event",
        str,
        "modified",
        "Single event to trigger once. Allowed: created modified moved deleted opened.",
        False,
    ),
    (
        "--oneshot-paths",
        str,
        [],
        "One or more file or directory paths to process in one-shot mode.",
        False,
    ),
    (
        "--oneshot-move-src-path",
        str,
        "",
        "Source path for moved event.",
        False,
    ),
    (
        "--oneshot-move-dest-path",
        str,
        "",
        "Destination path for moved event.",
        False,
    ),
]

_ALLOWED_EVENTS = {"created", "modified", "moved", "deleted", "opened"}


def _build_event(
    event_name: str, src_path: str, dest_path: str = ""
) -> FileSystemEvent:
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

    target_paths = [abs_expand_path(path_item) for path_item in config["oneshot_paths"]]
    moved_src = str(config["oneshot_move_src_path"]).strip()
    moved_dest = str(config["oneshot_move_dest_path"]).strip()

    if event_name == "moved":
        if not moved_src or not moved_dest:
            raise ValueError(
                "Moved event requires both --oneshot-move-src-path and --oneshot-move-dest-path."
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
            "One-shot event requires --oneshot-paths unless using moved event."
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
    modules = select_demon_lucy_modules(
        include_names=config["sys_modules"],
        exclude_names=config["sys_modules_exclude"],
    )
    manager = ModuleManager(
        modules=modules,
        args=list(unknown_args),
        system_config=config,
        run_mode="oneshot",
    )

    plan = _build_event_plan(config)
    first_event_type = plan[0][1].event_type if plan else "none"
    log_startup_message(
        run_mode="oneshot",
        modules=modules,
        config=config,
        unknown_args=list(unknown_args),
        extra_items=[
            ("events", len(plan)),
            ("event_type", first_event_type),
        ],
    )
    for path_value, event in plan:
        event_id = next_event_id()
        event_type = str(event.event_type)
        path_fields = event_paths(event, path_value)
        started_at = time.monotonic()
        ignore_paths = None
        status = "ok"

        logging.info(
            log_record(
                "event.start",
                id=event_id,
                mode="oneshot",
                source="synthetic",
                event=event_type,
                **path_fields,
            )
        )
        try:
            ignore_paths = manager.run(
                path=path_value,
                event=event,
                event_id=event_id,
            )
        except Exception:
            status = "error"
            logging.error(
                log_record(
                    "event.error",
                    id=event_id,
                    mode="oneshot",
                    event=event_type,
                    **path_fields,
                )
            )
            raise
        finally:
            changed_paths_count, changed_events_count = ignore_summary(ignore_paths)
            logging.info(
                log_record(
                    "event.done",
                    id=event_id,
                    mode="oneshot",
                    event=event_type,
                    status=status,
                    changed_paths=changed_paths_count,
                    changed_events=changed_events_count,
                    duration_ms=(time.monotonic() - started_at) * 1000.0,
                    **path_fields,
                )
            )

    logging.info(log_record("oneshot.done", events=len(plan), modules=len(modules)))
    return 0


def main() -> int:
    try:
        startup_args, _unknown_startup_args = parse_args(
            template=ONESHOT_STARTUP_TEMPLATE,
            args=sys.argv[1:],
        )
        config_path = startup_args.get("sys_config_path")
        if isinstance(config_path, str) and config_path.strip():
            run_config_migrations(config_path)
        config, unknown_args = setup_config_and_cli_args(
            template=ONESHOT_STARTUP_TEMPLATE
        )
        return run_oneshot(config=config, unknown_args=unknown_args)
    except (ValueError, KeyError) as exc:
        logging.basicConfig(level=logging.ERROR, force=True)
        logging.error(log_record("runtime.config_error", error=exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
