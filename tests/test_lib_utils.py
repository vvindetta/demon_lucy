from __future__ import annotations

import sys
import types
from pathlib import Path

import lucy_notes_manager.lib as lib_mod

_TERMUX_CONFIG = {
    "sys_notification_provider": "termuxapi",
    "sys_notification_min_interval_seconds": 10.0,
    "sys_notification_error_backoff_base_seconds": 10.0,
    "sys_notification_error_backoff_max_seconds": 1800.0,
    "sys_notification_error_burst_limit": 3,
    "sys_notification_error_burst_window_seconds": 600.0,
}
_AUTO_CONFIG = {
    "sys_notification_provider": "auto",
    "sys_notification_min_interval_seconds": 10.0,
    "sys_notification_error_backoff_base_seconds": 10.0,
    "sys_notification_error_backoff_max_seconds": 1800.0,
    "sys_notification_error_burst_limit": 3,
    "sys_notification_error_burst_window_seconds": 600.0,
}


def _reset_notify_state() -> None:
    lib_mod._NOTIFY_LAST.clear()
    lib_mod._ERROR_NOTIFY_LAST.clear()
    lib_mod._ERROR_NOTIFY_LEVEL.clear()
    lib_mod._ERROR_NOTIFY_HISTORY.clear()


def test_safe_notify_throttles_per_key(monkeypatch):
    calls: list[str] = []
    times = iter([0.0, 1.0, 2.0, 15.0])

    monkeypatch.setattr(
        lib_mod,
        "notify",
        lambda message, title="Lucy Note Manager", config=None: calls.append(message),
    )
    monkeypatch.setattr(lib_mod.time, "time", lambda: next(times))
    _reset_notify_state()

    lib_mod.safe_notify("k1", "first", config=_TERMUX_CONFIG)
    lib_mod.safe_notify("k2", "second", config=_TERMUX_CONFIG)
    lib_mod.safe_notify("k1", "third", config=_TERMUX_CONFIG)
    lib_mod.safe_notify("k1", "fourth", config=_TERMUX_CONFIG)

    assert calls == ["first", "second", "fourth"]


def test_safe_notify_error_uses_exponential_backoff(monkeypatch):
    calls: list[str] = []
    times = iter([0.0, 5.0, 10.0, 21.0, 45.0])

    monkeypatch.setattr(
        lib_mod,
        "notify",
        lambda message, title="Lucy Note Manager", config=None: calls.append(message),
    )
    monkeypatch.setattr(lib_mod.time, "time", lambda: next(times))
    _reset_notify_state()

    lib_mod.safe_notify("err:key", "m1", config=_TERMUX_CONFIG, is_error=True)
    lib_mod.safe_notify("err:key", "m2", config=_TERMUX_CONFIG, is_error=True)
    lib_mod.safe_notify("err:key", "m3", config=_TERMUX_CONFIG, is_error=True)
    lib_mod.safe_notify("err:key", "m4", config=_TERMUX_CONFIG, is_error=True)
    lib_mod.safe_notify("err:key", "m5", config=_TERMUX_CONFIG, is_error=True)

    assert calls == ["m1", "m3", "m5"]


def test_safe_notify_error_burst_limit_applies_globally(monkeypatch):
    calls: list[str] = []
    times = iter([0.0, 1.0, 2.0, 61.0])
    cfg = {
        "sys_notification_provider": "termuxapi",
        "sys_notification_min_interval_seconds": 0.0,
        "sys_notification_error_backoff_base_seconds": 0.0,
        "sys_notification_error_backoff_max_seconds": 0.0,
        "sys_notification_error_burst_limit": 2,
        "sys_notification_error_burst_window_seconds": 60.0,
    }

    monkeypatch.setattr(
        lib_mod,
        "notify",
        lambda message, title="Lucy Note Manager", config=None: calls.append(message),
    )
    monkeypatch.setattr(lib_mod.time, "time", lambda: next(times))
    _reset_notify_state()

    lib_mod.safe_notify("err:a", "a1", config=cfg, is_error=True)
    lib_mod.safe_notify("err:b", "b1", config=cfg, is_error=True)
    lib_mod.safe_notify("err:c", "c1", config=cfg, is_error=True)
    lib_mod.safe_notify("err:d", "d1", config=cfg, is_error=True)

    assert calls == ["a1", "b1", "d1"]


