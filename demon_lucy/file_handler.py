import logging
import os
import time
from collections import OrderedDict
from typing import Dict

from watchdog.events import FileSystemEventHandler

from demon_lucy.lib.logfmt import (
    event_paths,
    ignore_summary,
    log_record,
    next_event_id,
)
from demon_lucy.lib.path import canonical_path, path_has_component
from demon_lucy.module_manager import ModuleManager

logger = logging.getLogger(__name__)


class FileHandler(FileSystemEventHandler):
    def __init__(
        self,
        modules: ModuleManager,
        open_cooldown_seconds: int,
        process_opened_events: bool = True,
    ):
        self._ignore_paths: Dict[str, int] = {}
        self.modules = modules
        self._process_opened_events = process_opened_events

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
        else:
            if self._check_and_delete_ignore(file_path):
                return

        event_id = next_event_id()
        event_type = str(event.event_type)
        path_fields = event_paths(event, file_path)
        started_at = time.perf_counter()
        ignore_paths: Dict[str, int] | None = None
        status = "ok"

        logger.info(
            log_record(
                "event.start",
                id=event_id,
                mode="daemon",
                source="watchdog",
                event=event_type,
                **path_fields,
            )
        )
        try:
            ignore_paths = self.modules.run(
                path=file_path,
                event=event,
                event_id=event_id,
            )
            if ignore_paths:
                self._mark_to_ignore(ignore_paths=ignore_paths, event_id=event_id)
        except Exception:
            status = "error"
            logger.error(
                log_record(
                    "event.error",
                    id=event_id,
                    mode="daemon",
                    event=event_type,
                    **path_fields,
                )
            )
            raise
        finally:
            changed_paths_count, changed_events_count = ignore_summary(ignore_paths)
            logger.info(
                log_record(
                    "event.done",
                    id=event_id,
                    mode="daemon",
                    event=event_type,
                    status=status,
                    changed_paths=changed_paths_count,
                    changed_events=changed_events_count,
                    duration_ms=(time.perf_counter() - started_at) * 1000.0,
                    **path_fields,
                )
            )

    def _mark_to_ignore(self, ignore_paths: Dict[str, int], event_id: str) -> None:
        for path, count in ignore_paths.items():
            new_count = self._bump_ignore(path, count)
            logger.debug(
                log_record(
                    "event.ignore_mark",
                    id=event_id,
                    path=canonical_path(path),
                    count=new_count,
                )
            )

    def _check_and_delete_ignore(self, input_path: str) -> bool:
        cur = self._ignore_paths.get(canonical_path(input_path), 0)
        if cur <= 0:
            return False

        remaining = self._bump_ignore(input_path, -1)
        logger.debug(
            log_record(
                "event.skip",
                reason="ignore_map",
                path=canonical_path(input_path),
                remaining=remaining,
            )
        )
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

    def _touch_open_cache(
        self, cache: OrderedDict[str, float], key: str, now: float
    ) -> None:
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
        if not self._process_opened_events:
            return

        src_path = os.fsdecode(event.src_path)

        if event.is_directory or os.path.basename(src_path).startswith("."):
            return
        if path_has_component(canonical_path(src_path), ".git"):
            return
        if not self._should_process_open(file_path=src_path):
            logger.debug(
                log_record(
                    "event.skip",
                    reason="opened_cooldown",
                    event="opened",
                    path=canonical_path(src_path),
                    cooldown_seconds=self._open_cooldown_seconds,
                )
            )
            return
        self._process_file(event=event)
