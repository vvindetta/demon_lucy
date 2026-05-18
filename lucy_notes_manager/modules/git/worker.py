from __future__ import annotations

import logging
import subprocess
import time
from queue import Empty
from typing import Any

from lucy_notes_manager.lib import safe_notify
from lucy_notes_manager.modules.git.helpers import (
    parse_porcelain_paths,
    push_rejected_needs_pull,
)
from lucy_notes_manager.modules.git.batch_factory import (
    add_event_to_batch,
    make_repo_batch,
    refresh_repo_batch_from_snapshot,
)
from lucy_notes_manager.modules.git.operations import (
    auto_resolve_merge_conflicts,
    git_environment,
    merge_in_progress,
    run_git,
    safe_pull_merge,
)
from lucy_notes_manager.modules.git.scheduler import (
    collect_due_periodic_pull_events,
    should_force_flush_batch,
    update_periodic_pull_state,
)
from lucy_notes_manager.modules.git.types import _RepoBatch

logger = logging.getLogger(__name__)
_PULL_ONLY_EVENT_TYPES = {"opened", "scheduled_pull"}


def _notify_config_from_batch(batch: _RepoBatch) -> dict[str, Any]:
    return {
        "sys_notification_provider": batch.notify_provider,
        "sys_notification_min_interval_seconds": batch.notify_min_interval_sec,
    }


def _enqueue_oneshot(
    self,
    repo_root: str,
    event_type: str,
    paths: list[str],
    config_snapshot: dict,
    wants_pull: bool,
) -> None:
    environment = git_environment(self, config_snapshot)
    batch = make_repo_batch(
        repo_root=repo_root,
        config_snapshot=config_snapshot,
        environment=environment,
        wants_pull=bool(wants_pull),
        debounce_seconds_override=0.0,
        pull_cooldown_min_seconds_override=0.0,
        max_batch_seconds_override=0.0,
    )
    add_event_to_batch(
        batch=batch,
        event_type=event_type,
        paths=paths,
        wants_pull=bool(wants_pull),
    )
    process_batch(self, batch)


def enqueue(
    self,
    repo_root: str,
    event_type: str,
    paths: list[str],
    config_snapshot: dict,
    wants_pull: bool,
) -> None:
    if getattr(self, "_oneshot_mode", False):
        _enqueue_oneshot(
            self=self,
            repo_root=repo_root,
            event_type=event_type,
            paths=paths,
            config_snapshot=config_snapshot,
            wants_pull=wants_pull,
        )
        return
    self._event_queue.put((repo_root, event_type, paths, dict(config_snapshot), wants_pull))


def _create_or_get_batch(
    self,
    repo_root: str,
    config_snapshot: dict,
    environment: dict[str, str],
    wants_pull: bool,
) -> _RepoBatch:
    existing_batch = self._pending_batches.get(repo_root)
    if existing_batch:
        return existing_batch

    existing_batch = make_repo_batch(
        repo_root=repo_root,
        config_snapshot=config_snapshot,
        environment=environment,
        wants_pull=wants_pull,
    )
    self._pending_batches[repo_root] = existing_batch
    return existing_batch


def _apply_config_snapshot_to_batch(
    batch: _RepoBatch,
    config_snapshot: dict,
    environment: dict[str, str],
    wants_pull: bool,
    event_type: str,
    paths: list[str],
    now_timestamp: float,
) -> None:
    refresh_repo_batch_from_snapshot(
        batch=batch,
        config_snapshot=config_snapshot,
        environment=environment,
    )
    add_event_to_batch(
        batch=batch,
        event_type=event_type,
        paths=paths,
        wants_pull=wants_pull,
        now_timestamp=now_timestamp,
    )


def worker_loop(self) -> None:
    while True:
        try:
            repo_root, event_type, paths, config_snapshot, wants_pull = self._event_queue.get(
                timeout=0.2
            )
            now_timestamp = time.time()
            environment = git_environment(self, config_snapshot)

            with self._pending_lock:
                update_periodic_pull_state(
                    self,
                    repo_root=repo_root,
                    config_snapshot=config_snapshot,
                    now_timestamp=now_timestamp,
                )
                batch = _create_or_get_batch(
                    self=self,
                    repo_root=repo_root,
                    config_snapshot=config_snapshot,
                    environment=environment,
                    wants_pull=wants_pull,
                )
                _apply_config_snapshot_to_batch(
                    batch=batch,
                    config_snapshot=config_snapshot,
                    environment=environment,
                    wants_pull=wants_pull,
                    event_type=event_type,
                    paths=paths,
                    now_timestamp=now_timestamp,
                )
        except Empty:
            pass

        current_timestamp = time.time()
        due_batches: list[_RepoBatch] = []
        periodic_pull_events: list[tuple[str, str, list[str], dict, bool]] = []
        with self._pending_lock:
            for repo_root_key, batch in list(self._pending_batches.items()):
                quiet_due = current_timestamp - batch.last_event_at >= batch.debounce_seconds
                forced_due = should_force_flush_batch(batch, current_timestamp)
                if quiet_due or forced_due:
                    due_batches.append(batch)
                    del self._pending_batches[repo_root_key]
            periodic_pull_events = collect_due_periodic_pull_events(
                self,
                now_timestamp=current_timestamp,
            )

        _process_due_batches(self, due_batches)
        for event in periodic_pull_events:
            self._event_queue.put(event)


