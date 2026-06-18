from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from collections import deque
from typing import Any, Dict, Mapping

_NOTIFY_LAST: Dict[str, float] = {}
_ERROR_NOTIFY_LAST: Dict[str, float] = {}
_ERROR_NOTIFY_LEVEL: Dict[str, int] = {}
_ERROR_NOTIFY_HISTORY: deque[float] = deque()
_NOTIFY_STATE_LOCK = threading.Lock()
DEFAULT_NOTIFICATION_ICON_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "media", "icon.png")
)


def _prune_error_notify_history(now: float, window_seconds: float) -> None:
    while _ERROR_NOTIFY_HISTORY and (now - _ERROR_NOTIFY_HISTORY[0]) >= window_seconds:
        _ERROR_NOTIFY_HISTORY.popleft()


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


def _notify_desktop(message: str, title: str, icon_path: str) -> bool:
    try:
        from notifypy import Notify

        notifier = Notify()
        notifier.title = title
        notifier.message = message
        notifier.icon = icon_path
        notifier.send()
    except Exception:
        return False
    return True


def safe_notify(
    name: str,
    message: str,
    *,
    config: Mapping[str, Any],
    title: str = "Demon Lucy Note Manager",
    icon_path: str = DEFAULT_NOTIFICATION_ICON_PATH,
    use_rare_mode: bool = True,
) -> None:
    """
    Throttle notifications by `key`.

    - If called again within configured min interval, it does nothing.
    - Otherwise calls notify(message=...).
    - Rare mode supports exponential backoff + burst window limit.
    """
    min_interval_seconds = max(0.0, config["sys_notification_min_interval_seconds"])
    now = time.time()

    with _NOTIFY_STATE_LOCK:
        if use_rare_mode:
            backoff_base_seconds = max(
                min_interval_seconds,
                config["sys_notification_error_backoff_base_seconds"],
            )
            backoff_max_seconds = max(
                backoff_base_seconds,
                config["sys_notification_error_backoff_max_seconds"],
            )
            burst_limit = max(
                0,
                config["sys_notification_error_burst_limit"],
            )
            burst_window_seconds = max(
                0.0,
                config["sys_notification_error_burst_window_seconds"],
            )

            if burst_limit > 0 and burst_window_seconds > 0.0:
                _prune_error_notify_history(now, burst_window_seconds)
                if len(_ERROR_NOTIFY_HISTORY) >= burst_limit:
                    return

            last = _ERROR_NOTIFY_LAST.get(name)
            level = max(0, _ERROR_NOTIFY_LEVEL.get(name, 0))
            required_interval = min(
                backoff_base_seconds * (2.0 ** max(0, level - 1)),
                backoff_max_seconds,
            )
            if last is not None and (now - last) < required_interval:
                return

            _ERROR_NOTIFY_LAST[name] = now
            _ERROR_NOTIFY_LEVEL[name] = level + 1
            if burst_limit > 0 and burst_window_seconds > 0.0:
                _ERROR_NOTIFY_HISTORY.append(now)
        else:
            last = _NOTIFY_LAST.get(name)
            if last is not None and (now - last) < min_interval_seconds:
                return
            _NOTIFY_LAST[name] = now

    notify(message=message, title=title, icon_path=icon_path, config=config)


def notify(
    message: str,
    title: str = "Demon Lucy Note Manager",
    *,
    icon_path: str = DEFAULT_NOTIFICATION_ICON_PATH,
    config: Mapping[str, Any],
) -> None:
    """
    Send a notification via configured provider.
    Fails silently if notify-py (or its backend) is unavailable.
    """
    provider = _resolve_notification_provider(config)

    try:
        if provider == "desktop":
            _notify_desktop(message=message, title=title, icon_path=icon_path)
        elif provider == "termuxapi":
            _notify_termux(message=message, title=title)
    except Exception:
        return None
