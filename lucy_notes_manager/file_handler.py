import logging
import os
import time
from collections import OrderedDict
from typing import Dict

from watchdog.events import FileSystemEventHandler

from lucy_notes_manager.lib.path import canonical_path, path_has_component
from lucy_notes_manager.module_manager import ModuleManager

logger = logging.getLogger(__name__)


class FileHandler(FileSystemEventHandler):
    def __init__(
        self,
        modules: ModuleManager,
        open_cooldown_seconds: int,
    ):
        self._ignore_paths: Dict[str, int] = {}
        self.modules = modules

        # on_opened throttle (per file)
        self._open_cooldown_seconds = float(open_cooldown_seconds)
        self._last_open_ts: OrderedDict[str, float] = OrderedDict()
        self._last_open_dir_ts: OrderedDict[str, float] = OrderedDict()
        self._open_cache_max_entries = 4096

    def _process_file(self, event):
        src_path = os.fsdecode(event.src_path)
        dest_path = os.fsdecode(getattr(event, "dest_path", ""))

        if event.is_directory or os.path.basename(src_path).startswith("."):
            return

        file_path = dest_path if event.event_type == "moved" else src_path
        file_path = canonical_path(file_path)

        if path_has_component(file_path, ".git"):
            return

        if event.event_type == "moved":
            src_ignored = self._check_and_delete_ignore(canonical_path(src_path))
            dest_ignored = bool(dest_path) and self._check_and_delete_ignore(
                canonical_path(dest_path)
            )
            if src_ignored or dest_ignored:
                return
            logger.info(f"EVENT: Moved: {src_path} → {dest_path}")
        else:
            if self._check_and_delete_ignore(file_path):
                return
            logger.info(
                f"EVENT: {str(event.event_type).capitalize()}: {src_path}"
            )

        ignore_paths = self.modules.run(path=file_path, event=event)
        if ignore_paths:
            self._mark_to_ignore(ignore_paths=ignore_paths)

        logging.info("--- END ---\n\n")

    def _mark_to_ignore(self, ignore_paths: Dict[str, int]) -> None:
        for path, count in ignore_paths.items():
            new_count = self._bump_ignore(path, count)
            logger.info("MARKED TO IGNORE: %s (count=%d)", canonical_path(path), new_count)

    def _check_and_delete_ignore(self, input_path: str) -> bool:
        cur = self._ignore_paths.get(canonical_path(input_path), 0)
        if cur <= 0:
            return False

        remaining = self._bump_ignore(input_path, -1)
        logger.info("IGNORED: %s (remaining=%d)\n\n", canonical_path(input_path), remaining)
        return True

    def _bump_ignore(self, path: str, delta: int) -> int:
        abs_path = canonical_path(path)
        cur = self._ignore_paths.get(abs_path, 0)
        new = cur + int(delta)

        if new <= 0:
            if abs_path in self._ignore_paths:
                del self._ignore_paths[abs_path]
            return 0

        self._ignore_paths[abs_path] = new
        return new

    def _touch_open_cache(self, cache: OrderedDict[str, float], key: str, now: float) -> None:
        cache[key] = now
        cache.move_to_end(key)
        if len(cache) > self._open_cache_max_entries:
            cache.popitem(last=False)

    def _should_process_open(self, file_path: str) -> bool:
        if self._open_cooldown_seconds <= 0:
            return True

        abs_path = canonical_path(file_path)
        dir_path = os.path.dirname(abs_path)
        now = time.monotonic()

        last_dir = self._last_open_dir_ts.get(dir_path)
        if last_dir is not None and (now - last_dir) < self._open_cooldown_seconds:
            return False

        last = self._last_open_ts.get(abs_path)
        if last is not None and (now - last) < self._open_cooldown_seconds:
            return False

        self._touch_open_cache(self._last_open_ts, abs_path, now)
        self._touch_open_cache(self._last_open_dir_ts, dir_path, now)
        return True

    def on_modified(self, event):
        self._process_file(event=event)

    def on_created(self, event):
        self._process_file(event=event)

    def on_moved(self, event):
        self._process_file(event=event)

    def on_deleted(self, event):
        self._process_file(event=event)

    def on_opened(self, event):
        src_path = os.fsdecode(event.src_path)

        if event.is_directory or os.path.basename(src_path).startswith("."):
            return
        if path_has_component(canonical_path(src_path), ".git"):
            return
        if not self._should_process_open(file_path=src_path):
            return
        self._process_file(event=event)