def _process_due_batches(self, due_batches: list[_RepoBatch]) -> None:
    for batch in due_batches:
        try:
            process_batch(self, batch)
        except Exception:
            logger.exception(
                "process batch crashed; continuing worker loop | repo=%s",
                batch.repo_root,
            )
            safe_notify(
                name=f"batch-crash:{batch.repo_root}",
                message=(
                    f"Repository:\n{batch.repo_root}\n\n"
                    "Git batch processing crashed. Worker loop is still running."
                ),
                config=_notify_config_from_batch(batch),
            )


def _abort_unfinished_merge(
    self,
    repo_root: str,
    environment: dict[str, str],
    git_timeout_seconds: float,
) -> bool:
    try:
        abort_result = run_git(
            self,
            repo_root,
            ["merge", "--abort"],
            environment,
            timeout_seconds=git_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.error("git merge --abort timed out | repo=%s", repo_root)
        return False
    except Exception:
        logger.exception("git merge --abort crashed | repo=%s", repo_root)
        return False

    if abort_result.returncode == 0:
        return True

    abort_error = (abort_result.stderr or abort_result.stdout or "git merge --abort failed").strip()
    logger.error(
        "git merge --abort failed | repo=%s | error=%s",
        repo_root,
        abort_error[:1200],
    )
    return False


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

    resolved = auto_resolve_merge_conflicts(
        self,
        repo_root,
        environment,
        git_timeout_seconds,
        autoresolve_mode=autoresolve_mode,
    )
    if resolved:
        return True

    abort_ok = _abort_unfinished_merge(
        self=self,
        repo_root=repo_root,
        environment=environment,
        git_timeout_seconds=git_timeout_seconds,
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
    )
    return False


def _handle_pull_only_batch(
    self,
    batch: _RepoBatch,
    repo_root: str,
    environment: dict[str, str],
    pull_timeout_seconds: float,
    git_timeout_seconds: float,
) -> bool:
    pull_only_batch = batch.event_types and batch.event_types.issubset(
        _PULL_ONLY_EVENT_TYPES
    )
    if not pull_only_batch or not batch.wants_pull:
        return False

    if not self._pull_allowed_with_progression(
        repo_root=repo_root,
        cooldown_min_seconds=batch.pull_cooldown_min_seconds,
        cooldown_max_seconds=batch.pull_cooldown_max_seconds,
    ):
        return True

    safe_pull_merge(
        self,
        repo_root,
        environment,
        pull_timeout_seconds=pull_timeout_seconds,
        operation_timeout_seconds=git_timeout_seconds,
        autoresolve_mode=batch.autoresolve_mode,
        notify_config=_notify_config_from_batch(batch),
        auto_set_upstream=batch.auto_set_upstream,
        network_probe_timeout_seconds=batch.network_probe_timeout_seconds,
        pull_offline_error_markers=batch.pull_offline_error_markers,
    )
    return True


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
        )
        return False, "", []

    if add_result.returncode != 0:
        add_error = (add_result.stderr or add_result.stdout or "git add failed").strip()
        logger.error("git add failed | repo=%s | error=%s", repo_root, add_error[:1200])
        safe_notify(
            name=f"addfail:{repo_root}",
            message=f"Repository:\n{repo_root}\n\nError:\n{add_error[:1200]}",
            config=notify_config,
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
        )
        return False, "", []

    if status_result.returncode != 0:
        status_error = (status_result.stderr or status_result.stdout or "git status failed").strip()
        logger.error("git status failed | repo=%s | error=%s", repo_root, status_error[:1200])
        safe_notify(
            name=f"statusfail:{repo_root}",
            message=f"Repository:\n{repo_root}\n\nError:\n{status_error[:1200]}",
            config=notify_config,
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
    )
    return False


def _maybe_pull_before_push(
    self,
    batch: _RepoBatch,
    repo_root: str,
    environment: dict[str, str],
    pull_timeout_seconds: float,
    git_timeout_seconds: float,
) -> None:
    if not batch.wants_pull:
        return
    if not self._pull_allowed_with_progression(
        repo_root=repo_root,
        cooldown_min_seconds=batch.pull_cooldown_min_seconds,
        cooldown_max_seconds=batch.pull_cooldown_max_seconds,
    ):
        return

    safe_pull_merge(
        self,
        repo_root,
        environment,
        pull_timeout_seconds=pull_timeout_seconds,
        operation_timeout_seconds=git_timeout_seconds,
        autoresolve_mode=batch.autoresolve_mode,
        notify_config=_notify_config_from_batch(batch),
        auto_set_upstream=batch.auto_set_upstream,
        network_probe_timeout_seconds=batch.network_probe_timeout_seconds,
        pull_offline_error_markers=batch.pull_offline_error_markers,
    )


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


