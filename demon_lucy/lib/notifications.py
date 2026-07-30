from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from collections import deque
from enum import StrEnum

from demon_lucy.lib.args.models import ParsedArgs
from demon_lucy.lib.logfmt import log_record

logger = logging.getLogger(__name__)

_NOTIFY_LAST: dict[str, float] = {}
_ERROR_NOTIFY_LAST: dict[str, float] = {}
_ERROR_NOTIFY_LEVEL: dict[str, int] = {}
_ERROR_NOTIFY_HISTORY: deque[float] = deque()
_NOTIFY_STATE_LOCK = threading.Lock()
DEFAULT_NOTIFICATION_ICON_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "media", "icon.jpg")
)


class NotificationProvider(StrEnum):
    AUTO = "auto"
    TERMUX_API = "termuxapi"
    DESKTOP = "desktop"
    DISABLE = "disable"


def _prune_error_notify_history(now: float, window_seconds: float) -> None:
    while _ERROR_NOTIFY_HISTORY and (now - _ERROR_NOTIFY_HISTORY[0]) >= window_seconds:
        _ERROR_NOTIFY_HISTORY.popleft()


def _resolve_notification_provider(
    args: ParsedArgs,
) -> NotificationProvider:
    provider: NotificationProvider = args.require("sys-notification-provider").value
    if provider is not NotificationProvider.AUTO:
        return provider
    if shutil.which("termux-notification"):
        return NotificationProvider.TERMUX_API
    return NotificationProvider.DESKTOP


def _notify_termux(message: str, title: str) -> bool:
    executable = shutil.which("termux-notification")
    if not executable:
        return False

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

    return result.returncode == 0


def _notify_desktop(message: str, title: str, icon_path: str) -> bool:
    from notifypy import Notify

    notifier = Notify()
    notifier.title = title
    notifier.message = message
    if os.path.isfile(icon_path):
        notifier.icon = icon_path
    return bool(notifier.send())


def safe_notify(
    name: str,
    message: str,
    *,
    args: ParsedArgs,
    title: str = "Demon Lucy Note Manager",
    icon_path: str = DEFAULT_NOTIFICATION_ICON_PATH,
    use_rare_mode: bool = True,
) -> bool:
    """
    Throttle notifications by `key`.

    - If called again within configured min interval, it does nothing.
    - Otherwise calls notify(message=...) and returns whether backend accepted it.
    - Rare mode supports exponential backoff + burst window limit.
    """
    min_interval_seconds = max(
        0.0,
        args.require("sys-notification-min-interval-seconds").value,
    )
    now = time.time()

    with _NOTIFY_STATE_LOCK:
        if use_rare_mode:
            backoff_base_seconds = max(
                min_interval_seconds,
                args.require("sys-notification-error-backoff-base-seconds").value,
            )
            backoff_max_seconds = max(
                backoff_base_seconds,
                args.require("sys-notification-error-backoff-max-seconds").value,
            )
            burst_limit = max(
                0,
                args.require("sys-notification-error-burst-limit").value,
            )
            burst_window_seconds = max(
                0.0,
                args.require("sys-notification-error-burst-window-seconds").value,
            )

            if burst_limit > 0 and burst_window_seconds > 0.0:
                _prune_error_notify_history(now, burst_window_seconds)
                if len(_ERROR_NOTIFY_HISTORY) >= burst_limit:
                    return False

            last = _ERROR_NOTIFY_LAST.get(name)
            level = max(0, _ERROR_NOTIFY_LEVEL.get(name, 0))
            required_interval = min(
                backoff_base_seconds * (2.0 ** max(0, level - 1)),
                backoff_max_seconds,
            )
            if last is not None and (now - last) < required_interval:
                return False

            _ERROR_NOTIFY_LAST[name] = now
            _ERROR_NOTIFY_LEVEL[name] = level + 1
            if burst_limit > 0 and burst_window_seconds > 0.0:
                _ERROR_NOTIFY_HISTORY.append(now)
        else:
            last = _NOTIFY_LAST.get(name)
            if last is not None and (now - last) < min_interval_seconds:
                return False
            _NOTIFY_LAST[name] = now

    return notify(message=message, title=title, icon_path=icon_path, args=args)


def notify(
    message: str,
    title: str = "Demon Lucy Note Manager",
    *,
    icon_path: str = DEFAULT_NOTIFICATION_ICON_PATH,
    args: ParsedArgs,
) -> bool:
    """
    Send a notification via configured provider.
    Returns whether the backend accepted it and logs failed delivery attempts.
    """
    provider = _resolve_notification_provider(args)

    try:
        if provider is NotificationProvider.DESKTOP:
            delivered = _notify_desktop(
                message=message, title=title, icon_path=icon_path
            )
        elif provider is NotificationProvider.TERMUX_API:
            delivered = _notify_termux(message=message, title=title)
        elif provider is NotificationProvider.DISABLE:
            return False
        else:
            logger.error(
                log_record(
                    "notification.failed",
                    provider=provider,
                    reason="unsupported_provider",
                )
            )
            return False
    except Exception as exc:
        logger.error(
            log_record(
                "notification.failed",
                provider=provider,
                reason="provider_exception",
                error=exc,
            )
        )
        return False

    if not delivered:
        logger.error(
            log_record(
                "notification.failed",
                provider=provider,
                reason="backend_returned_false",
            )
        )
        return False

    return True
