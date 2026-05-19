import os
import shutil
import subprocess
import time
from typing import Any, Dict, List, Mapping

_NOTIFY_LAST: Dict[str, float] = {}

def _resolve_notification_provider(config: Mapping[str, Any]) -> str:
    provider = config["sys_notification_provider"]
    if provider != "auto":
        return provider
    if shutil.which("termux-notification"):
        return "termuxapi"
    return "desktop"


def _notify_termux(message: str, title: str) -> bool:
    executable = shutil.which("termux-notification")
    if not executable:
        return False

    try:
        result = subprocess.run(
            [
                executable,
                "--title",
                title,
                "--content",
                message,
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    return result.returncode == 0


def _notify_desktop(message: str, title: str) -> bool:
    try:
        from notifypy import Notify

        notifier = Notify()
        notifier.title = title
        notifier.message = message
        notifier.send()
    except Exception:
        return False
    return True


def safe_notify(
    name: str,
    message: str,
    *,
    config: Mapping[str, Any],
    title: str = "Lucy Note Manager",
) -> None:
    """
    Throttle notifications by `key`.

    - If called again within configured min interval, it does nothing.
    - Otherwise calls lucy_notes_manager.lib.notify(message=...).
    """
    min_interval_seconds = config["sys_notification_min_interval_seconds"]
    now = time.time()
    last = _NOTIFY_LAST.get(name)
    if last is not None and (now - last) < min_interval_seconds:
        return
    _NOTIFY_LAST[name] = now
    notify(message=message, title=title, config=config)


def notify(
    message: str,
    title: str = "Lucy Note Manager",
    *,
    config: Mapping[str, Any],
) -> None:
    """
    Send a notification via configured provider.
    Fails silently if notify-py (or its backend) is unavailable.
    """
    provider = _resolve_notification_provider(config)

    try:
        if provider == "desktop":
            _notify_desktop(message=message, title=title)
        elif provider == "termuxapi":
            _notify_termux(message=message, title=title)
    except Exception:
        return None


def slow_write_lines_from(
    path: str,
    lines: List[str],
    from_line: int,
    delay: float = 0.2,
) -> Dict[str, int]:
    abs_path = os.path.abspath(path)
    from_idx = max(0, int(from_line) - 1)

    slow_writes = 0

    with open(abs_path, "w", encoding="utf-8") as f:
        # fast part
        if from_idx > 0:
            f.writelines(lines[:from_idx])

        # slow part
        for line in lines[from_idx:]:
            f.write(line)
            f.flush()
            slow_writes += 1
            time.sleep(delay)

    # if slow part was empty, file still changed -> ignore once
    return {abs_path: slow_writes or 1}