def _handle_push_timeout(
    self,
    repo_root: str,
    backoff_start_seconds: float,
    backoff_max_seconds: float,
    notify_config: dict[str, Any],
) -> None:
    self._register_push_failure(repo_root, backoff_start_seconds, backoff_max_seconds)
    logger.error("git push timed out | repo=%s", repo_root)
    safe_notify(
        name=f"timeout:push:{repo_root}",
        message=f"git push timed out:\n{repo_root}",
        config=notify_config,
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
    backoff_start_seconds: float,
    backoff_max_seconds: float,
    notify_config: dict[str, Any],
) -> None:
    def _reset_push_backoff() -> None:
        self._push_backoff_seconds[repo_root] = backoff_start_seconds
        self._push_next_allowed_at[repo_root] = 0.0

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
        _reset_push_backoff()
        return

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
            batch.auto_merge_on_push and push_rejected_needs_pull(combined_push_output)
        )

    if should_pull_before_retry:
        pulled = safe_pull_merge(
            self,
            repo_root,
            environment,
            pull_timeout_seconds=pull_timeout_seconds,
            operation_timeout_seconds=git_timeout_seconds,
            autoresolve_mode=batch.autoresolve_mode,
            notify_config=notify_config,
            auto_set_upstream=batch.auto_set_upstream,
            network_probe_timeout_seconds=batch.network_probe_timeout_seconds,
            pull_offline_error_markers=batch.pull_offline_error_markers,
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
        _handle_push_timeout(
            self=self,
            repo_root=repo_root,
            backoff_start_seconds=backoff_start_seconds,
            backoff_max_seconds=backoff_max_seconds,
            notify_config=notify_config,
        )
        return

    if second_push_result.returncode == 0:
        _reset_push_backoff()
        return

    self._register_push_failure(repo_root, backoff_start_seconds, backoff_max_seconds)
    push_error = _push_error_text(second_push_result)
    logger.error(
        "git push failed | repo=%s | attempt=2/2 | error=%s",
        repo_root,
        push_error[:1200],
    )
    safe_notify(
        name=f"pushfail:{repo_root}",
        message=(
            f"Repository:\n{repo_root}\n\n"
            f"Command:\ngit push\n\n"
            f"Error:\n{push_error[:1200]}"
        ),
        config=notify_config,
    )


def process_batch(self, batch: _RepoBatch) -> None:
    repo_root = batch.repo_root
    environment = batch.environment
    git_timeout_seconds = batch.git_timeout_seconds
    pull_timeout_seconds = batch.pull_timeout_seconds
    push_timeout_seconds = batch.push_timeout_seconds
    backoff_start_seconds = batch.backoff_start_seconds
    backoff_max_seconds = batch.backoff_max_seconds
    notify_config = _notify_config_from_batch(batch)

    if not _ensure_merge_state_clean(
        self=self,
        repo_root=repo_root,
        environment=environment,
        git_timeout_seconds=git_timeout_seconds,
        autoresolve_mode=batch.autoresolve_mode,
        notify_config=notify_config,
    ):
        return

    if _handle_pull_only_batch(
        self=self,
        batch=batch,
        repo_root=repo_root,
        environment=environment,
        pull_timeout_seconds=pull_timeout_seconds,
        git_timeout_seconds=git_timeout_seconds,
    ):
        return

    staged_ok, porcelain_text, changed_paths = _stage_and_collect_changes(
        self=self,
        repo_root=repo_root,
        environment=environment,
        git_timeout_seconds=git_timeout_seconds,
        notify_config=notify_config,
    )
    if not staged_ok:
        return

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
        return

    _maybe_pull_before_push(
        self=self,
        batch=batch,
        repo_root=repo_root,
        environment=environment,
        pull_timeout_seconds=pull_timeout_seconds,
        git_timeout_seconds=git_timeout_seconds,
    )

    now_timestamp = time.time()
    next_allowed_timestamp = self._push_next_allowed_at.get(repo_root, 0.0)
    if now_timestamp < next_allowed_timestamp:
        return

    _attempt_push_with_retry(
        self=self,
        batch=batch,
        repo_root=repo_root,
        environment=environment,
        pull_timeout_seconds=pull_timeout_seconds,
        push_timeout_seconds=push_timeout_seconds,
        git_timeout_seconds=git_timeout_seconds,
        backoff_start_seconds=backoff_start_seconds,
        backoff_max_seconds=backoff_max_seconds,
        notify_config=notify_config,
    )
