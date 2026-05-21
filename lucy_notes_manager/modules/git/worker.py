from __future__ import annotations

import fcntl
import logging
import os
import subprocess
import threading
import time
from typing import Any, Callable

from lucy_notes_manager.lib import safe_notify
from lucy_notes_manager.modules.git.batch_factory import make_repo_batch
from lucy_notes_manager.modules.git.helpers import (
    failure_looks_like_network_issue,
    parse_porcelain_paths,
    push_rejected_needs_pull,
)
from lucy_notes_manager.modules.git.operations import (
    abort_merge_safely,
    git_environment,
    merge_in_progress,
    resolve_merge_conflicts_with_fallback,
    run_git,
    safe_pull_merge,
)
from lucy_notes_manager.modules.git.sync_marker import write_sync_success_timestamp
from lucy_notes_manager.modules.git.types import _RepoBatch

logger = logging.getLogger(__name__)


_REPO_EVENT_LOCKS_GUARD = threading.Lock()
_REPO_EVENT_LOCKS: dict[str, threading.Lock] = {}


def _repo_event_lock(repo_root: str) -> threading.Lock:
    with _REPO_EVENT_LOCKS_GUARD:
        lock_obj = _REPO_EVENT_LOCKS.get(repo_root)
        if lock_obj is None:
            lock_obj = threading.Lock()
            _REPO_EVENT_LOCKS[repo_root] = lock_obj
        return lock_obj


def _run_event_with_repo_lock(
    self,
    repo_root: str,
    event_type: str,
    paths: list[str],
    config_snapshot: dict,
) -> bool:
    repo_lock = _repo_event_lock(repo_root)
    with repo_lock:
        return _process_event_once(
            self=self,
            repo_root=repo_root,
            event_type=event_type,
            paths=paths,
            config_snapshot=config_snapshot,
        )


def _run_event_with_retry_window_repo_locked(
    self,
    repo_root: str,
    event_type: str,
    paths: list[str],
    config_snapshot: dict,
) -> None:
    repo_lock = _repo_event_lock(repo_root)
    with repo_lock:
        _run_event_with_retry_window(
            self=self,
            repo_root=repo_root,
            event_type=event_type,
            paths=paths,
            config_snapshot=config_snapshot,
        )


def _notify_config_from_batch(batch: _RepoBatch) -> dict[str, Any]:
    return {
        "sys_notification_provider": batch.notify_provider,
        "sys_notification_min_interval_seconds": batch.notify_min_interval_sec,
        "sys_notification_error_backoff_base_seconds": (
            batch.notify_error_backoff_base_seconds
        ),
        "sys_notification_error_backoff_max_seconds": batch.notify_error_backoff_max_seconds,
        "sys_notification_error_burst_limit": batch.notify_error_burst_limit,
        "sys_notification_error_burst_window_seconds": (
            batch.notify_error_burst_window_seconds
        ),
    }


def _notify_git_network_issue(
    repo_root: str,
    command_text: str,
    reason_text: str,
    notify_config: dict[str, Any],
) -> None:
    safe_notify(
        name=f"git-network:{repo_root}",
        message=(
            f"Repository:\n{repo_root}\n\n"
            f"Git sync waiting for network.\n\n"
            f"Command:\n{command_text}\n\n"
            f"Reason:\n{reason_text[:1200]}"
        ),
        config=notify_config,
        use_rare_mode=True,
    )


def _repo_process_lock_path(repo_root: str) -> str:
    return os.path.join(repo_root, ".git", "lucy-sync.lock")


