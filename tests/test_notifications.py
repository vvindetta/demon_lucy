from __future__ import annotations

import logging
import sys
import types

import demon_lucy.lib.notifications as notifications_mod
from demon_lucy.lib.args.models import KnownArg, ParsedArgs


def _args(values: dict[str, object]) -> ParsedArgs:
    return ParsedArgs(
        known=tuple(
            KnownArg(
                name=key.replace("_", "-"),
                value=value,
            )
            for key, value in values.items()
        )
    )


_TERMUX_ARGS = _args(
    {
        "sys_notification_provider": notifications_mod.NotificationProvider.TERMUX_API,
        "sys_notification_min_interval_seconds": 10.0,
        "sys_notification_error_backoff_base_seconds": 10.0,
        "sys_notification_error_backoff_max_seconds": 1800.0,
        "sys_notification_error_burst_limit": 3,
        "sys_notification_error_burst_window_seconds": 600.0,
    }
)
_AUTO_ARGS = _args(
    {
        "sys_notification_provider": notifications_mod.NotificationProvider.AUTO,
        "sys_notification_min_interval_seconds": 10.0,
        "sys_notification_error_backoff_base_seconds": 10.0,
        "sys_notification_error_backoff_max_seconds": 1800.0,
        "sys_notification_error_burst_limit": 3,
        "sys_notification_error_burst_window_seconds": 600.0,
    }
)


def _reset_notify_state() -> None:
    notifications_mod._NOTIFY_LAST.clear()
    notifications_mod._ERROR_NOTIFY_LAST.clear()
    notifications_mod._ERROR_NOTIFY_LEVEL.clear()
    notifications_mod._ERROR_NOTIFY_HISTORY.clear()


def test_safe_notify_throttles_per_key(monkeypatch):
    calls: list[str] = []
    times = iter([0.0, 1.0, 2.0, 15.0])

    monkeypatch.setattr(
        notifications_mod,
        "notify",
        lambda message, title="Demon Lucy Note Manager", icon_path="", args=None: calls.append(
            message
        ),
    )
    monkeypatch.setattr(notifications_mod.time, "time", lambda: next(times))
    _reset_notify_state()

    notifications_mod.safe_notify("k1", "first", args=_TERMUX_ARGS)
    notifications_mod.safe_notify("k2", "second", args=_TERMUX_ARGS)
    notifications_mod.safe_notify("k1", "third", args=_TERMUX_ARGS)
    notifications_mod.safe_notify("k1", "fourth", args=_TERMUX_ARGS)

    assert calls == ["first", "second", "fourth"]


def test_safe_notify_error_uses_exponential_backoff(monkeypatch):
    calls: list[str] = []
    times = iter([0.0, 5.0, 10.0, 21.0, 45.0])

    monkeypatch.setattr(
        notifications_mod,
        "notify",
        lambda message, title="Demon Lucy Note Manager", icon_path="", args=None: calls.append(
            message
        ),
    )
    monkeypatch.setattr(notifications_mod.time, "time", lambda: next(times))
    _reset_notify_state()

    notifications_mod.safe_notify(
        "err:key", "m1", args=_TERMUX_ARGS, use_rare_mode=True
    )
    notifications_mod.safe_notify(
        "err:key", "m2", args=_TERMUX_ARGS, use_rare_mode=True
    )
    notifications_mod.safe_notify(
        "err:key", "m3", args=_TERMUX_ARGS, use_rare_mode=True
    )
    notifications_mod.safe_notify(
        "err:key", "m4", args=_TERMUX_ARGS, use_rare_mode=True
    )
    notifications_mod.safe_notify(
        "err:key", "m5", args=_TERMUX_ARGS, use_rare_mode=True
    )

    assert calls == ["m1", "m3", "m5"]