def test_notify_termux_provider_uses_termux_api(monkeypatch):
    calls: list[list[str]] = []

    class _Result:
        returncode = 0

    monkeypatch.setattr(lib_mod.shutil, "which", lambda _name: "/usr/bin/termux-notification")
    monkeypatch.setattr(
        lib_mod.subprocess,
        "run",
        lambda args, **_kwargs: calls.append(list(args)) or _Result(),
    )

    lib_mod.notify("hello termux", title="Lucy", config=_TERMUX_CONFIG)

    assert calls
    assert calls[0][0].endswith("termux-notification")
    assert "--title" in calls[0]
    assert "--content" in calls[0]


def test_notify_termux_provider_silent_when_termux_missing(monkeypatch):
    monkeypatch.setattr(lib_mod.shutil, "which", lambda _name: None)

    # should stay silent and not crash
    lib_mod.notify("missing-termux", title="Lucy", config=_TERMUX_CONFIG)


def test_notify_disable_provider_skips_termux_call(monkeypatch):
    called: dict[str, bool] = {"value": False}

    def _mark(*_args, **_kwargs):
        called["value"] = True
        return True

    monkeypatch.setattr(lib_mod, "_notify_termux", _mark)
    lib_mod.notify(
        "disabled",
        config={"sys_notification_provider": "disable", "sys_notification_min_interval_seconds": 10.0},
    )
    assert called["value"] is False


def test_notify_desktop_provider_uses_desktop_notifier(monkeypatch):
    class DummyNotify:
        def __init__(self):
            self.sent = False
            self.title = ""
            self.message = ""

        def send(self):
            self.sent = True

    dummy = DummyNotify()
    monkeypatch.setitem(
        sys.modules,
        "notifypy",
        types.SimpleNamespace(Notify=lambda: dummy),
    )
    monkeypatch.setattr(
        lib_mod,
        "_notify_termux",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("termux notifier must not be used for desktop provider")
        ),
    )

    lib_mod.notify(
        "desktop notification",
        title="Lucy",
        config={"sys_notification_provider": "desktop", "sys_notification_min_interval_seconds": 10.0},
    )

    assert dummy.sent is True
    assert dummy.title == "Lucy"
    assert dummy.message == "desktop notification"


def test_notify_auto_provider_uses_termux_on_termux(monkeypatch):
    calls: dict[str, int] = {"termux": 0, "desktop": 0}

    monkeypatch.setattr(lib_mod.shutil, "which", lambda _name: "/usr/bin/termux-notification")
    monkeypatch.setattr(
        lib_mod,
        "_notify_termux",
        lambda *_args, **_kwargs: calls.__setitem__("termux", calls["termux"] + 1) or True,
    )
    monkeypatch.setattr(
        lib_mod,
        "_notify_desktop",
        lambda *_args, **_kwargs: calls.__setitem__("desktop", calls["desktop"] + 1) or True,
    )

    lib_mod.notify("auto termux", title="Lucy", config=_AUTO_CONFIG)

    assert calls == {"termux": 1, "desktop": 0}


def test_notify_auto_provider_uses_desktop_when_termux_missing(monkeypatch):
    calls: dict[str, int] = {"termux": 0, "desktop": 0}

    monkeypatch.setattr(lib_mod.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        lib_mod,
        "_notify_termux",
        lambda *_args, **_kwargs: calls.__setitem__("termux", calls["termux"] + 1) or True,
    )
    monkeypatch.setattr(
        lib_mod,
        "_notify_desktop",
        lambda *_args, **_kwargs: calls.__setitem__("desktop", calls["desktop"] + 1) or True,
    )

    lib_mod.notify("auto desktop", title="Lucy", config=_AUTO_CONFIG)

    assert calls == {"termux": 0, "desktop": 1}


def test_slow_write_lines_from_writes_and_counts(tmp_path: Path, monkeypatch):
    path = tmp_path / "note.txt"
    monkeypatch.setattr(lib_mod.time, "sleep", lambda _d: None)

    result = lib_mod.slow_write_lines_from(
        str(path),
        lines=["a\n", "b\n", "c\n"],
        from_line=2,
        delay=0.01,
    )

    assert path.read_text(encoding="utf-8") == "a\nb\nc\n"
    assert result == {str(path.resolve()): 2}
