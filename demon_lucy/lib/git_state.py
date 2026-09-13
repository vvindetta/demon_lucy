from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.operating_system import OperatingSystem
from demon_lucy.lib.path import git_common_dir_for_repo_root, git_dir_for_repo_root

SYNC_SUCCESS_MARKER_FILE_NAME = "demon_lucy-last-sync-success.timestamp"
REPO_PROCESS_LOCK_FILE_NAME = "demon_lucy-sync.lock"


class GitRepoBusyError(Exception):
    """A repository operation can be retried after its current owner finishes."""


@contextmanager
def locked_git_repo(
    repo_root: str,
    *,
    wait_timeout_seconds: float,
    retry_sleep_seconds: float,
    stale_seconds: float,
    operating_system: OperatingSystem,
    event_id: str = "",
) -> Iterator[None]:
    """Acquire Lucy's shared process lock; never run unlocked on an IO error."""
    lock_path = repo_process_lock_path(repo_root)
    if lock_path is None:
        raise ValueError(f"not a Git repository: {repo_root}")
    deadline = time.monotonic() + max(0, wait_timeout_seconds)
    while not try_create_repo_process_lock(lock_path):
        if remove_stale_repo_process_lock(
            lock_path,
            wait_timeout_seconds=wait_timeout_seconds,
            stale_seconds=stale_seconds,
            operating_system=operating_system,
        ):
            logging.getLogger(__name__).warning(
                log_record(
                    "git.repo_lock_removed",
                    id=event_id,
                    reason="stale",
                    repo=repo_root,
                    lock=lock_path,
                )
            )
            continue
        if time.monotonic() >= deadline:
            raise GitRepoBusyError(f"repository is busy: {repo_root}")
        time.sleep(max(0.01, retry_sleep_seconds))
    try:
        yield
    finally:
        if not release_repo_process_lock(lock_path):
            raise OSError(f"failed to release repository lock: {lock_path}")


def sync_success_marker_path(repo_root: str) -> str:
    git_dir = git_dir_for_repo_root(repo_root)
    if not git_dir:
        return ""
    return os.path.join(git_dir, SYNC_SUCCESS_MARKER_FILE_NAME)


def write_sync_success_timestamp(
    repo_root: str, timestamp_seconds: float | None = None
) -> bool:
    marker_path = sync_success_marker_path(repo_root)
    if not marker_path:
        return False
    marker_dir = os.path.dirname(marker_path)
    ts_value = float(time.time() if timestamp_seconds is None else timestamp_seconds)
    text_value = f"{int(ts_value)}\n"
    temp_path = f"{marker_path}.tmp"

    try:
        os.makedirs(marker_dir, exist_ok=True)
        with open(temp_path, "w", encoding="utf-8") as handle:
            handle.write(text_value)
        os.replace(temp_path, marker_path)
    except OSError:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        return False
    return True


def read_sync_success_timestamp(repo_root: str) -> float | None:
    marker_path = sync_success_marker_path(repo_root)
    if not marker_path:
        return None
    try:
        with open(marker_path, "r", encoding="utf-8") as handle:
            raw_value = handle.read().strip()
    except OSError:
        return None

    if not raw_value:
        return None

    try:
        return float(raw_value)
    except ValueError:
        return None


def repo_process_lock_path(repo_root: str) -> str | None:
    # Linked worktrees share refs and info/exclude, so they must share this lock.
    git_dir = git_common_dir_for_repo_root(repo_root)
    if not git_dir:
        return None
    return os.path.join(git_dir, REPO_PROCESS_LOCK_FILE_NAME)


def lock_owner_pid(lock_path: str) -> int | None:
    try:
        with open(lock_path, "r", encoding="utf-8") as lock_file:
            for raw_line in lock_file:
                line = raw_line.strip()
                if not line.startswith("pid="):
                    continue
                pid_text = line.split("=", 1)[1].strip()
                if not pid_text:
                    return None
                return int(pid_text)
    except (FileNotFoundError, OSError, ValueError):
        return None
    return None


def _posix_pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True


def _windows_pid_is_alive(pid: int) -> bool:
    try:
        import ctypes
        from ctypes import wintypes
    except (ImportError, AttributeError):
        return True

    error_invalid_parameter = 87
    process_query_limited_information = 0x1000
    still_active = 259

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
    except (AttributeError, OSError):
        return True

    handle = kernel32.OpenProcess(
        process_query_limited_information,
        False,
        pid,
    )
    if not handle:
        return ctypes.get_last_error() != error_invalid_parameter

    exit_code = wintypes.DWORD()
    try:
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def pid_is_alive(pid: int, *, operating_system: OperatingSystem) -> bool:
    if pid <= 0:
        return False
    if operating_system == "windows":
        return _windows_pid_is_alive(pid)
    return _posix_pid_is_alive(pid)


def remove_stale_repo_process_lock(
    lock_path: str,
    *,
    wait_timeout_seconds: float,
    stale_seconds: float,
    operating_system: OperatingSystem,
    on_removed: Callable[[str, float, int | None], None] | None = None,
) -> bool:
    try:
        lock_mtime_seconds = os.path.getmtime(lock_path)
    except FileNotFoundError:
        return False
    except OSError:
        return False

    lock_age_seconds = max(0.0, time.time() - lock_mtime_seconds)
    owner_pid = lock_owner_pid(lock_path)
    stale_by_pid = owner_pid is not None and not pid_is_alive(
        owner_pid,
        operating_system=operating_system,
    )
    stale_by_age = lock_age_seconds >= stale_seconds
    stale_legacy_no_pid = owner_pid is None and lock_age_seconds >= wait_timeout_seconds
    if not stale_by_pid and not stale_by_age and not stale_legacy_no_pid:
        return False

    try:
        os.remove(lock_path)
    except FileNotFoundError:
        return False
    except OSError:
        return False

    if on_removed is not None:
        on_removed(lock_path, lock_age_seconds, owner_pid)
    return True


def repo_process_lock_is_active(
    repo_root: str,
    *,
    wait_timeout_seconds: float,
    stale_seconds: float,
    operating_system: OperatingSystem,
    on_stale_removed: Callable[[str, float, int | None], None] | None = None,
) -> bool:
    lock_path = repo_process_lock_path(repo_root)
    if not lock_path:
        return False
    if not os.path.exists(lock_path):
        return False
    if remove_stale_repo_process_lock(
        lock_path,
        wait_timeout_seconds=wait_timeout_seconds,
        stale_seconds=stale_seconds,
        operating_system=operating_system,
        on_removed=on_stale_removed,
    ):
        return False
    return os.path.exists(lock_path)


def try_create_repo_process_lock(lock_path: str) -> bool:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(lock_path, flags, 0o644)
    except FileExistsError:
        return False

    try:
        payload = f"pid={os.getpid()}\ncreated_ts={int(time.time())}\n"
        os.write(fd, payload.encode("utf-8", errors="replace"))
    finally:
        os.close(fd)
    return True


def release_repo_process_lock(lock_path: str) -> bool:
    try:
        os.remove(lock_path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True
