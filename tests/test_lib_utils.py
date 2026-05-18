from __future__ import annotations

from pathlib import Path

import lucy_notes_manager.lib as lib_mod

_TERMUX_CONFIG = {
    "sys_notification_provider": "termuxapi",
    "sys_notification_min_interval_seconds": 10.0,
}


def test_safe_notify_throttles_per_key(monkeypatch):
    calls: list[str] = []
    times = iter([0.0, 1.0, 2.0, 15.0])

    monkeypatch.setattr(
        lib_mod,
        "notify",
        lambda message, title="Lucy Note Manager", config=None: calls.append(message),
    )
    monkeypatch.setattr(lib_mod.time, "time", lambda: next(times))
    lib_mod._NOTIFY_LAST.clear()

    lib_mod.safe_notify("k1", "first", config=_TERMUX_CONFIG)
    lib_mod.safe_notify("k2", "second", config=_TERMUX_CONFIG)
    lib_mod.safe_notify("k1", "third", config=_TERMUX_CONFIG)
    lib_mod.safe_notify("k1", "fourth", config=_TERMUX_CONFIG)

    assert calls == ["first", "second", "fourth"]


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
    monkeypatch.setattr(lib_mod, "notifypy", dummy)
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
