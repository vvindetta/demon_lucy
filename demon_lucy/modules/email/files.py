"""Private file safety and process coordination for email account storage."""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from demon_lucy.lib.file_open import open_file_no_follow
from demon_lucy.lib.operating_system import detect_operating_system
from demon_lucy.lib.path import path_inside_no_symlinks


def safe_path(root: str, relative: str) -> str:
    root_path = Path(os.path.abspath(root))
    if any(
        path.is_symlink() or getattr(path, "is_junction", lambda: False)()
        for path in (root_path, *root_path.parents)
    ):
        raise ValueError("symlink account path is not allowed")
    return path_inside_no_symlinks(root, relative)


def _open(path_value: str, flags: int, *, mode: int = 0o600) -> int:
    path = Path(os.path.abspath(path_value))
    if os.name != "posix":
        if any(
            parent.is_symlink() or getattr(parent, "is_junction", lambda: False)()
            for parent in path.parents
        ):
            raise OSError(errno.ELOOP, "symlink parent is not allowed", path_value)
        return open_file_no_follow(
            path_value, flags, operating_system=detect_operating_system(), mode=mode
        )
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd = os.open(path.anchor, directory_flags)
    try:
        for component in path.parts[1:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        return os.open(path.name, flags | os.O_NOFOLLOW, mode, dir_fd=directory_fd)
    finally:
        os.close(directory_fd)


def read_bytes_no_follow(path_value: str, max_bytes: int) -> bytes:
    if max_bytes < 0:
        raise ValueError("max_bytes must not be negative")
    descriptor: int | None = _open(
        path_value, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(errno.EINVAL, "expected a regular file", path_value)
        if info.st_size > max_bytes:
            raise ValueError("file exceeds size limit")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            result = handle.read(max_bytes + 1)
        if len(result) > max_bytes:
            raise ValueError("file exceeds size limit")
        return result
    finally:
        if descriptor is not None:
            os.close(descriptor)


class FileBusyError(OSError):
    """Another email operation currently owns the account lock."""


@contextmanager
def locked_file(path: str) -> Iterator[None]:
    descriptor = _open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NONBLOCK", 0))
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("lock must be a regular file")
        if os.name == "nt":
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise FileBusyError("file is busy") from exc
        else:
            import fcntl

            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise FileBusyError("file is busy") from exc
        yield
    finally:
        os.close(descriptor)


def write_text_if_missing(path: str, text: str, *, mode: int = 0o600) -> bool:
    try:
        descriptor = _open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode=mode)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    return True
