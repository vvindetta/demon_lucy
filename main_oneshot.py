from __future__ import annotations

import logging
import sys
import time
from enum import StrEnum

from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileOpenedEvent,
    FileSystemEvent,
)

from demon_lucy.lib.args.models import (
    KnownArg,
    ParsedArgs,
    Template,
)
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.lib.args.sources import load_args
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
    run_config_migrations,
    select_demon_lucy_modules,
)


class OneShotEvent(StrEnum):
    CREATED = "created"
    MODIFIED = "modified"
    MOVED = "moved"
    DELETED = "deleted"
    OPENED = "opened"


ONESHOT_STARTUP_TEMPLATE: Template = DEMON_LUCY_STARTUP_TEMPLATE + [
    KnownArg(
        name="oneshot-event",
        value_type=OneShotEvent,
        default=OneShotEvent.MODIFIED,
        description=(
            "Single event to trigger once. Allowed: created modified moved "
            "deleted opened."
        ),
        required=False,
    ),
    KnownArg(
        name="oneshot-paths",
        value_type=str,
        default=[],
        description="One or more file or directory paths to process in one-shot mode.",
        required=False,
    ),
    KnownArg(
        name="oneshot-move-src-path",
        value_type=str,
        default="",
        description="Source path for moved event.",
        required=False,
    ),
    KnownArg(
        name="oneshot-move-dest-path",
        value_type=str,
        default="",
        description="Destination path for moved event.",
        required=False,
    ),
]


def _build_event(
    event_name: OneShotEvent,
    src_path: str,
    dest_path: str = "",
) -> FileSystemEvent:
    factories: dict[OneShotEvent, type[FileSystemEvent]] = {
        OneShotEvent.CREATED: FileCreatedEvent,
        OneShotEvent.MODIFIED: FileModifiedEvent,
        OneShotEvent.DELETED: FileDeletedEvent,
        OneShotEvent.OPENED: FileOpenedEvent,
    }
    if event_name is OneShotEvent.MOVED:
        return FileMovedEvent(src_path=src_path, dest_path=dest_path, is_synthetic=True)
    return factories[event_name](src_path=src_path, is_synthetic=True)


def _build_event_plan(args: ParsedArgs) -> list[tuple[str, FileSystemEvent]]:
    event_name: OneShotEvent = args.require("oneshot-event").value

    target_paths = [
        abs_expand_path(path_item)
        for path_item in args.require("oneshot-paths").value
    ]
    moved_src = args.require("oneshot-move-src-path").value.strip()
    moved_dest = args.require("oneshot-move-dest-path").value.strip()

    if event_name is OneShotEvent.MOVED:
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
                    event_name=OneShotEvent.MOVED,
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


def _uses_cli_flow(args: ParsedArgs) -> bool:
    if args.require("oneshot-paths").value:
        return False
    return args.require("oneshot-event").value is not OneShotEvent.MOVED


def _run_cli_flow(
    *,
    manager: ModuleManager,
    modules: list,
    args: ParsedArgs,
) -> int:
    event_id = next_event_id()
    log_startup_message(
        run_mode="cli",
        modules=modules,
        args=args,
        extra_items=[("flow", "cli")],
    )

    started_at = time.monotonic()
    status = "ok"
    ignore_paths = None
    modules_run = 0
    logging.info(log_record("cli_run.start", id=event_id, mode="cli"))
    try:
        ignore_paths, modules_run = manager.run_cli(event_id=event_id)
        if modules_run == 0:
            raise ValueError(
                "CLI run requires at least one module argument."
            )
    except Exception:
        status = "error"
        logging.error(log_record("cli_run.error", id=event_id, mode="cli"))
        raise
    finally:
        changed_paths_count, changed_events_count = ignore_summary(ignore_paths)
        logging.info(
            log_record(
                "cli_run.done",
                id=event_id,
                mode="cli",
                status=status,
                modules=modules_run,
                changed_paths=changed_paths_count,
                changed_events=changed_events_count,
                duration_ms=(time.monotonic() - started_at) * 1000.0,
            )
        )

    logging.info(
        log_record(
            "oneshot.done",
            flow="cli",
            modules=modules_run,
        )
    )
    return 0


def _run_event_flow(
    *,
    manager: ModuleManager,
    modules: list,
    args: ParsedArgs,
) -> int:
    plan = _build_event_plan(args)
    first_event_type = plan[0][1].event_type if plan else "none"
    log_startup_message(
        run_mode="oneshot",
        modules=modules,
        args=args,
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

    logging.info(
        log_record(
            "oneshot.done",
            flow="event",
            events=len(plan),
            modules=len(modules),
        )
    )
    return 0


def run_oneshot(
    startup_args: ParsedArgs,
) -> int:
    configure_logging(startup_args)
    modules = select_demon_lucy_modules(
        include_names=startup_args.require("sys-modules").value,
        exclude_names=startup_args.require("sys-modules-exclude").value,
    )
    use_cli_flow = _uses_cli_flow(startup_args)
    manager = ModuleManager(
        modules=modules,
        startup_args=startup_args,
        run_mode="cli" if use_cli_flow else "oneshot",
    )

    if use_cli_flow:
        return _run_cli_flow(
            manager=manager,
            modules=modules,
            args=startup_args,
        )
    return _run_event_flow(
        manager=manager,
        modules=modules,
        args=startup_args,
    )


def main() -> int:
    try:
        initial_args = parse_args(
            template=ONESHOT_STARTUP_TEMPLATE,
            args=sys.argv[1:],
        )
        config_path_arg = initial_args.find("sys-config-path")
        if config_path_arg is not None:
            run_config_migrations(config_path_arg.value)
        startup_args = load_args(
            template=ONESHOT_STARTUP_TEMPLATE
        )
        return run_oneshot(startup_args=startup_args)
    except (ValueError, KeyError) as exc:
        logging.basicConfig(level=logging.ERROR, force=True)
        logging.error(log_record("runtime.config_error", error=exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