def test_safe_notify_error_burst_limit_applies_globally(monkeypatch):
    calls: list[str] = []
    times = iter([0.0, 1.0, 2.0, 61.0])
    args = _args(
        {
            "sys_notification_provider": notifications_mod.NotificationProvider.TERMUX_API,
            "sys_notification_min_interval_seconds": 0.0,
            "sys_notification_error_backoff_base_seconds": 0.0,
            "sys_notification_error_backoff_max_seconds": 0.0,
            "sys_notification_error_burst_limit": 2,
            "sys_notification_error_burst_window_seconds": 60.0,
        }
    )

    monkeypatch.setattr(
        notifications_mod,
        "notify",
        lambda message, title="Demon Lucy Note Manager", icon_path="", args=None: calls.append(
            message
        ),
    )
    monkeypatch.setattr(notifications_mod.time, "time", lambda: next(times))
    _reset_notify_state()

    notifications_mod.safe_notify("err:a", "a1", args=args, use_rare_mode=True)
    notifications_mod.safe_notify("err:b", "b1", args=args, use_rare_mode=True)
    notifications_mod.safe_notify("err:c", "c1", args=args, use_rare_mode=True)
    notifications_mod.safe_notify("err:d", "d1", args=args, use_rare_mode=True)

    assert calls == ["a1", "b1", "d1"]


def test_notify_termux_provider_uses_termux_api(monkeypatch):
    calls: list[list[str]] = []

    class _Result:
        returncode = 0

    monkeypatch.setattr(
        notifications_mod.shutil,
        "which",
        lambda _name: "/usr/bin/termux-notification",
    )
    monkeypatch.setattr(
        notifications_mod.subprocess,
        "run",
        lambda args, **_kwargs: calls.append(list(args)) or _Result(),
    )

    result = notifications_mod.notify(
        "hello termux", title="Demon Lucy", args=_TERMUX_ARGS
    )

    assert result is True
    assert calls
    assert calls[0][0].endswith("termux-notification")
    assert "--title" in calls[0]
    assert "--content" in calls[0]


def test_notify_termux_provider_logs_when_termux_missing(monkeypatch, caplog):
    monkeypatch.setattr(notifications_mod.shutil, "which", lambda _name: None)

    with caplog.at_level(logging.ERROR, logger="demon_lucy.lib.notifications"):
        result = notifications_mod.notify(
            "missing-termux", title="Demon Lucy", args=_TERMUX_ARGS
        )

    assert result is False
    assert "notification.failed" in caplog.text
    assert "provider=termuxapi" in caplog.text
    assert "reason=backend_returned_false" in caplog.text


def test_notify_disable_provider_skips_termux_call(monkeypatch, caplog):
    called: dict[str, bool] = {"value": False}

    def _mark(*_args, **_kwargs):
        called["value"] = True
        return True

    monkeypatch.setattr(notifications_mod, "_notify_termux", _mark)
    with caplog.at_level(logging.ERROR, logger="demon_lucy.lib.notifications"):
        result = notifications_mod.notify(
            "disabled",
            args=_args(
                {
                    "sys_notification_provider": notifications_mod.NotificationProvider.DISABLE,
                }
            ),
        )
    assert result is False
    assert called["value"] is False
    assert caplog.text == ""


def test_notify_desktop_provider_uses_desktop_notifier(monkeypatch):
    class DummyNotify:
        def __init__(self):
            self.sent = False
            self.title = ""
            self.message = ""

        def send(self):
            self.sent = True
            return True

    dummy = DummyNotify()
    monkeypatch.setitem(
        sys.modules,
        "notifypy",
        types.SimpleNamespace(Notify=lambda: dummy),
    )
    monkeypatch.setattr(
        notifications_mod,
        "_notify_termux",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("termux notifier must not be used for desktop provider")
        ),
    )

    result = notifications_mod.notify(
        "desktop notification",
        title="Demon Lucy",
        args=_args(
            {
                "sys_notification_provider": notifications_mod.NotificationProvider.DESKTOP,
            }
        ),
    )

    assert result is True
    assert dummy.sent is True
    assert dummy.title == "Demon Lucy"
    assert dummy.message == "desktop notification"


