from __future__ import annotations

import logging
import sys
import threading

from watchdog.observers import Observer

from demon_lucy.file_handler import FileHandler
from demon_lucy.lib.args.parser import parse_args, setup_config_and_cli_args
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
    startup_args, _unknown_startup_args = parse_args(
        template=DEMON_LUCY_STARTUP_TEMPLATE,
        args=sys.argv[1:],
    )
    config_path = startup_args.get("sys_config_path")
    if isinstance(config_path, str) and config_path.strip():
        run_config_migrations(config_path)
    config, unknown_args = setup_config_and_cli_args(
        template=DEMON_LUCY_STARTUP_TEMPLATE
    )
    notes_dirs = config.get("sys_watch_paths")
    if not notes_dirs:
        raise ValueError("No --sys-watch-paths was setuped")
    if "/path/to/note/dir" in notes_dirs:
        raise ValueError(
            "--sys-watch-paths: '/path/to/note/dir' is not a valid path. Please edit your config."
        )

    configure_logging(config)

    modules = ModuleManager(
        modules=select_demon_lucy_modules(
            include_names=config["sys_modules"],
            exclude_names=config["sys_modules_exclude"],
        ),
        args=list(unknown_args),
        system_config=config,
        run_mode="daemon",
    )
    log_startup_message(
        run_mode="daemon",
        modules=modules.modules,
        config=config,
        unknown_args=list(unknown_args),
    )

    if modules.runtime_system != "linux" and not config["sys_disable_opened_events"]:
        system_name = {
            "macos": "macOS",
            "windows": "Windows",
        }.get(modules.runtime_system, "this system")
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
                open_cooldown_seconds=config["sys_opened_event_cooldown_seconds"],
                process_opened_events=not config["sys_disable_opened_events"],
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