def _with_repo_process_lock(repo_root: str, run_fn: Callable[[], bool]) -> bool:
    lock_path = _repo_process_lock_path(repo_root)
    lock_dir = os.path.dirname(lock_path)
    try:
        os.makedirs(lock_dir, exist_ok=True)
        with open(lock_path, "a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                return run_fn()
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except OSError:
        logger.exception("failed to acquire repo process lock; running without lock | repo=%s", repo_root)
        return run_fn()


def _build_batch(
    self,
    repo_root: str,
    event_type: str,
    paths: list[str],
    config_snapshot: dict,
) -> _RepoBatch:
    return make_repo_batch(
        repo_root=repo_root,
        event_type=event_type,
        paths=paths,
        config_snapshot=config_snapshot,
        environment=git_environment(self, config_snapshot),
    )


def _process_event_once(
    self,
    repo_root: str,
    event_type: str,
    paths: list[str],
    config_snapshot: dict,
) -> bool:
    batch = _build_batch(
        self=self,
        repo_root=repo_root,
        event_type=event_type,
        paths=paths,
        config_snapshot=config_snapshot,
    )
    return process_batch(self, batch)


def _run_event_with_retry_window(
    self,
    repo_root: str,
    event_type: str,
    paths: list[str],
    config_snapshot: dict,
) -> None:
    retry_window_seconds = max(
        0.0, float(config_snapshot.get("git_sync_retry_window_seconds", 0.0))
    )
    backoff_start_seconds = max(
        0.2,
        float(config_snapshot.get("git_sync_retry_backoff_start_seconds", 5.0)),
    )
    backoff_max_seconds = max(
        backoff_start_seconds,
        float(config_snapshot.get("git_sync_retry_backoff_max_seconds", 60.0)),
    )

    deadline = None
    if retry_window_seconds > 0.0:
        deadline = time.monotonic() + retry_window_seconds

    delay_seconds = backoff_start_seconds
    while True:
        success = _process_event_once(
            self=self,
            repo_root=repo_root,
            event_type=event_type,
            paths=paths,
            config_snapshot=config_snapshot,
        )
        if success:
            return

        if deadline is None:
            return

        now_timestamp = time.monotonic()
        if now_timestamp >= deadline:
            return

        sleep_seconds = min(delay_seconds, deadline - now_timestamp)
        if sleep_seconds > 0.0:
            time.sleep(sleep_seconds)
        delay_seconds = min(delay_seconds * 2.0, backoff_max_seconds)


def process_event(
    self,
    repo_root: str,
    event_type: str,
    paths: list[str],
    config_snapshot: dict,
    run_in_background: bool = False,
) -> bool:
    if not run_in_background:
        return _run_event_with_repo_lock(
            self=self,
            repo_root=repo_root,
            event_type=event_type,
            paths=paths,
            config_snapshot=config_snapshot,
        )

    runner = threading.Thread(
        target=_run_event_with_retry_window_repo_locked,
        kwargs={
            "self": self,
            "repo_root": repo_root,
            "event_type": event_type,
            "paths": list(paths),
            "config_snapshot": dict(config_snapshot),
        },
        daemon=True,
    )
    runner.start()
    return True


def _ensure_merge_state_clean(
    self,
    repo_root: str,
    environment: dict[str, str],
    git_timeout_seconds: float,
    autoresolve_mode: str,
    notify_config: dict[str, Any],
) -> bool:
    if not merge_in_progress(self, repo_root, environment, git_timeout_seconds):
        return True

    resolved = resolve_merge_conflicts_with_fallback(
        self,
        repo_root,
        environment,
        git_timeout_seconds,
        autoresolve_mode=autoresolve_mode,
    )
    if resolved:
        return True

    abort_ok = abort_merge_safely(
        self=self,
        repo_root=repo_root,
        environment=environment,
        timeout_seconds=git_timeout_seconds,
    )
    abort_note = "" if abort_ok else " Merge abort failed or timed out."
    logger.error(
        "found unfinished merge; auto-resolve failed; merge aborted%s | repo=%s",
        " (abort failed)" if not abort_ok else "",
        repo_root,
    )
    safe_notify(
        name=f"merge-stuck:{repo_root}",
        message=(
            f"Repository:\n{repo_root}\n\n"
            f"Found unfinished merge; auto-resolve failed; merge aborted.{abort_note}"
        ),
        config=notify_config,
        use_rare_mode=True,
    )
    return False


def _stage_and_collect_changes(
    self,
    repo_root: str,
    environment: dict[str, str],
    git_timeout_seconds: float,
    notify_config: dict[str, Any],
) -> tuple[bool, str, list[str]]:
    try:
        add_result = run_git(
            self,
            repo_root,
            ["add", "-A"],
            environment,
            timeout_seconds=git_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.error("git add timed out | repo=%s", repo_root)
        safe_notify(
            name=f"timeout:add:{repo_root}",
            message=f"git add timed out:\n{repo_root}",
            config=notify_config,
            use_rare_mode=True,
        )
        return False, "", []

    if add_result.returncode != 0:
        add_error = (add_result.stderr or add_result.stdout or "git add failed").strip()
        logger.error("git add failed | repo=%s | error=%s", repo_root, add_error[:1200])
        safe_notify(
            name=f"addfail:{repo_root}",
            message=f"Repository:\n{repo_root}\n\nError:\n{add_error[:1200]}",
            config=notify_config,
            use_rare_mode=True,
        )
        return False, "", []

    try:
        status_result = run_git(
            self,
            repo_root,
            ["status", "--porcelain"],
            environment,
            timeout_seconds=git_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.error("git status timed out | repo=%s", repo_root)
        safe_notify(
            name=f"timeout:status:{repo_root}",
            message=f"git status timed out:\n{repo_root}",
            config=notify_config,
            use_rare_mode=True,
        )
        return False, "", []

    if status_result.returncode != 0:
        status_error = (status_result.stderr or status_result.stdout or "git status failed").strip()
        logger.error("git status failed | repo=%s | error=%s", repo_root, status_error[:1200])
        safe_notify(
            name=f"statusfail:{repo_root}",
            message=f"Repository:\n{repo_root}\n\nError:\n{status_error[:1200]}",
            config=notify_config,
            use_rare_mode=True,
        )
        return False, "", []

    porcelain_text = (status_result.stdout or "").strip()
    return True, porcelain_text, parse_porcelain_paths(porcelain_text)


def _commit_if_needed(
    self,
    batch: _RepoBatch,
    repo_root: str,
    environment: dict[str, str],
    git_timeout_seconds: float,
    porcelain_text: str,
    changed_paths: list[str],
    notify_config: dict[str, Any],
) -> bool:
    if not porcelain_text:
        return True

    commit_message = self._build_commit_message(batch, changed_paths)
    try:
        commit_result = run_git(
            self,
            repo_root,
            ["commit", "-m", commit_message],
            environment,
            timeout_seconds=git_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.error("git commit timed out | repo=%s", repo_root)
        safe_notify(
            name=f"timeout:commit:{repo_root}",
            message=f"git commit timed out:\n{repo_root}",
            config=notify_config,
            use_rare_mode=True,
        )
        return False

    if commit_result.returncode == 0:
        return True

    combined_output = (
        ((commit_result.stderr or "") + "\n" + (commit_result.stdout or ""))
        .strip()
        .lower()
    )
    if "nothing to commit" in combined_output:
        return True

    commit_error = (commit_result.stderr or commit_result.stdout or "git commit failed").strip()
    logger.error("git commit failed | repo=%s | error=%s", repo_root, commit_error[:1200])
    safe_notify(
        name=f"commitfail:{repo_root}",
        message=f"Repository:\n{repo_root}\n\nError:\n{commit_error[:1200]}",
        config=notify_config,
        use_rare_mode=True,
    )
    return False


def _run_push_once(
    self,
    repo_root: str,
    environment: dict[str, str],
    push_timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    return run_git(
        self,
        repo_root,
        ["push"],
        environment,
        timeout_seconds=push_timeout_seconds,
    )


def _push_error_text(push_result: subprocess.CompletedProcess[str]) -> str:
    return (push_result.stderr or push_result.stdout or "git push failed").strip()


def _attempt_push_with_retry(
    self,
    batch: _RepoBatch,
    repo_root: str,
    environment: dict[str, str],
    pull_timeout_seconds: float,
    push_timeout_seconds: float,
    git_timeout_seconds: float,
    notify_config: dict[str, Any],
) -> bool:
    try:
        first_push_result = _run_push_once(
            self=self,
            repo_root=repo_root,
            environment=environment,
            push_timeout_seconds=push_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.error("git push timed out | repo=%s | attempt=1/2", repo_root)
        first_push_result = None

    if first_push_result is not None and first_push_result.returncode == 0:
        return True

    if first_push_result is not None:
        first_push_error = _push_error_text(first_push_result)
        logger.error(
            "git push failed | repo=%s | attempt=1/2 | error=%s",
            repo_root,
            first_push_error[:1200],
        )

    should_pull_before_retry = False
    if first_push_result is not None:
        combined_push_output = (
            ((first_push_result.stderr or "") + "\n" + (first_push_result.stdout or ""))
            .strip()
        )
        should_pull_before_retry = (
            batch.policy.auto_merge_on_push
            and push_rejected_needs_pull(combined_push_output)
        )

    if should_pull_before_retry:
        pulled = safe_pull_merge(
            self,
            repo_root,
            environment,
            pull_timeout_seconds=pull_timeout_seconds,
            operation_timeout_seconds=git_timeout_seconds,
            autoresolve_mode=batch.policy.autoresolve_mode.value,
            notify_config=notify_config,
            auto_set_upstream=batch.policy.auto_set_upstream,
            network_probe_timeout_seconds=batch.policy.network_probe_timeout_seconds,
            pull_offline_error_markers=list(batch.policy.pull_offline_error_markers),
        )
        if not pulled:
            logger.warning(
                "git pull before push retry was skipped/failed | repo=%s",
                repo_root,
            )

    try:
        second_push_result = _run_push_once(
            self=self,
            repo_root=repo_root,
            environment=environment,
            push_timeout_seconds=push_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.error("git push timed out | repo=%s", repo_root)
        _notify_git_network_issue(
            repo_root=repo_root,
            command_text="git push",
            reason_text="git push timed out.",
            notify_config=notify_config,
        )
        return False

    if second_push_result.returncode == 0:
        return True

    push_error = _push_error_text(second_push_result)
    logger.error(
        "git push failed | repo=%s | attempt=2/2 | error=%s",
        repo_root,
        push_error[:1200],
    )
    if failure_looks_like_network_issue(
        output_text=push_error,
        error_markers=list(batch.policy.pull_offline_error_markers),
    ):
        _notify_git_network_issue(
            repo_root=repo_root,
            command_text="git push",
            reason_text=push_error,
            notify_config=notify_config,
        )
        return False

    safe_notify(
        name=f"pushfail:{repo_root}",
        message=(
            f"Repository:\n{repo_root}\n\n"
            f"Command:\ngit push\n\n"
            f"Error:\n{push_error[:1200]}"
        ),
        config=notify_config,
        use_rare_mode=True,
    )
    return False


def _process_batch_unlocked(self, batch: _RepoBatch) -> bool:
    repo_root = batch.repo_root
    environment = batch.environment
    git_timeout_seconds = batch.git_timeout_seconds
    pull_timeout_seconds = batch.pull_timeout_seconds
    push_timeout_seconds = batch.push_timeout_seconds
    notify_config = _notify_config_from_batch(batch)

    if not _ensure_merge_state_clean(
        self=self,
        repo_root=repo_root,
        environment=environment,
        git_timeout_seconds=git_timeout_seconds,
        autoresolve_mode=batch.policy.autoresolve_mode.value,
        notify_config=notify_config,
    ):
        return False

    staged_ok, porcelain_text, changed_paths = _stage_and_collect_changes(
        self=self,
        repo_root=repo_root,
        environment=environment,
        git_timeout_seconds=git_timeout_seconds,
        notify_config=notify_config,
    )
    if not staged_ok:
        return False

    if not _commit_if_needed(
        self=self,
        batch=batch,
        repo_root=repo_root,
        environment=environment,
        git_timeout_seconds=git_timeout_seconds,
        porcelain_text=porcelain_text,
        changed_paths=changed_paths,
        notify_config=notify_config,
    ):
        return False

    pushed_ok = _attempt_push_with_retry(
        self=self,
        batch=batch,
        repo_root=repo_root,
        environment=environment,
        pull_timeout_seconds=pull_timeout_seconds,
        push_timeout_seconds=push_timeout_seconds,
        git_timeout_seconds=git_timeout_seconds,
        notify_config=notify_config,
    )
    if not pushed_ok:
        return False

    if not write_sync_success_timestamp(repo_root):
        logger.warning("failed to write git sync success marker | repo=%s", repo_root)
    return True


def process_batch(self, batch: _RepoBatch) -> bool:
    return _with_repo_process_lock(
        batch.repo_root,
        lambda: _process_batch_unlocked(self, batch),
    )
