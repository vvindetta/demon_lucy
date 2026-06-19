from __future__ import annotations

import os
import threading
from itertools import count
from typing import Mapping

from watchdog.events import FileSystemEvent

_EVENT_COUNTER = count(1)
_EVENT_COUNTER_LOCK = threading.Lock()


def next_event_id() -> str:
    with _EVENT_COUNTER_LOCK:
        return f"evt-{next(_EVENT_COUNTER):06d}"


def _format_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.1f}"
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(item) for item in value) or "-"
    return str(value)


def kv(**items: object) -> str:
    parts: list[str] = []
    for key, value in items.items():
        if value is None:
            continue
        rendered = _format_value(value)
        if rendered == "":
            rendered = "-"
        parts.append(f"{key}={rendered}")
    return " | ".join(parts)


def log_record(action: str, **items: object) -> str:
    rendered_items = kv(**items)
    if not rendered_items:
        return action
    return f"{action} | {rendered_items}"


def event_paths(event: FileSystemEvent, path: str) -> dict[str, str]:
    event_type = str(getattr(event, "event_type", "unknown"))
    src_path = os.fsdecode(getattr(event, "src_path", path))
    dest_path = os.fsdecode(getattr(event, "dest_path", ""))
    if event_type == "moved":
        return {
            "path": os.fsdecode(path),
            "src": src_path,
            "dest": dest_path,
        }
    return {"path": os.fsdecode(path)}


def ignore_summary(ignore_paths: Mapping[str, int] | None) -> tuple[int, int]:
    if not ignore_paths:
        return 0, 0
    changed_paths = 0
    changed_events = 0
    for ignore_count in ignore_paths.values():
        if not ignore_count:
            continue
        changed_paths += 1
        changed_events += int(ignore_count)
    return changed_paths, changed_events
