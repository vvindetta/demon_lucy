from __future__ import annotations

import logging
import sys
import threading

from watchdog.observers import Observer

from demon_lucy.file_handler import FileHandler
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.lib.args.sources import load_args
from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.path import abs_expand_path
from demon_lucy.module_manager import ModuleManager
from demon_lucy.runtime import (
    DEMON_LUCY_STARTUP_TEMPLATE,
    configure_logging,
    log_startup_message,
    run_config_migrations,
    select_demon_lucy_modules,
)

logger = logging.getLogger(__name__)


def main() -> int:
    initial_args = parse_args(
        template=DEMON_LUCY_STARTUP_TEMPLATE,
        args=sys.argv[1:],
    )
    config_path_arg = initial_args.find("sys-config-path")
    if config_path_arg is not None:
        run_config_migrations(config_path_arg.value)
    startup_args = load_args(
        template=DEMON_LUCY_STARTUP_TEMPLATE
    )
    notes_dirs = startup_args.require("sys-watch-paths").value
    if not notes_dirs:
        raise ValueError("No --sys-watch-paths was setuped")
    if "/path/to/note/dir" in notes_dirs:
        raise ValueError(
            "--sys-watch-paths: '/path/to/note/dir' is not a valid path. Please edit your config."
        )

    configure_logging(startup_args)

    modules = ModuleManager(
        modules=select_demon_lucy_modules(
            include_names=startup_args.require("sys-modules").value,
            exclude_names=startup_args.require("sys-modules-exclude").value,
        ),
        startup_args=startup_args,
        run_mode="daemon",
    )
    log_startup_message(
        run_mode="daemon",
        modules=modules.modules,
        args=startup_args,
    )

    disable_opened_events = startup_args.require(
        "sys-disable-opened-events"
    ).value
    if modules.operating_system != "linux" and not disable_opened_events:
        system_name = {
            "macos": "macOS",
            "windows": "Windows",
        }.get(modules.operating_system, "this system")
        logger.info(
            log_record(
                "watcher.opened_events_unavailable",
                system=system_name,
                message=(
                    f"Lucy cannot detect when a file is only opened on {system_name}. "
                    "Created, modified, moved, and deleted files are still processed."
                ),
            )
        )

    observer = Observer()
    for path in notes_dirs:
        observer.schedule(
            FileHandler(
                modules=modules,
                open_cooldown_seconds=startup_args.require(
                    "sys-opened-event-cooldown-seconds"
                ).value,
                process_opened_events=not disable_opened_events,
            ),
            path=abs_expand_path(path),
            recursive=True,
        )

    observer.start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        observer.stop()
    finally:
        observer.join()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
