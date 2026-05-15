from __future__ import annotations

import time

from watchdog.observers import Observer

from lucy_notes_manager.file_handler import FileHandler
from lucy_notes_manager.lib.args import setup_config_and_cli_args
from lucy_notes_manager.module_manager import ModuleManager
from lucy_notes_manager.runtime import (
    LUCY_STARTUP_TEMPLATE,
    configure_logging,
    select_lucy_modules,
)


def main() -> int:
    config, unknown_args = setup_config_and_cli_args(template=LUCY_STARTUP_TEMPLATE)

    configure_logging(config)

    notes_dirs = config["sys_notes_dirs"]
    if not notes_dirs:
        raise ValueError("No --sys-notes-dirs was setuped")
    if "/path/to/note/dir" in notes_dirs:
        raise ValueError(
            "--sys-notes-dirs: '/path/to/note/dir' is not a valid path. Please edit your config."
        )

    modules = ModuleManager(
        modules=select_lucy_modules(
            include_experimental=config["sys_enable_experimental_modules"],
        ),
        args=list(unknown_args),
    )

    observer = Observer()
    for path in notes_dirs:
        observer.schedule(
            FileHandler(
                modules=modules,
                open_cooldown_seconds=config["sys_on_open_cooldown"],
            ),
            path=path,
            recursive=True,
        )

    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    finally:
        observer.join()

    return 0


if __name__ == "__main__":
    main()
