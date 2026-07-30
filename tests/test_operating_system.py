from __future__ import annotations

from demon_lucy.lib import operating_system
from demon_lucy.lib.operating_system import OperatingSystem


def test_detect_operating_system_windows(monkeypatch) -> None:
    monkeypatch.setattr(operating_system.os, "name", "nt")

    assert operating_system.detect_operating_system() is OperatingSystem.WINDOWS


def test_detect_operating_system_macos(monkeypatch) -> None:
    monkeypatch.setattr(operating_system.os, "name", "posix")
    monkeypatch.setattr(operating_system.sys, "platform", "darwin")

    assert operating_system.detect_operating_system() is OperatingSystem.MACOS


def test_detect_operating_system_linux(monkeypatch) -> None:
    monkeypatch.setattr(operating_system.os, "name", "posix")
    monkeypatch.setattr(operating_system.sys, "platform", "linux")

    assert operating_system.detect_operating_system() is OperatingSystem.LINUX


def test_detect_operating_system_other(monkeypatch) -> None:
    monkeypatch.setattr(operating_system.os, "name", "posix")
    monkeypatch.setattr(operating_system.sys, "platform", "freebsd14")

    assert operating_system.detect_operating_system() is OperatingSystem.OTHER
