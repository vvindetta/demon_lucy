from __future__ import annotations

import os
import subprocess
import time
from typing import Callable, Optional, Protocol

from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.path import git_dir_for_repo_root


def index_lock_path(repo_root: str) -> str | None:
    git_dir = git_dir_for_repo_root(repo_root)
    if not git_dir:
        return None
    return os.path.join(git_dir, "index.lock")


def failure_is_index_lock(error_text: str) -> bool:
    return "index.lock" in (error_text or "").lower()


def clear_stale_index_lock(
    repo_root: str,
    *,
    min_stale_age_seconds: float,
    logger,
) -> bool:
    lock_path = index_lock_path(repo_root)
    if not lock_path:
        return False

    try:
        lock_mtime_seconds = os.path.getmtime(lock_path)
    except FileNotFoundError:
        return False
    except OSError:
        logger.exception(log_record("git.index_lock_inspect_failed", repo=repo_root))
        return False

    lock_age_seconds = max(0.0, time.time() - lock_mtime_seconds)
    if lock_age_seconds < min_stale_age_seconds:
        return False

    try:
        os.remove(lock_path)
    except FileNotFoundError:
        return False
    except IsADirectoryError:
        logger.error(
            log_record("git.index_lock_remove_failed", reason="is_directory", repo=repo_root)
        )
        return False
    except OSError:
        logger.exception(log_record("git.index_lock_remove_failed", repo=repo_root))
        return False

    logger.warning(
        log_record(
            "git.index_lock_removed",
            reason="stale",
            repo=repo_root,
            age_seconds=lock_age_seconds,
        )
    )
    return True


def index_lock_age_seconds(
    repo_root: str,
    *,
    logger,
) -> float | None:
    lock_path = index_lock_path(repo_root)
    if not lock_path:
        return None
    try:
        lock_mtime_seconds = os.path.getmtime(lock_path)
    except FileNotFoundError:
        return None
    except OSError:
        logger.exception(log_record("git.index_lock_inspect_failed", repo=repo_root))
        return None
    return max(0.0, time.time() - lock_mtime_seconds)


class GitCommandExecutor(Protocol):
    def run(
        self,
        arguments: list[str],
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        ...


def run_git(
    repo_root: str,
    arguments: list[str],
    environment: dict[str, str],
    timeout_seconds: float,
    *,
    executor_factory: Callable[[str, dict[str, str]], GitCommandExecutor],
    output_getter: Callable[[subprocess.CompletedProcess[str]], str],
    failure_is_index_lock_fn: Callable[[str], bool],
    clear_stale_index_lock_fn: Callable[[str], bool],
    index_lock_age_seconds_fn: Callable[[str], Optional[float]],
    recent_retry_max_attempts: int,
    recent_retry_sleep_seconds: float,
    logger,
) -> subprocess.CompletedProcess[str]:
    executor = executor_factory(repo_root, environment)
    recent_retry_attempt = 0
    while True:
        result = executor.run(arguments=arguments, timeout_seconds=timeout_seconds)
        if result.returncode == 0:
            return result

        if not failure_is_index_lock_fn(output_getter(result)):
            return result

        if clear_stale_index_lock_fn(repo_root):
            logger.info(
                log_record(
                    "git.command_retry",
                    reason="index_lock_cleanup",
                    repo=repo_root,
                    args=" ".join(arguments[:4]),
                )
            )
            continue

        if recent_retry_attempt >= recent_retry_max_attempts:
            return result

        lock_age = index_lock_age_seconds_fn(repo_root)
        if lock_age is None:
            return result

        if recent_retry_attempt in (0, recent_retry_max_attempts - 1):
            logger.info(
                log_record(
                    "git.command_retry",
                    reason="index_lock_active",
                    repo=repo_root,
                    age_seconds=lock_age,
                    retry=f"{recent_retry_attempt + 1}/{recent_retry_max_attempts}",
                )
            )
        time.sleep(recent_retry_sleep_seconds)
        recent_retry_attempt += 1
