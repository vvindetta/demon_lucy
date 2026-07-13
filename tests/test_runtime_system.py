from __future__ import annotations

from demon_lucy.lib import runtime_system


def test_detect_runtime_system_windows(monkeypatch) -> None:
    monkeypatch.setattr(runtime_system.os, "name", "nt")

    assert runtime_system.detect_runtime_system() == "windows"


def test_detect_runtime_system_macos(monkeypatch) -> None:
    monkeypatch.setattr(runtime_system.os, "name", "posix")
    monkeypatch.setattr(runtime_system.sys, "platform", "darwin")

    assert runtime_system.detect_runtime_system() == "macos"


def test_detect_runtime_system_linux(monkeypatch) -> None:
    monkeypatch.setattr(runtime_system.os, "name", "posix")
    monkeypatch.setattr(runtime_system.sys, "platform", "linux")

    assert runtime_system.detect_runtime_system() == "linux"


def test_detect_runtime_system_other(monkeypatch) -> None:
    monkeypatch.setattr(runtime_system.os, "name", "posix")
    monkeypatch.setattr(runtime_system.sys, "platform", "freebsd14")

    assert runtime_system.detect_runtime_system() == "other"
