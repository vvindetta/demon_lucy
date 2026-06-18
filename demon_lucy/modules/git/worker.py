from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from demon_lucy.lib.notifications import safe_notify
from demon_lucy.lib.path import git_dir_for_repo_root
from demon_lucy.modules.git.batch_factory import make_repo_batch
from demon_lucy.modules.git.commit_message import (
    GitChange,
    build_commit_message,
    changes_from_staged_diff,
)
from demon_lucy.modules.git.helpers import (
    failure_looks_like_network_issue,
    parse_porcelain_paths,
    push_rejected_needs_pull,
)
from demon_lucy.modules.git.operations import (
    abort_merge_safely,
    failure_is_index_lock,
    git_environment,
    merge_in_progress,
    resolve_merge_conflicts_with_fallback,
    run_git,
    safe_pull_merge,
)
from demon_lucy.modules.git.sync_marker import write_sync_success_timestamp
from demon_lucy.modules.git.types import _RepoBatch

logger = logging.getLogger(__name__)


_REPO_EVENT_LOCKS_GUARD = threading.Lock()
_REPO_EVENT_LOCKS: dict[str, threading.Lock] = {}
_REPO_PROCESS_LOCK_WAIT_TIMEOUT_SECONDS = 30.0
_REPO_PROCESS_LOCK_RETRY_SLEEP_SECONDS = 0.2
_REPO_PROCESS_LOCK_STALE_SECONDS = 1800.0
_INDEX_LOCK_ERROR_LOG_GUARD = threading.Lock()
_INDEX_LOCK_ERROR_LAST_LOG_TS: dict[str, float] = {}
_INDEX_LOCK_ERROR_LOG_MIN_INTERVAL_SECONDS = 30.0
_CORRUPTED_INDEX_ERROR_MARKERS = (
    "index file smaller than expected",
    "index file corrupt",
    "fatal: .git/index:",
)


@dataclass(frozen=True)
class DirtyTreeCommitResult:
    status: str
    repo_root: str
    commit_sha: str = ""
    changed_paths: tuple[str, ...] = ()
    error_text: str = ""


@dataclass(frozen=True)
class PatchPacketBuildResult:
    status: str
    repo_root: str
    patch_id: str = ""
    patch_path: str = ""
    metadata_path: str = ""
    error_text: str = ""


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


def _notify_git_sync_issue(
    repo_root: str,
    summary_text: str,
    notify_config: dict[str, Any],
    details_text: str = "",
) -> None:
    message_text = f"Repository:\n{repo_root}\n\n{summary_text}"
    if details_text:
        message_text += f"\n\nDetails:\n{details_text[:1200]}"
    safe_notify(
        name=f"git-sync:{repo_root}",
        message=message_text,
        config=notify_config,
        use_rare_mode=True,
    )


def _should_log_index_lock_error(repo_root: str) -> bool:
    now_mono_seconds = time.monotonic()
    with _INDEX_LOCK_ERROR_LOG_GUARD:
        last_logged = _INDEX_LOCK_ERROR_LAST_LOG_TS.get(repo_root)
        if (
            last_logged is not None
            and now_mono_seconds - last_logged
            < _INDEX_LOCK_ERROR_LOG_MIN_INTERVAL_SECONDS
        ):
            return False
        _INDEX_LOCK_ERROR_LAST_LOG_TS[repo_root] = now_mono_seconds
        return True


def _looks_like_corrupted_index(error_text: str) -> bool:
    normalized = (error_text or "").strip().lower()
    if not normalized:
        return False
    return any(marker in normalized for marker in _CORRUPTED_INDEX_ERROR_MARKERS)


