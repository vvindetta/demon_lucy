from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable

from demon_lucy.lib.args.models import ParsedArgs
from demon_lucy.lib.git_state import (
    release_repo_process_lock,
    remove_stale_repo_process_lock,
    repo_process_lock_path,
    try_create_repo_process_lock,
    write_sync_success_timestamp,
)
from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.notifications import safe_notify
from demon_lucy.lib.operating_system import OperatingSystem
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
    git_environment,
    merge_in_progress,
    resolve_merge_conflicts_with_fallback,
    run_git,
    safe_pull_merge,
)
from demon_lucy.modules.git.ops import command_ops
from demon_lucy.modules.git.types import MergeAutoresolveMode, _RepoBatch

logger = logging.getLogger(__name__)


_REPO_EVENT_LOCKS_GUARD = threading.Lock()
_REPO_EVENT_LOCKS: dict[str, threading.Lock] = {}
_INDEX_LOCK_ERROR_LOG_GUARD = threading.Lock()
_INDEX_LOCK_ERROR_LAST_LOG_TS: dict[str, float] = {}
_INDEX_LOCK_ERROR_LOG_MIN_INTERVAL_SECONDS = 30.0
_CORRUPTED_INDEX_ERROR_MARKERS = (
    "index file smaller than expected",
    "index file corrupt",
    "fatal: .git/index:",
)

SyncFailureNotifier = Callable[[str, str], None]


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


@dataclass
class _PendingSyncFailure:
    summary_text: str = ""
    details_text: str = ""

    def clear(self) -> None:
        self.summary_text = ""
        self.details_text = ""

    def record(self, summary_text: str, details_text: str = "") -> None:
        self.summary_text = summary_text
        self.details_text = details_text

    @property
    def exists(self) -> bool:
        return bool(self.summary_text)


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
    args: ParsedArgs,
    operating_system: OperatingSystem,
) -> bool:
    repo_lock = _repo_event_lock(repo_root)
    with repo_lock:
        return _process_event_once(
            self=self,
            repo_root=repo_root,
            event_type=event_type,
            paths=paths,
            args=args,
            operating_system=operating_system,
        )


def _run_event_with_retry_window_repo_locked(
    self,
    repo_root: str,
    event_type: str,
    paths: list[str],
    args: ParsedArgs,
    operating_system: OperatingSystem,
) -> None:
    repo_lock = _repo_event_lock(repo_root)
    with repo_lock:
        _run_event_with_retry_window(
            self=self,
            repo_root=repo_root,
            event_type=event_type,
            paths=paths,
            args=args,
            operating_system=operating_system,
        )


def _notify_git_sync_issue(
    repo_root: str,
    summary_text: str,
    args: ParsedArgs,
    details_text: str = "",
    failure_notifier: SyncFailureNotifier | None = None,
) -> None:
    if failure_notifier is not None:
        failure_notifier(summary_text, details_text)
        return

    message_text = f"Repository:\n{repo_root}\n\n{summary_text}"
    if details_text:
        message_text += f"\n\nDetails:\n{details_text[:1200]}"
    safe_notify(
        name=f"git-sync:{repo_root}",
        message=message_text,
        args=args,
        use_rare_mode=True,
    )


