from __future__ import annotations

import os
import time
from typing import Callable, Optional


def index_lock_path(repo_root: str) -> str:
    return os.path.join(repo_root, ".git", "index.lock")


def failure_is_index_lock(error_text: str) -> bool:
    return "index.lock" in (error_text or "").lower()


def clear_stale_index_lock(
    repo_root: str,
    *,
    min_stale_age_seconds: float,
    logger,
) -> bool:
    _ = min_stale_age_seconds
    lock_path = index_lock_path(repo_root)

    try:
        os.remove(lock_path)
    except FileNotFoundError:
        return False
    except IsADirectoryError:
        logger.error("git index.lock path is a directory; cannot remove | repo=%s", repo_root)
        return False
    except OSError:
        logger.exception("failed to remove git index.lock | repo=%s", repo_root)
        return False

    logger.warning(
        "removed git index.lock before retrying command | repo=%s",
        repo_root,
    )
    return True


def index_lock_age_seconds(
    repo_root: str,
    *,
    logger,
) -> float | None:
    lock_path = index_lock_path(repo_root)
    try:
        lock_mtime_seconds = os.path.getmtime(lock_path)
    except FileNotFoundError:
        return None
    except OSError:
        logger.exception("failed to inspect git index.lock | repo=%s", repo_root)
        return None
    return max(0.0, time.time() - lock_mtime_seconds)


def run_git(
    repo_root: str,
    arguments: list[str],
    environment: dict[str, str],
    timeout_seconds: float,
    *,
    executor_factory: Callable[[str, dict[str, str]], object],
    output_getter: Callable[[object], str],
    failure_is_index_lock_fn: Callable[[str], bool],
    clear_stale_index_lock_fn: Callable[[str], bool],
    index_lock_age_seconds_fn: Callable[[str], Optional[float]],
    recent_retry_max_attempts: int,
    recent_retry_sleep_seconds: float,
    logger,
):
    executor = executor_factory(repo_root, environment)
    recent_retry_attempt = 0
    while True:
        result = executor.run(arguments=arguments, timeout_seconds=timeout_seconds)
        if result.returncode == 0:
            return result

        if not failure_is_index_lock_fn(output_getter(result)):
            return result

        if clear_stale_index_lock_fn(repo_root):
            logger.warning(
                "retrying git command after index.lock cleanup | repo=%s | args=%s",
                repo_root,
                " ".join(arguments[:4]),
            )
            continue

        if recent_retry_attempt >= recent_retry_max_attempts:
            return result

        lock_age = index_lock_age_seconds_fn(repo_root)
        if lock_age is None:
            return result

        logger.warning(
            "git index.lock is active; waiting before retry | repo=%s | age_seconds=%.1f | retry=%d/%d",
            repo_root,
            lock_age,
            recent_retry_attempt + 1,
            recent_retry_max_attempts,
        )
        time.sleep(recent_retry_sleep_seconds)
        recent_retry_attempt += 1
