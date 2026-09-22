from __future__ import annotations

import os
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from demon_lucy.modules.email import files
from demon_lucy.modules.email.files import FileBusyError, locked_file
from demon_lucy.modules.email.files import read_bytes_no_follow
from demon_lucy.modules.email.files import safe_path


def test_bounded_read_preserves_exact_bytes_and_accepts_empty_files(
    tmp_path: Path,
) -> None:
    path = tmp_path / "data.bin"
    content = bytes(range(256)) + b"\r\n\x00\xff"
    path.write_bytes(content)
    assert read_bytes_no_follow(str(path), len(content)) == content
    with pytest.raises(ValueError, match="size limit"):
        read_bytes_no_follow(str(path), len(content) - 1)
    with pytest.raises(ValueError, match="negative"):
        read_bytes_no_follow(str(path), -1)
    path.write_bytes(b"")
    assert read_bytes_no_follow(str(path), 0) == b""


def test_bounded_read_rejects_file_growing_after_stat(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "growing.bin"
    path.write_bytes(b"before")
    original_stat = os.fstat

    def grow_after_stat(descriptor: int):
        result = original_stat(descriptor)
        path.write_bytes(b"more bytes than the configured bound")
        return result

    monkeypatch.setattr(files.os, "fstat", grow_after_stat)
    with pytest.raises(ValueError, match="size limit"):
        read_bytes_no_follow(str(path), 10)


@pytest.mark.skipif(os.name != "posix", reason="symlink tests require POSIX")
@pytest.mark.parametrize("operation", ["read", "lock", "path"])
@pytest.mark.parametrize("location", ["file", "parent", "root_ancestor"])
def test_helpers_reject_symlinks_at_every_depth(
    tmp_path: Path, operation: str, location: str
) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    (actual / "data").write_bytes(b"safe")
    if location == "file":
        (actual / "link").symlink_to(actual / "data")
        root, relative = actual, "link"
    elif location == "parent":
        (tmp_path / "link").symlink_to(actual, target_is_directory=True)
        root, relative = tmp_path, "link/data"
    else:
        (actual / "nested").mkdir()
        (actual / "nested" / "data").write_bytes(b"safe")
        (tmp_path / "link").symlink_to(actual, target_is_directory=True)
        root, relative = tmp_path / "link" / "nested", "data"
    with pytest.raises((OSError, ValueError)):
        if operation == "read":
            read_bytes_no_follow(str(root / relative), 10)
        elif operation == "lock":
            with locked_file(str(root / relative)):
                pytest.fail("a symlink was accepted")
        else:
            safe_path(str(root), relative)
    assert (actual / "data").read_bytes() == b"safe"


@pytest.mark.skipif(os.name != "posix", reason="FIFO and POSIX descriptor tests")
@pytest.mark.parametrize("kind", ["directory", "fifo"])
@pytest.mark.parametrize("operation", ["read", "lock"])
def test_nonregular_paths_fail_without_blocking(
    tmp_path: Path, kind: str, operation: str
) -> None:
    path = tmp_path / "not-regular"
    if kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)
    script = """
import sys
from demon_lucy.modules.email.files import read_bytes_no_follow
from demon_lucy.modules.email.files import locked_file
try:
    if sys.argv[2] == "read":
        read_bytes_no_follow(sys.argv[1], 1024)
    else:
        with locked_file(sys.argv[1]):
            pass
except (OSError, ValueError):
    raise SystemExit(23)
raise SystemExit(24)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(path), operation],
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 23, result.stderr.decode()


def test_lock_is_private_contended_and_released_after_exception(tmp_path: Path) -> None:
    path = tmp_path / ".lock"
    with pytest.raises(RuntimeError, match="intentional"):
        with locked_file(str(path)):
            if os.name == "posix":
                assert stat.S_IMODE(path.stat().st_mode) == 0o600
            with pytest.raises(FileBusyError):
                with locked_file(str(path)):
                    pytest.fail("two contexts own the same lock")
            raise RuntimeError("intentional")
    with locked_file(str(path)):
        pass


def test_file_lock_contends_between_processes(tmp_path: Path) -> None:
    path = tmp_path / ".lock"
    script = """
import sys
from demon_lucy.modules.email.files import FileBusyError, locked_file
try:
    with locked_file(sys.argv[1]):
        pass
except FileBusyError:
    raise SystemExit(23)
raise SystemExit(24)
"""
    with locked_file(str(path)):
        result = subprocess.run(
            [sys.executable, "-c", script, str(path)], capture_output=True, timeout=5
        )
    assert result.returncode == 23, result.stderr.decode()
    with locked_file(str(path)):
        pass


def test_blocking_lock_waits_until_owner_releases(tmp_path: Path) -> None:
    path = tmp_path / ".lock"
    started = threading.Event()
    acquired = threading.Event()
    errors = []

    def wait_for_lock():
        started.set()
        try:
            with locked_file(str(path), blocking=True):
                acquired.set()
        except BaseException as error:
            errors.append(error)

    with locked_file(str(path)):
        worker = threading.Thread(target=wait_for_lock, daemon=True)
        worker.start()
        assert started.wait(5)
        assert not acquired.wait(0.05)
    worker.join(5)
    assert not worker.is_alive()
    assert errors == []
    assert acquired.is_set()


def test_process_exit_releases_lock_without_context_cleanup(tmp_path: Path) -> None:
    path = tmp_path / ".lock"
    script = """
import os
import sys
from demon_lucy.modules.email.files import locked_file
with locked_file(sys.argv[1]):
    print("locked", flush=True)
    os._exit(17)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(path)], capture_output=True, timeout=5
    )
    assert result.returncode == 17, result.stderr.decode()
    assert result.stdout == b"locked\n"
    with locked_file(str(path)):
        pass


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory descriptors")
def test_parent_swap_cannot_redirect_secure_read(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    outside = tmp_path / "outside"
    source.mkdir()
    outside.mkdir()
    (source / "file").write_bytes(b"source")
    (outside / "file").write_bytes(b"outside")
    original_open = os.open
    swapped = False

    def swap_parent(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if path == "file" and dir_fd is not None and not swapped:
            swapped = True
            source.rename(tmp_path / "old-source")
            source.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(files.os, "open", swap_parent)
    assert read_bytes_no_follow(str(source / "file"), 100) == b"source"
    assert swapped


@pytest.mark.parametrize("relative", ["../escape", "/absolute", ""])
def test_contained_paths_reject_traversal_and_absolute_paths(
    tmp_path: Path, relative: str
) -> None:
    with pytest.raises(ValueError):
        safe_path(str(tmp_path), relative)