def _log_sync_failure(
    action: str,
    *,
    failure_notifier: SyncFailureNotifier | None,
    **fields,
) -> None:
    log_method = logger.warning if failure_notifier is not None else logger.error
    log_method(log_record(action, **fields))


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
    failure_notifier: SyncFailureNotifier | None = None,
) -> bool:
    logger.warning(
        log_record("git.index_recovery_start", reason="corrupted_index", repo=repo_root)
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
        _log_sync_failure(
            "git.index_recovery_failed",
            failure_notifier=failure_notifier,
            reason="timeout",
            repo=repo_root,
        )
        return False

    if reset_result.returncode == 0:
        logger.warning(log_record("git.index_recovery_done", repo=repo_root))
        return True

    reset_error = (
        reset_result.stderr or reset_result.stdout or "git reset failed"
    ).strip()
    _log_sync_failure(
        "git.index_recovery_failed",
        failure_notifier=failure_notifier,
        repo=repo_root,
        error=reset_error[:1200],
    )
    return False


def _log_stale_repo_process_lock_removed(
    lock_path: str,
    lock_age_seconds: float,
    owner_pid: int | None,
) -> None:
    logger.warning(
        log_record(
            "git.repo_lock_removed",
            reason="stale",
            lock=lock_path,
            age_seconds=lock_age_seconds,
            owner_pid=owner_pid if owner_pid is not None else "unknown",
        )
    )


def _with_repo_process_lock(
    repo_root: str,
    run_fn: Callable[[], bool],
    *,
    wait_timeout_seconds: float,
    retry_sleep_seconds: float,
    stale_seconds: float,
    operating_system: OperatingSystem,
) -> bool:
    lock_path = repo_process_lock_path(repo_root)
    if not lock_path:
        logger.warning(
            log_record("git.batch_skip", reason="invalid_repo_root", repo=repo_root)
        )
        return False
    lock_dir = os.path.dirname(lock_path)
    try:
        os.makedirs(lock_dir, exist_ok=True)
    except OSError:
        logger.exception(log_record("git.repo_lock_prepare_failed", repo=repo_root))
        return run_fn()

    wait_timeout_seconds = max(0.0, wait_timeout_seconds)
    retry_sleep_seconds = max(0.01, retry_sleep_seconds)
    stale_seconds = max(0.0, stale_seconds)
    deadline = time.monotonic() + wait_timeout_seconds
    while True:
        try:
            acquired = try_create_repo_process_lock(lock_path)
        except OSError:
            logger.exception(log_record("git.repo_lock_create_failed", repo=repo_root))
            return run_fn()

        if acquired:
            try:
                return run_fn()
            finally:
                if not release_repo_process_lock(lock_path):
                    logger.warning(
                        log_record("git.repo_lock_release_failed", lock=lock_path)
                    )

        if remove_stale_repo_process_lock(
            lock_path,
            wait_timeout_seconds=wait_timeout_seconds,
            stale_seconds=stale_seconds,
            operating_system=operating_system,
            on_removed=_log_stale_repo_process_lock_removed,
        ):
            continue

        if time.monotonic() >= deadline:
            logger.warning(
                log_record("git.batch_skip", reason="repo_lock_busy", repo=repo_root)
            )
            return False
        time.sleep(retry_sleep_seconds)


def _with_repo_process_lock_status(
    repo_root: str,
    run_fn: Callable[[], DirtyTreeCommitResult | PatchPacketBuildResult],
    *,
    wait_timeout_seconds: float,
    retry_sleep_seconds: float,
    stale_seconds: float,
    operating_system: OperatingSystem,
    on_busy_fn: Callable[[], DirtyTreeCommitResult | PatchPacketBuildResult],
    on_invalid_repo_fn: Callable[[], DirtyTreeCommitResult | PatchPacketBuildResult],
) -> DirtyTreeCommitResult | PatchPacketBuildResult:
    lock_path = repo_process_lock_path(repo_root)
    if not lock_path:
        logger.warning(
            log_record(
                "git.locked_operation_skip",
                reason="invalid_repo_root",
                repo=repo_root,
            )
        )
        return on_invalid_repo_fn()
    lock_dir = os.path.dirname(lock_path)
    try:
        os.makedirs(lock_dir, exist_ok=True)
    except OSError:
        logger.exception(log_record("git.repo_lock_prepare_failed", repo=repo_root))
        return run_fn()

    wait_timeout_seconds = max(0.0, wait_timeout_seconds)
    retry_sleep_seconds = max(0.01, retry_sleep_seconds)
    stale_seconds = max(0.0, stale_seconds)
    deadline = time.monotonic() + wait_timeout_seconds
    while True:
        try:
            acquired = try_create_repo_process_lock(lock_path)
        except OSError:
            logger.exception(log_record("git.repo_lock_create_failed", repo=repo_root))
            return run_fn()

        if acquired:
            try:
                return run_fn()
            finally:
                if not release_repo_process_lock(lock_path):
                    logger.warning(
                        log_record("git.repo_lock_release_failed", lock=lock_path)
                    )

        if remove_stale_repo_process_lock(
            lock_path,
            wait_timeout_seconds=wait_timeout_seconds,
            stale_seconds=stale_seconds,
            operating_system=operating_system,
            on_removed=_log_stale_repo_process_lock_removed,
        ):
            continue

        if time.monotonic() >= deadline:
            return on_busy_fn()
        time.sleep(retry_sleep_seconds)


def _process_event_once(
    self,
    repo_root: str,
    event_type: str,
    paths: list[str],
    args: ParsedArgs,
    operating_system: OperatingSystem,
    failure_notifier: SyncFailureNotifier | None = None,
) -> bool:
    batch = make_repo_batch(
        repo_root=repo_root,
        event_type=event_type,
        paths=paths,
        args=args,
        environment=git_environment(),
    )
    if failure_notifier is None:
        return process_batch(
            self,
            batch,
            args,
            operating_system=operating_system,
        )
    return process_batch(
        self,
        batch,
        args,
        operating_system=operating_system,
        failure_notifier=failure_notifier,
    )


def _run_event_with_retry_window(
    self,
    repo_root: str,
    event_type: str,
    paths: list[str],
    args: ParsedArgs,
    operating_system: OperatingSystem,
) -> None:
    retry_window_seconds = max(
        0.0,
        args.require("git-sync-retry-window-seconds").value,
    )
    backoff_start_seconds = max(
        0.2,
        args.require("git-sync-retry-backoff-start-seconds").value,
    )
    backoff_max_seconds = max(
        backoff_start_seconds,
        args.require("git-sync-retry-backoff-max-seconds").value,
    )

    deadline = None
    pending_failure = None
    if retry_window_seconds > 0.0:
        deadline = time.monotonic() + retry_window_seconds
        pending_failure = _PendingSyncFailure()

    delay_seconds = backoff_start_seconds
    attempt_number = 0
    while True:
        attempt_number += 1
        if pending_failure is not None:
            pending_failure.clear()
        success = _process_event_once(
            self=self,
            repo_root=repo_root,
            event_type=event_type,
            paths=paths,
            args=args,
            operating_system=operating_system,
            failure_notifier=(
                pending_failure.record if pending_failure is not None else None
            ),
        )
        if success:
            if attempt_number > 1:
                logger.info(
                    log_record(
                        "git.sync_recovered",
                        repo=repo_root,
                        attempt=attempt_number,
                    )
                )
            return

        if deadline is None:
            return

        now_timestamp = time.monotonic()
        if now_timestamp >= deadline:
            if pending_failure is not None and pending_failure.exists:
                final_error = (
                    pending_failure.details_text or pending_failure.summary_text
                )
                logger.error(
                    log_record(
                        "git.sync_failed",
                        reason="retry_window_exhausted",
                        repo=repo_root,
                        attempt=attempt_number,
                        error=final_error[:1200],
                    )
                )
                _notify_git_sync_issue(
                    repo_root=repo_root,
                    summary_text=pending_failure.summary_text,
                    details_text=pending_failure.details_text,
                    args=args,
                )
            else:
                logger.warning(
                    log_record(
                        "git.sync_retry_exhausted",
                        reason="retryable_failure",
                        repo=repo_root,
                        attempt=attempt_number,
                    )
                )
            return

        sleep_seconds = min(delay_seconds, deadline - now_timestamp)
        logger.info(
            log_record(
                "git.sync_retry",
                reason="batch_failed",
                repo=repo_root,
                attempt=attempt_number,
                retry_in_seconds=round(sleep_seconds, 3),
            )
        )
        if sleep_seconds > 0.0:
            time.sleep(sleep_seconds)
        delay_seconds = min(delay_seconds * 2.0, backoff_max_seconds)


def process_event(
    self,
    repo_root: str,
    event_type: str,
    paths: list[str],
    args: ParsedArgs,
    operating_system: OperatingSystem,
    run_in_background: bool = False,
) -> bool:
    if not run_in_background:
        return _run_event_with_repo_lock(
            self=self,
            repo_root=repo_root,
            event_type=event_type,
            paths=paths,
            args=args,
            operating_system=operating_system,
        )

    runner = threading.Thread(
        target=_run_event_with_retry_window_repo_locked,
        kwargs={
            "self": self,
            "repo_root": repo_root,
            "event_type": event_type,
            "paths": list(paths),
            "args": args,
            "operating_system": operating_system,
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
    autoresolve_mode: MergeAutoresolveMode,
    args: ParsedArgs,
    failure_notifier: SyncFailureNotifier | None = None,
) -> bool:
    if not merge_in_progress(self, repo_root, environment, git_timeout_seconds):
        return True

    resolved = resolve_merge_conflicts_with_fallback(
        self,
        repo_root,
        environment,
        git_timeout_seconds,
        autoresolve_mode=autoresolve_mode,
        failure_is_deferred=failure_notifier is not None,
    )
    if resolved:
        return True

    abort_ok = abort_merge_safely(
        self=self,
        repo_root=repo_root,
        environment=environment,
        timeout_seconds=git_timeout_seconds,
        failure_is_deferred=failure_notifier is not None,
    )
    if not abort_ok:
        rebuilt = _attempt_rebuild_git_index(
            self=self,
            repo_root=repo_root,
            environment=environment,
            git_timeout_seconds=git_timeout_seconds,
            failure_notifier=failure_notifier,
        )
        if rebuilt:
            abort_ok = abort_merge_safely(
                self=self,
                repo_root=repo_root,
                environment=environment,
                timeout_seconds=git_timeout_seconds,
                failure_is_deferred=failure_notifier is not None,
            )
            if abort_ok:
                logger.warning(
                    log_record(
                        "git.merge_abort_done", after="index_recovery", repo=repo_root
                    )
                )
    abort_note = "" if abort_ok else " Merge abort failed or timed out."
    _log_sync_failure(
        "git.merge_unfinished",
        failure_notifier=failure_notifier,
        autoresolve="failed",
        abort="failed" if not abort_ok else "done",
        repo=repo_root,
    )
    _notify_git_sync_issue(
        repo_root=repo_root,
        summary_text=f"Found unfinished merge; auto-resolve failed; merge aborted.{abort_note}",
        args=args,
        failure_notifier=failure_notifier,
    )
    return False


def _stage_and_collect_changes(
    self,
    repo_root: str,
    environment: dict[str, str],
    git_timeout_seconds: float,
    args: ParsedArgs,
    failure_notifier: SyncFailureNotifier | None = None,
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
            _log_sync_failure(
                "git.stage_failed",
                failure_notifier=failure_notifier,
                reason="timeout",
                repo=repo_root,
            )
            _notify_git_sync_issue(
                repo_root=repo_root,
                summary_text="git add timed out.",
                args=args,
                failure_notifier=failure_notifier,
            )
            return False, "", []

        if add_result.returncode == 0:
            break

        add_error = (add_result.stderr or add_result.stdout or "git add failed").strip()
        if command_ops.failure_is_index_lock(add_error):
            if _should_log_index_lock_error(repo_root):
                logger.info(
                    log_record(
                        "git.sync_skip",
                        reason="index_lock_active",
                        stage="add",
                        repo=repo_root,
                    )
                )
            return False, "", []

        if not recovered_index and _looks_like_corrupted_index(add_error):
            recovered_index = _attempt_rebuild_git_index(
                self=self,
                repo_root=repo_root,
                environment=environment,
                git_timeout_seconds=git_timeout_seconds,
                failure_notifier=failure_notifier,
            )
            if recovered_index:
                continue

        _log_sync_failure(
            "git.stage_failed",
            failure_notifier=failure_notifier,
            repo=repo_root,
            error=add_error[:1200],
        )
        _notify_git_sync_issue(
            repo_root=repo_root,
            summary_text="git add failed.",
            args=args,
            details_text=add_error,
            failure_notifier=failure_notifier,
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
        _log_sync_failure(
            "git.status_failed",
            failure_notifier=failure_notifier,
            reason="timeout",
            repo=repo_root,
        )
        _notify_git_sync_issue(
            repo_root=repo_root,
            summary_text="git status timed out.",
            args=args,
            failure_notifier=failure_notifier,
        )
        return False, "", []

    if status_result.returncode != 0:
        status_error = (
            status_result.stderr or status_result.stdout or "git status failed"
        ).strip()
        _log_sync_failure(
            "git.status_failed",
            failure_notifier=failure_notifier,
            repo=repo_root,
            error=status_error[:1200],
        )
        _notify_git_sync_issue(
            repo_root=repo_root,
            summary_text="git status failed.",
            args=args,
            details_text=status_error,
            failure_notifier=failure_notifier,
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
        logger.info(
            log_record(
                "git.commit_message_fallback",
                reason="diff_timeout",
                repo=repo_root,
            )
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
        logger.info(
            log_record(
                "git.commit_message_fallback",
                reason="diff_failed",
                repo=repo_root,
                error=details[:1200],
            )
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
    args: ParsedArgs,
    failure_notifier: SyncFailureNotifier | None = None,
) -> bool:
    if not porcelain_text:
        return True

    try:
        staged_result = run_git(
            self,
            repo_root,
            ["diff", "--cached", "--quiet", "--exit-code"],
            environment,
            timeout_seconds=git_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        staged_result = None
        logger.warning(
            log_record(
                "git.commit_check_failed",
                reason="timeout",
                repo=repo_root,
            )
        )

    if staged_result is not None and staged_result.returncode == 0:
        logger.info(
            log_record(
                "git.commit_skip",
                reason="no_staged_changes",
                repo=repo_root,
            )
        )
        return True
    if staged_result is not None and staged_result.returncode not in {0, 1}:
        check_error = (staged_result.stderr or staged_result.stdout or "").strip()
        logger.warning(
            log_record(
                "git.commit_check_failed",
                reason="git_diff_failed",
                repo=repo_root,
                error=check_error[:1200],
            )
        )

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
        _log_sync_failure(
            "git.commit_failed",
            failure_notifier=failure_notifier,
            reason="timeout",
            repo=repo_root,
        )
        _notify_git_sync_issue(
            repo_root=repo_root,
            summary_text="git commit timed out.",
            args=args,
            failure_notifier=failure_notifier,
        )
        return False

    if commit_result.returncode == 0:
        return True

    combined_output = (
        ((commit_result.stderr or "") + "\n" + (commit_result.stdout or ""))
        .strip()
        .lower()
    )
    if (
        "nothing to commit" in combined_output
        or "nothing added to commit" in combined_output
    ):
        logger.info(
            log_record(
                "git.commit_skip",
                reason="staged_changes_consumed",
                repo=repo_root,
            )
        )
        return True

    commit_error = (
        commit_result.stderr or commit_result.stdout or "git commit failed"
    ).strip()
    _log_sync_failure(
        "git.commit_failed",
        failure_notifier=failure_notifier,
        repo=repo_root,
        error=commit_error[:1200],
    )
    _notify_git_sync_issue(
        repo_root=repo_root,
        summary_text="git commit failed.",
        args=args,
        details_text=commit_error,
        failure_notifier=failure_notifier,
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
    args: ParsedArgs,
    failure_notifier: SyncFailureNotifier | None = None,
) -> bool:
    try:
        first_push_result = _run_push_once(
            self=self,
            repo_root=repo_root,
            environment=environment,
            push_timeout_seconds=push_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            log_record(
                "git.push_retry",
                reason="timeout",
                repo=repo_root,
                attempt="1/2",
            )
        )
        first_push_result = None

    if first_push_result is not None and first_push_result.returncode == 0:
        return True

    if first_push_result is not None:
        first_push_error = _push_error_text(first_push_result)
        logger.warning(
            log_record(
                "git.push_retry",
                reason="push_failed",
                repo=repo_root,
                attempt="1/2",
                error=first_push_error[:1200],
            )
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
            autoresolve_mode=batch.policy.autoresolve_mode,
            args=args,
            auto_set_upstream=batch.policy.auto_set_upstream,
            network_probe_timeout_seconds=batch.policy.network_probe_timeout_seconds,
            pull_offline_error_markers=list(batch.policy.pull_offline_error_markers),
            remote_already_reached=True,
            failure_notifier=failure_notifier,
        )
        if not pulled:
            logger.warning(
                log_record(
                    "git.push_retry",
                    reason="pre_retry_pull_failed",
                    repo=repo_root,
                )
            )
            return False

    try:
        second_push_result = _run_push_once(
            self=self,
            repo_root=repo_root,
            environment=environment,
            push_timeout_seconds=push_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        _log_sync_failure(
            "git.push_failed",
            failure_notifier=failure_notifier,
            reason="timeout",
            repo=repo_root,
        )
        return False

    if second_push_result.returncode == 0:
        return True

    push_error = _push_error_text(second_push_result)
    _log_sync_failure(
        "git.push_failed",
        failure_notifier=failure_notifier,
        repo=repo_root,
        attempt="2/2",
        error=push_error[:1200],
    )
    if failure_looks_like_network_issue(
        output_text=push_error,
        error_markers=list(batch.policy.pull_offline_error_markers),
    ):
        return False

    _notify_git_sync_issue(
        repo_root=repo_root,
        summary_text="git push failed.",
        args=args,
        details_text=push_error,
        failure_notifier=failure_notifier,
    )
    return False


def _process_batch_unlocked(
    self,
    batch: _RepoBatch,
    args: ParsedArgs,
    failure_notifier: SyncFailureNotifier | None = None,
) -> bool:
    repo_root = batch.repo_root
    environment = batch.environment
    git_timeout_seconds = batch.git_timeout_seconds
    pull_timeout_seconds = batch.pull_timeout_seconds
    push_timeout_seconds = batch.push_timeout_seconds

    if not _ensure_merge_state_clean(
        self=self,
        repo_root=repo_root,
        environment=environment,
        git_timeout_seconds=git_timeout_seconds,
        autoresolve_mode=batch.policy.autoresolve_mode,
        args=args,
        failure_notifier=failure_notifier,
    ):
        return False

    staged_ok, porcelain_text, changed_paths = _stage_and_collect_changes(
        self=self,
        repo_root=repo_root,
        environment=environment,
        git_timeout_seconds=git_timeout_seconds,
        args=args,
        failure_notifier=failure_notifier,
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
        args=args,
        failure_notifier=failure_notifier,
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
        args=args,
        failure_notifier=failure_notifier,
    )
    if not pushed_ok:
        return False

    if not write_sync_success_timestamp(repo_root):
        logger.warning(log_record("git.sync_marker_write_failed", repo=repo_root))
    return True


def process_batch(
    self,
    batch: _RepoBatch,
    args: ParsedArgs,
    *,
    operating_system: OperatingSystem,
    failure_notifier: SyncFailureNotifier | None = None,
) -> bool:
    def _run_batch() -> bool:
        if failure_notifier is None:
            return _process_batch_unlocked(self, batch, args)
        return _process_batch_unlocked(
            self,
            batch,
            args,
            failure_notifier=failure_notifier,
        )

    return _with_repo_process_lock(
        batch.repo_root,
        _run_batch,
        wait_timeout_seconds=max(
            0.0,
            args.require("sys-git-repo-lock-wait-timeout-seconds").value,
        ),
        retry_sleep_seconds=max(
            0.01,
            args.require("sys-git-repo-lock-retry-sleep-seconds").value,
        ),
        stale_seconds=max(
            0.0,
            args.require("sys-git-repo-lock-stale-seconds").value,
        ),
        operating_system=operating_system,
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
    args: ParsedArgs,
    operating_system: OperatingSystem,
) -> DirtyTreeCommitResult:
    batch = make_repo_batch(
        repo_root=repo_root,
        event_type=event_type,
        paths=paths,
        args=args,
        environment=git_environment(),
    )
    pending_failure = _PendingSyncFailure()

    def _error_text(fallback_text: str) -> str:
        if not pending_failure.exists:
            return fallback_text
        if pending_failure.details_text:
            return (
                f"{pending_failure.summary_text}\n{pending_failure.details_text[:1200]}"
            )
        return pending_failure.summary_text

    def _run_commit() -> DirtyTreeCommitResult:
        pending_failure.clear()
        if not _ensure_merge_state_clean(
            self=self,
            repo_root=repo_root,
            environment=batch.environment,
            git_timeout_seconds=batch.git_timeout_seconds,
            autoresolve_mode=batch.policy.autoresolve_mode,
            args=args,
            failure_notifier=pending_failure.record,
        ):
            return DirtyTreeCommitResult(
                status="error",
                repo_root=repo_root,
                error_text=_error_text("merge state is not clean"),
            )

        staged_ok, porcelain_text, changed_paths = _stage_and_collect_changes(
            self=self,
            repo_root=repo_root,
            environment=batch.environment,
            git_timeout_seconds=batch.git_timeout_seconds,
            args=args,
            failure_notifier=pending_failure.record,
        )
        if not staged_ok:
            return DirtyTreeCommitResult(
                status="error",
                repo_root=repo_root,
                error_text=_error_text("failed to stage changes"),
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
            args=args,
            failure_notifier=pending_failure.record,
        )
        if not committed_ok:
            return DirtyTreeCommitResult(
                status="error",
                repo_root=repo_root,
                error_text=_error_text("git commit failed"),
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
        wait_timeout_seconds=max(
            0.0,
            args.require("sys-git-repo-lock-wait-timeout-seconds").value,
        ),
        retry_sleep_seconds=max(
            0.01,
            args.require("sys-git-repo-lock-retry-sleep-seconds").value,
        ),
        stale_seconds=max(
            0.0,
            args.require("sys-git-repo-lock-stale-seconds").value,
        ),
        operating_system=operating_system,
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
    args: ParsedArgs,
    operating_system: OperatingSystem,
) -> PatchPacketBuildResult:
    batch = make_repo_batch(
        repo_root=repo_root,
        event_type="modified",
        paths=changed_paths,
        args=args,
        environment=git_environment(),
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
        wait_timeout_seconds=max(
            0.0,
            args.require("sys-git-repo-lock-wait-timeout-seconds").value,
        ),
        retry_sleep_seconds=max(
            0.01,
            args.require("sys-git-repo-lock-retry-sleep-seconds").value,
        ),
        stale_seconds=max(
            0.0,
            args.require("sys-git-repo-lock-stale-seconds").value,
        ),
        operating_system=operating_system,
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