def _attempt_rebuild_git_index(
    self,
    repo_root: str,
    environment: dict[str, str],
    git_timeout_seconds: float,
) -> bool:
    logger.warning(
        "detected corrupted git index; trying recovery reset --mixed | repo=%s",
        repo_root,
    )
    try:
        reset_result = run_git(
            self,
            repo_root,
            ["reset", "--mixed", "-q"],
            environment,
            timeout_seconds=git_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.error("git index recovery timed out | repo=%s", repo_root)
        return False

    if reset_result.returncode == 0:
        logger.warning("git index recovery succeeded | repo=%s", repo_root)
        return True

    reset_error = (
        reset_result.stderr or reset_result.stdout or "git reset failed"
    ).strip()
    logger.error(
        "git index recovery failed | repo=%s | error=%s", repo_root, reset_error[:1200]
    )
    return False


def _repo_process_lock_path(repo_root: str) -> str | None:
    git_dir = git_dir_for_repo_root(repo_root)
    if not git_dir:
        return None
    return os.path.join(git_dir, "demon_lucy-sync.lock")


def _lock_owner_pid(lock_path: str) -> int | None:
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


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True


def _remove_stale_repo_process_lock(lock_path: str) -> bool:
    try:
        lock_mtime_seconds = os.path.getmtime(lock_path)
    except FileNotFoundError:
        return False
    except OSError:
        return False

    lock_age_seconds = max(0.0, time.time() - lock_mtime_seconds)
    owner_pid = _lock_owner_pid(lock_path)
    stale_by_pid = owner_pid is not None and not _pid_is_alive(owner_pid)
    stale_by_age = lock_age_seconds >= _REPO_PROCESS_LOCK_STALE_SECONDS
    stale_legacy_no_pid = (
        owner_pid is None
        and lock_age_seconds >= _REPO_PROCESS_LOCK_WAIT_TIMEOUT_SECONDS
    )
    if not stale_by_pid and not stale_by_age and not stale_legacy_no_pid:
        return False

    try:
        os.remove(lock_path)
    except FileNotFoundError:
        return False
    except OSError:
        return False

    logger.warning(
        "removed stale repo process lock | lock=%s | age_seconds=%.1f | owner_pid=%s",
        lock_path,
        lock_age_seconds,
        owner_pid if owner_pid is not None else "unknown",
    )
    return True


def repo_process_lock_is_active(repo_root: str) -> bool:
    lock_path = _repo_process_lock_path(repo_root)
    if not lock_path:
        return False
    if not os.path.exists(lock_path):
        return False
    if _remove_stale_repo_process_lock(lock_path):
        return False
    return os.path.exists(lock_path)


def _try_create_repo_process_lock(lock_path: str) -> bool:
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


def _release_repo_process_lock(lock_path: str) -> None:
    try:
        os.remove(lock_path)
    except FileNotFoundError:
        return
    except OSError:
        logger.warning("failed to release repo process lock | lock=%s", lock_path)


def _with_repo_process_lock(repo_root: str, run_fn: Callable[[], bool]) -> bool:
    lock_path = _repo_process_lock_path(repo_root)
    if not lock_path:
        logger.warning("invalid git repo root; skipping git batch | repo=%s", repo_root)
        return False
    lock_dir = os.path.dirname(lock_path)
    try:
        os.makedirs(lock_dir, exist_ok=True)
    except OSError:
        logger.exception(
            "failed to prepare repo lock directory; running without lock | repo=%s",
            repo_root,
        )
        return run_fn()

    deadline = time.monotonic() + _REPO_PROCESS_LOCK_WAIT_TIMEOUT_SECONDS
    while True:
        try:
            acquired = _try_create_repo_process_lock(lock_path)
        except OSError:
            logger.exception(
                "failed to create repo process lock; running without lock | repo=%s",
                repo_root,
            )
            return run_fn()

        if acquired:
            try:
                return run_fn()
            finally:
                _release_repo_process_lock(lock_path)

        if _remove_stale_repo_process_lock(lock_path):
            continue

        if time.monotonic() >= deadline:
            logger.warning(
                "repo process lock is busy; skipping git batch for now | repo=%s",
                repo_root,
            )
            return False
        time.sleep(_REPO_PROCESS_LOCK_RETRY_SLEEP_SECONDS)


def _with_repo_process_lock_status(
    repo_root: str,
    run_fn: Callable[[], DirtyTreeCommitResult | PatchPacketBuildResult],
    *,
    on_busy_fn: Callable[[], DirtyTreeCommitResult | PatchPacketBuildResult],
    on_invalid_repo_fn: Callable[[], DirtyTreeCommitResult | PatchPacketBuildResult],
) -> DirtyTreeCommitResult | PatchPacketBuildResult:
    lock_path = _repo_process_lock_path(repo_root)
    if not lock_path:
        logger.warning(
            "invalid git repo root; skipping git lock-wrapped operation | repo=%s",
            repo_root,
        )
        return on_invalid_repo_fn()
    lock_dir = os.path.dirname(lock_path)
    try:
        os.makedirs(lock_dir, exist_ok=True)
    except OSError:
        logger.exception(
            "failed to prepare repo lock directory; running without lock | repo=%s",
            repo_root,
        )
        return run_fn()

    deadline = time.monotonic() + _REPO_PROCESS_LOCK_WAIT_TIMEOUT_SECONDS
    while True:
        try:
            acquired = _try_create_repo_process_lock(lock_path)
        except OSError:
            logger.exception(
                "failed to create repo process lock; running without lock | repo=%s",
                repo_root,
            )
            return run_fn()

        if acquired:
            try:
                return run_fn()
            finally:
                _release_repo_process_lock(lock_path)

        if _remove_stale_repo_process_lock(lock_path):
            continue

        if time.monotonic() >= deadline:
            return on_busy_fn()
        time.sleep(_REPO_PROCESS_LOCK_RETRY_SLEEP_SECONDS)


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
    if not abort_ok:
        rebuilt = _attempt_rebuild_git_index(
            self=self,
            repo_root=repo_root,
            environment=environment,
            git_timeout_seconds=git_timeout_seconds,
        )
        if rebuilt:
            abort_ok = abort_merge_safely(
                self=self,
                repo_root=repo_root,
                environment=environment,
                timeout_seconds=git_timeout_seconds,
            )
            if abort_ok:
                logger.warning(
                    "merge abort succeeded after git index recovery | repo=%s",
                    repo_root,
                )
    abort_note = "" if abort_ok else " Merge abort failed or timed out."
    logger.error(
        "found unfinished merge; auto-resolve failed; merge aborted%s | repo=%s",
        " (abort failed)" if not abort_ok else "",
        repo_root,
    )
    _notify_git_sync_issue(
        repo_root=repo_root,
        summary_text=f"Found unfinished merge; auto-resolve failed; merge aborted.{abort_note}",
        notify_config=notify_config,
    )
    return False


def _stage_and_collect_changes(
    self,
    repo_root: str,
    environment: dict[str, str],
    git_timeout_seconds: float,
    notify_config: dict[str, Any],
) -> tuple[bool, str, list[str]]:
    recovered_index = False
    while True:
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
            _notify_git_sync_issue(
                repo_root=repo_root,
                summary_text="git add timed out.",
                notify_config=notify_config,
            )
            return False, "", []

        if add_result.returncode == 0:
            break

        add_error = (add_result.stderr or add_result.stdout or "git add failed").strip()
        if failure_is_index_lock(add_error):
            if _should_log_index_lock_error(repo_root):
                logger.warning(
                    "git add blocked by active index.lock; will retry later | repo=%s",
                    repo_root,
                )
            return False, "", []

        if not recovered_index and _looks_like_corrupted_index(add_error):
            recovered_index = _attempt_rebuild_git_index(
                self=self,
                repo_root=repo_root,
                environment=environment,
                git_timeout_seconds=git_timeout_seconds,
            )
            if recovered_index:
                continue

        logger.error("git add failed | repo=%s | error=%s", repo_root, add_error[:1200])
        _notify_git_sync_issue(
            repo_root=repo_root,
            summary_text="git add failed.",
            notify_config=notify_config,
            details_text=add_error,
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
        _notify_git_sync_issue(
            repo_root=repo_root,
            summary_text="git status timed out.",
            notify_config=notify_config,
        )
        return False, "", []

    if status_result.returncode != 0:
        status_error = (
            status_result.stderr or status_result.stdout or "git status failed"
        ).strip()
        logger.error(
            "git status failed | repo=%s | error=%s", repo_root, status_error[:1200]
        )
        _notify_git_sync_issue(
            repo_root=repo_root,
            summary_text="git status failed.",
            notify_config=notify_config,
            details_text=status_error,
        )
        return False, "", []

    porcelain_text = (status_result.stdout or "").strip()
    return True, porcelain_text, parse_porcelain_paths(porcelain_text)


def _collect_staged_changes_for_commit_message(
    self,
    repo_root: str,
    environment: dict[str, str],
    git_timeout_seconds: float,
) -> list[GitChange]:
    try:
        name_status_result = run_git(
            self,
            repo_root,
            ["diff", "--cached", "--name-status", "-z"],
            environment,
            timeout_seconds=git_timeout_seconds,
        )
        numstat_result = run_git(
            self,
            repo_root,
            ["diff", "--cached", "--numstat", "-z"],
            environment,
            timeout_seconds=git_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "git diff for commit message timed out; using fallback message | repo=%s",
            repo_root,
        )
        return []

    if name_status_result.returncode != 0 or numstat_result.returncode != 0:
        details = (
            name_status_result.stderr
            or numstat_result.stderr
            or name_status_result.stdout
            or numstat_result.stdout
            or "git diff failed"
        ).strip()
        logger.warning(
            "git diff for commit message failed; using fallback message | repo=%s | error=%s",
            repo_root,
            details[:1200],
        )
        return []

    return changes_from_staged_diff(
        name_status_z=name_status_result.stdout or "",
        numstat_z=numstat_result.stdout or "",
        repo_root=repo_root,
    )


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

    staged_changes = _collect_staged_changes_for_commit_message(
        self=self,
        repo_root=repo_root,
        environment=environment,
        git_timeout_seconds=git_timeout_seconds,
    )
    commit_message = build_commit_message(
        batch,
        changed_paths,
        changes=staged_changes,
    )
    try:
        commit_result = run_git(
            self,
            repo_root,
            commit_message.to_git_args(),
            environment,
            timeout_seconds=git_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.error("git commit timed out | repo=%s", repo_root)
        _notify_git_sync_issue(
            repo_root=repo_root,
            summary_text="git commit timed out.",
            notify_config=notify_config,
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

    commit_error = (
        commit_result.stderr or commit_result.stdout or "git commit failed"
    ).strip()
    logger.error(
        "git commit failed | repo=%s | error=%s", repo_root, commit_error[:1200]
    )
    _notify_git_sync_issue(
        repo_root=repo_root,
        summary_text="git commit failed.",
        notify_config=notify_config,
        details_text=commit_error,
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
            (first_push_result.stderr or "") + "\n" + (first_push_result.stdout or "")
        ).strip()
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

    _notify_git_sync_issue(
        repo_root=repo_root,
        summary_text="git push failed.",
        notify_config=notify_config,
        details_text=push_error,
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


def _head_commit_sha(
    self,
    repo_root: str,
    environment: dict[str, str],
    git_timeout_seconds: float,
) -> str:
    head_result = run_git(
        self,
        repo_root,
        ["rev-parse", "HEAD"],
        environment,
        timeout_seconds=git_timeout_seconds,
    )
    if head_result.returncode != 0:
        return ""
    return (head_result.stdout or "").strip()


def _changed_paths_for_commit(
    self,
    repo_root: str,
    commit_sha: str,
    environment: dict[str, str],
    git_timeout_seconds: float,
) -> tuple[str, ...]:
    if not commit_sha:
        return ()
    show_result = run_git(
        self,
        repo_root,
        ["show", "--pretty=format:", "--name-only", "--diff-filter=ACDMR", commit_sha],
        environment,
        timeout_seconds=git_timeout_seconds,
    )
    if show_result.returncode != 0:
        return ()
    paths: list[str] = []
    for line_text in (show_result.stdout or "").splitlines():
        path_text = line_text.strip()
        if not path_text:
            continue
        paths.append(path_text)
    return tuple(paths)


def commit_dirty_tree(
    self,
    *,
    repo_root: str,
    event_type: str,
    paths: list[str],
    config_snapshot: dict,
) -> DirtyTreeCommitResult:
    batch = _build_batch(
        self=self,
        repo_root=repo_root,
        event_type=event_type,
        paths=paths,
        config_snapshot=config_snapshot,
    )
    notify_config = _notify_config_from_batch(batch)

    def _run_commit() -> DirtyTreeCommitResult:
        if not _ensure_merge_state_clean(
            self=self,
            repo_root=repo_root,
            environment=batch.environment,
            git_timeout_seconds=batch.git_timeout_seconds,
            autoresolve_mode=batch.policy.autoresolve_mode.value,
            notify_config=notify_config,
        ):
            return DirtyTreeCommitResult(
                status="error",
                repo_root=repo_root,
                error_text="merge state is not clean",
            )

        staged_ok, porcelain_text, changed_paths = _stage_and_collect_changes(
            self=self,
            repo_root=repo_root,
            environment=batch.environment,
            git_timeout_seconds=batch.git_timeout_seconds,
            notify_config=notify_config,
        )
        if not staged_ok:
            return DirtyTreeCommitResult(
                status="error",
                repo_root=repo_root,
                error_text="failed to stage changes",
            )
        if not porcelain_text:
            return DirtyTreeCommitResult(
                status="noop",
                repo_root=repo_root,
            )

        committed_ok = _commit_if_needed(
            self=self,
            batch=batch,
            repo_root=repo_root,
            environment=batch.environment,
            git_timeout_seconds=batch.git_timeout_seconds,
            porcelain_text=porcelain_text,
            changed_paths=changed_paths,
            notify_config=notify_config,
        )
        if not committed_ok:
            return DirtyTreeCommitResult(
                status="error",
                repo_root=repo_root,
                error_text="git commit failed",
            )

        commit_sha = _head_commit_sha(
            self=self,
            repo_root=repo_root,
            environment=batch.environment,
            git_timeout_seconds=batch.git_timeout_seconds,
        )
        if not commit_sha:
            return DirtyTreeCommitResult(
                status="error",
                repo_root=repo_root,
                error_text="failed to read HEAD commit",
            )

        return DirtyTreeCommitResult(
            status="committed",
            repo_root=repo_root,
            commit_sha=commit_sha,
            changed_paths=_changed_paths_for_commit(
                self=self,
                repo_root=repo_root,
                commit_sha=commit_sha,
                environment=batch.environment,
                git_timeout_seconds=batch.git_timeout_seconds,
            ),
        )

    result = _with_repo_process_lock_status(
        repo_root,
        _run_commit,
        on_busy_fn=lambda: DirtyTreeCommitResult(
            status="busy",
            repo_root=repo_root,
            error_text="repo lock is busy",
        ),
        on_invalid_repo_fn=lambda: DirtyTreeCommitResult(
            status="error",
            repo_root=repo_root,
            error_text="invalid git repo root",
        ),
    )
    if isinstance(result, DirtyTreeCommitResult):
        return result
    return DirtyTreeCommitResult(
        status="error",
        repo_root=repo_root,
        error_text="unexpected lock result type",
    )


def build_patch_packet(
    self,
    *,
    repo_root: str,
    commit_sha: str,
    changed_paths: list[str],
    queue_dir: str,
    author_device: str,
    config_snapshot: dict,
) -> PatchPacketBuildResult:
    batch = _build_batch(
        self=self,
        repo_root=repo_root,
        event_type="modified",
        paths=changed_paths,
        config_snapshot=config_snapshot,
    )

    if not commit_sha.strip():
        return PatchPacketBuildResult(
            status="error",
            repo_root=repo_root,
            error_text="empty commit sha",
        )

    def _run_build() -> PatchPacketBuildResult:
        patch_result = run_git(
            self,
            repo_root,
            ["format-patch", "--stdout", "-1", commit_sha],
            batch.environment,
            timeout_seconds=batch.git_timeout_seconds,
        )
        if patch_result.returncode != 0:
            patch_error = (
                patch_result.stderr or patch_result.stdout or "git format-patch failed"
            ).strip()
            return PatchPacketBuildResult(
                status="error",
                repo_root=repo_root,
                error_text=patch_error,
            )

        patch_id = f"{int(time.time())}-{commit_sha[:12]}"
        os.makedirs(queue_dir, exist_ok=True)
        patch_path = os.path.join(queue_dir, f"{patch_id}.patch")
        metadata_path = os.path.join(queue_dir, f"{patch_id}.json")

        parent_result = run_git(
            self,
            repo_root,
            ["rev-parse", f"{commit_sha}^"],
            batch.environment,
            timeout_seconds=batch.git_timeout_seconds,
        )
        base_commit = (
            (parent_result.stdout or "").strip()
            if parent_result.returncode == 0
            else ""
        )

        metadata = {
            "patch_id": patch_id,
            "origin_commit": commit_sha,
            "base_commit": base_commit,
            "path_list": list(changed_paths),
            "created_at": int(time.time()),
            "author_device": author_device,
        }

        tmp_patch_path = patch_path + ".tmp"
        tmp_metadata_path = metadata_path + ".tmp"
        with open(tmp_patch_path, "w", encoding="utf-8", newline="") as patch_file:
            patch_file.write(patch_result.stdout or "")
            patch_file.flush()
            os.fsync(patch_file.fileno())
        os.replace(tmp_patch_path, patch_path)

        with open(
            tmp_metadata_path, "w", encoding="utf-8", newline=""
        ) as metadata_file:
            json.dump(metadata, metadata_file, ensure_ascii=True, sort_keys=True)
            metadata_file.write("\n")
            metadata_file.flush()
            os.fsync(metadata_file.fileno())
        os.replace(tmp_metadata_path, metadata_path)

        return PatchPacketBuildResult(
            status="built",
            repo_root=repo_root,
            patch_id=patch_id,
            patch_path=patch_path,
            metadata_path=metadata_path,
        )

    result = _with_repo_process_lock_status(
        repo_root,
        _run_build,
        on_busy_fn=lambda: PatchPacketBuildResult(
            status="busy",
            repo_root=repo_root,
            error_text="repo lock is busy",
        ),
        on_invalid_repo_fn=lambda: PatchPacketBuildResult(
            status="error",
            repo_root=repo_root,
            error_text="invalid git repo root",
        ),
    )
    if isinstance(result, PatchPacketBuildResult):
        return result
    return PatchPacketBuildResult(
        status="error",
        repo_root=repo_root,
        error_text="unexpected lock result type",
    )
