from __future__ import annotations

import ctypes
import errno
import os
import sys
from types import SimpleNamespace

import pytest

from demon_lucy.lib import file_open


def test_posix_open_adds_no_follow_flag(monkeypatch) -> None:
    calls: list[tuple[str, int, int]] = []

    def fake_open(path_value: str, flags: int, mode: int) -> int:
        calls.append((path_value, flags, mode))
        return 17

    monkeypatch.setattr(file_open.os, "open", fake_open)

    file_descriptor = file_open.open_file_no_follow(
        "note.md",
        os.O_WRONLY | os.O_CREAT,
        runtime_system="linux",
        mode=0o640,
    )

    assert file_descriptor == 17
    assert calls == [
        ("note.md", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o640)
    ]


def test_secure_open_rejects_truncate_before_open(monkeypatch) -> None:
    opened = False

    def fake_open(path_value: str, flags: int, mode: int) -> int:
        nonlocal opened
        opened = True
        return 17

    monkeypatch.setattr(file_open.os, "open", fake_open)

    with pytest.raises(ValueError, match="validating the opened file"):
        file_open.open_file_no_follow(
            "note.md",
            os.O_WRONLY | os.O_TRUNC,
            runtime_system="linux",
        )

    assert opened is False


def _install_fake_windows_api(
    monkeypatch,
    *,
    file_attributes: int,
) -> tuple[list[tuple[object, ...]], list[int], list[tuple[int, int]]]:
    create_calls: list[tuple[object, ...]] = []
    closed_handles: list[int] = []
    converted_handles: list[tuple[int, int]] = []

    def create_file(*args):
        create_calls.append(args)
        return 71

    def get_file_information(
        handle,
        information_class,
        file_info_pointer,
        file_info_size,
    ):
        file_info_pointer._obj.file_attributes = file_attributes
        return True

    def close_handle(handle):
        closed_handles.append(handle)
        return True

    def open_osfhandle(handle: int, flags: int) -> int:
        converted_handles.append((handle, flags))
        return 23

    kernel32 = SimpleNamespace(
        CreateFileW=create_file,
        GetFileInformationByHandleEx=get_file_information,
        CloseHandle=close_handle,
    )
    monkeypatch.setattr(
        ctypes,
        "WinDLL",
        lambda name, use_last_error: kernel32,
        raising=False,
    )
    monkeypatch.setitem(
        sys.modules,
        "msvcrt",
        SimpleNamespace(open_osfhandle=open_osfhandle),
    )
    return create_calls, closed_handles, converted_handles


def test_windows_open_rejects_reparse_point(monkeypatch) -> None:
    create_calls, closed_handles, converted_handles = _install_fake_windows_api(
        monkeypatch,
        file_attributes=0x00000400,
    )

    with pytest.raises(OSError) as error:
        file_open.open_file_no_follow(
            "note.md",
            os.O_RDONLY,
            runtime_system="windows",
        )

    assert error.value.errno == errno.ELOOP
    assert create_calls[0][5] & 0x00200000
    assert closed_handles == [71]
    assert converted_handles == []


def test_windows_open_transfers_valid_handle_to_file_descriptor(monkeypatch) -> None:
    _, closed_handles, converted_handles = _install_fake_windows_api(
        monkeypatch,
        file_attributes=0,
    )

    file_descriptor = file_open.open_file_no_follow(
        "note.md",
        os.O_WRONLY | os.O_CREAT,
        runtime_system="windows",
    )

    assert file_descriptor == 23
    assert converted_handles == [(71, os.O_WRONLY)]
    assert closed_handles == []
