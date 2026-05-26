from __future__ import annotations

import threading

from watchdog.observers import Observer

from lucy_notes_manager.file_handler import FileHandler
from lucy_notes_manager.lib.args import setup_config_and_cli_args
from lucy_notes_manager.lib.path import abs_expand_path
from lucy_notes_manager.module_manager import ModuleManager
from lucy_notes_manager.runtime import (
    LUCY_STARTUP_TEMPLATE,
    configure_logging,
    log_startup_message,
    select_lucy_modules,
)


def main() -> int:
    config, unknown_args = setup_config_and_cli_args(template=LUCY_STARTUP_TEMPLATE)
    notes_dirs = config.get("sys_watch_paths")
    if not notes_dirs:
        raise ValueError("No --sys-watch-paths was setuped")
    if "/path/to/note/dir" in notes_dirs:
        raise ValueError(
            "--sys-watch-paths: '/path/to/note/dir' is not a valid path. Please edit your config."
        )

    configure_logging(config)

    modules = ModuleManager(
        modules=select_lucy_modules(
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
    main()