def test_notify_desktop_provider_ignores_missing_optional_icon(monkeypatch):
    class DummyNotify:
        def __init__(self):
            self.title = ""
            self.message = ""
            self.icon = "unset"

        def send(self):
            return True

    dummy = DummyNotify()
    monkeypatch.setitem(
        sys.modules,
        "notifypy",
        types.SimpleNamespace(Notify=lambda: dummy),
    )

    delivered = notifications_mod._notify_desktop(
        message="desktop notification",
        title="Demon Lucy",
        icon_path="/missing/demon-lucy-icon.png",
    )

    assert delivered is True
    assert dummy.icon == "unset"


def test_notify_desktop_provider_logs_backend_false(monkeypatch, caplog):
    class DummyNotify:
        def __init__(self):
            self.title = ""
            self.message = ""

        def send(self):
            return False

    monkeypatch.setitem(
        sys.modules,
        "notifypy",
        types.SimpleNamespace(Notify=DummyNotify),
    )

    with caplog.at_level(logging.ERROR, logger="demon_lucy.lib.notifications"):
        result = notifications_mod.notify(
            "desktop notification",
            title="Demon Lucy",
            args=_args(
                {
                    "sys_notification_provider": notifications_mod.NotificationProvider.DESKTOP,
                }
            ),
        )

    assert result is False
    assert "notification.failed" in caplog.text
    assert "provider=desktop" in caplog.text
    assert "reason=backend_returned_false" in caplog.text


def test_notify_desktop_provider_logs_exception(monkeypatch, caplog):
    def _raise(*_args, **_kwargs):
        raise RuntimeError("dbus unavailable")

    monkeypatch.setattr(notifications_mod, "_notify_desktop", _raise)

    with caplog.at_level(logging.ERROR, logger="demon_lucy.lib.notifications"):
        result = notifications_mod.notify(
            "desktop notification",
            title="Demon Lucy",
            args=_args(
                {
                    "sys_notification_provider": notifications_mod.NotificationProvider.DESKTOP,
                }
            ),
        )

    assert result is False
    assert "notification.failed" in caplog.text
    assert "provider=desktop" in caplog.text
    assert "reason=provider_exception" in caplog.text
    assert "error=dbus unavailable" in caplog.text


def test_notify_auto_provider_uses_termux_on_termux(monkeypatch):
    calls: dict[str, int] = {"termux": 0, "desktop": 0}

    monkeypatch.setattr(
        notifications_mod.shutil,
        "which",
        lambda _name: "/usr/bin/termux-notification",
    )
    monkeypatch.setattr(
        notifications_mod,
        "_notify_termux",
        lambda *_args, **_kwargs: calls.__setitem__("termux", calls["termux"] + 1)
        or True,
    )
    monkeypatch.setattr(
        notifications_mod,
        "_notify_desktop",
        lambda *_args, **_kwargs: calls.__setitem__("desktop", calls["desktop"] + 1)
        or True,
    )

    result = notifications_mod.notify(
        "auto termux", title="Demon Lucy", args=_AUTO_ARGS
    )

    assert result is True
    assert calls == {"termux": 1, "desktop": 0}


def test_notify_auto_provider_uses_desktop_when_termux_missing(monkeypatch):
    calls: dict[str, int] = {"termux": 0, "desktop": 0}

    monkeypatch.setattr(notifications_mod.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        notifications_mod,
        "_notify_termux",
        lambda *_args, **_kwargs: calls.__setitem__("termux", calls["termux"] + 1)
        or True,
    )
    monkeypatch.setattr(
        notifications_mod,
        "_notify_desktop",
        lambda *_args, **_kwargs: calls.__setitem__("desktop", calls["desktop"] + 1)
        or True,
    )

    result = notifications_mod.notify(
        "auto desktop", title="Demon Lucy", args=_AUTO_ARGS
    )

    assert result is True
    assert calls == {"termux": 0, "desktop": 1}
