from __future__ import annotations

import threading

from watchdog.observers import Observer

from lucy_notes_manager.file_handler import FileHandler
from lucy_notes_manager.lib.args import setup_config_and_cli_args
from lucy_notes_manager.module_manager import ModuleManager
from lucy_notes_manager.runtime import (
    LUCY_STARTUP_TEMPLATE,
    configure_logging,
    select_lucy_modules,
)


def _wait_until_interrupted() -> None:
    threading.Event().wait()


def main() -> int:
    config, unknown_args = setup_config_and_cli_args(template=LUCY_STARTUP_TEMPLATE)

    configure_logging(config)

    notes_dirs = config["sys_watch_paths"]
    if not notes_dirs:
        raise ValueError("No --sys-watch-paths was setuped")
    if "/path/to/note/dir" in notes_dirs:
        raise ValueError(
            "--sys-watch-paths: '/path/to/note/dir' is not a valid path. Please edit your config."
        )

    modules = ModuleManager(
        modules=select_lucy_modules(),
        args=list(unknown_args),
        system_config=config,
    )

    observer = Observer()
    for path in notes_dirs:
        observer.schedule(
            FileHandler(
                modules=modules,
                open_cooldown_seconds=config["sys_opened_event_cooldown_seconds"],
                process_opened_events=not config["sys_disable_opened_events"],
            ),
            path=path,
            recursive=True,
        )

    observer.start()
    try:
        _wait_until_interrupted()
    except KeyboardInterrupt:
        observer.stop()
    finally:
        observer.join()

    return 0


if __name__ == "__main__":
    main()
