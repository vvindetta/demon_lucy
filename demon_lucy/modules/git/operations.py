from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from demon_lucy.lib.args.models import ParsedArgs
from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.notifications import safe_notify
from demon_lucy.modules.git.executor import GitExecutor, combined_output
from demon_lucy.modules.git.helpers import (
    failure_looks_like_network_issue,
    union_resolve_text,
)
from demon_lucy.modules.git.ops import command_ops, conflict_ops, network_ops
from demon_lucy.modules.git.types import MergeAutoresolveMode

logger = logging.getLogger(__name__)
_MIN_STALE_INDEX_LOCK_AGE_SECONDS = 60.0
_RECENT_INDEX_LOCK_RETRY_MAX_ATTEMPTS = 15
_RECENT_INDEX_LOCK_RETRY_SLEEP_SECONDS = 1.0
SyncFailureNotifier = Callable[[str, str], None]


def clear_stale_index_lock(repo_root: str) -> bool:
    return command_ops.clear_stale_index_lock(
        repo_root,
        min_stale_age_seconds=_MIN_STALE_INDEX_LOCK_AGE_SECONDS,
        logger=logger,
    )


def _index_lock_age_seconds(repo_root: str) -> float | None:
    return command_ops.index_lock_age_seconds(repo_root, logger=logger)


def abort_merge_safely(
    self,
    repo_root: str,
    environment: Dict[str, str],
    timeout_seconds: float,
    failure_is_deferred: bool = False,
) -> bool:
    return conflict_ops.abort_merge_safely(
        self_obj=self,
        repo_root=repo_root,
        environment=environment,
        timeout_seconds=timeout_seconds,
        run_git_fn=run_git,
        combined_output_fn=combined_output,
        logger=logger,
        failure_is_deferred=failure_is_deferred,
    )


def git_environment() -> Dict[str, str]:
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["LC_ALL"] = "C"
    environment["LANG"] = "C"
    environment["LANGUAGE"] = "C"
    return environment


def run_git(
    self,
    repo_root: str,
    arguments: list[str],
    environment: Dict[str, str],
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    _ = self
    return command_ops.run_git(
        repo_root=repo_root,
        arguments=arguments,
        environment=environment,
        timeout_seconds=timeout_seconds,
        executor_factory=lambda root, env: GitExecutor(repo_root=root, environment=env),
        output_getter=combined_output,
        failure_is_index_lock_fn=command_ops.failure_is_index_lock,
        clear_stale_index_lock_fn=clear_stale_index_lock,
        index_lock_age_seconds_fn=_index_lock_age_seconds,
        recent_retry_max_attempts=_RECENT_INDEX_LOCK_RETRY_MAX_ATTEMPTS,
        recent_retry_sleep_seconds=_RECENT_INDEX_LOCK_RETRY_SLEEP_SECONDS,
        logger=logger,
    )


def has_upstream(
    self, repo_root: str, environment: Dict[str, str], timeout_seconds: float
) -> bool:
    result = run_git(
        self,
        repo_root,
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        environment,
        timeout_seconds,
    )
    return result.returncode == 0 and bool((result.stdout or "").strip())


def current_branch(
    self, repo_root: str, environment: Dict[str, str], timeout_seconds: float
) -> Optional[str]:
    result = run_git(
        self,
        repo_root,
        ["rev-parse", "--abbrev-ref", "HEAD"],
        environment,
        timeout_seconds,
    )
    branch_name = (result.stdout or "").strip()
    if result.returncode != 0 or not branch_name or branch_name == "HEAD":
        return None
    return branch_name


def pick_remote(
    self, repo_root: str, environment: Dict[str, str], timeout_seconds: float
) -> Optional[str]:
    result = run_git(self, repo_root, ["remote"], environment, timeout_seconds)
    if result.returncode != 0:
        return None
    remote_names = [
        line_text.strip()
        for line_text in (result.stdout or "").splitlines()
        if line_text.strip()
    ]
    if not remote_names:
        return None
    if "origin" in remote_names:
        return "origin"
    return remote_names[0]


def upstream_remote_name(
    self, repo_root: str, environment: Dict[str, str], timeout_seconds: float
) -> Optional[str]:
    result = run_git(
        self,
        repo_root,
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        environment,
        timeout_seconds,
    )
    if result.returncode != 0:
        return None

    upstream_ref = (result.stdout or "").strip()
    if not upstream_ref or "/" not in upstream_ref:
        return None
    return upstream_ref.split("/", 1)[0]


def remote_url(
    self,
    repo_root: str,
    remote_name: str,
    environment: Dict[str, str],
    timeout_seconds: float,
) -> Optional[str]:
    result = run_git(
        self,
        repo_root,
        ["remote", "get-url", remote_name],
        environment,
        timeout_seconds,
    )
    if result.returncode != 0:
        return None

    remote_url_value = (result.stdout or "").strip()
    if not remote_url_value:
        return None
    return remote_url_value


def remote_is_reachable(
    self,
    repo_root: str,
    remote_name: str,
    environment: Dict[str, str],
    timeout_seconds: float,
    network_probe_timeout_seconds: float = 0.0,
) -> bool:
    return network_ops.remote_is_reachable(
        repo_root=repo_root,
        remote_name=remote_name,
        timeout_seconds=timeout_seconds,
        network_probe_timeout_seconds=network_probe_timeout_seconds,
        remote_url_getter=lambda: remote_url(
            self,
            repo_root,
            remote_name,
            environment,
            timeout_seconds,
        ),
        parse_remote_endpoint_fn=network_ops.parse_remote_endpoint,
        resolve_address_infos_fn=network_ops.resolve_address_infos,
        logger=logger,
    )


def remote_branch_exists(
    self,
    repo_root: str,
    remote_name: str,
    branch_name: str,
    environment: Dict[str, str],
    timeout_seconds: float,
) -> bool:
    result = run_git(
        self,
        repo_root,
        ["ls-remote", "--heads", remote_name, branch_name],
        environment,
        timeout_seconds,
    )
    return result.returncode == 0 and bool((result.stdout or "").strip())


def try_set_upstream(
    self,
    repo_root: str,
    remote_name: str,
    branch_name: str,
    environment: Dict[str, str],
    timeout_seconds: float,
) -> bool:
    result = run_git(
        self,
        repo_root,
        [
            "branch",
            "--set-upstream-to",
            f"{remote_name}/{branch_name}",
            branch_name,
        ],
        environment,
        timeout_seconds,
    )
    return result.returncode == 0


def merge_in_progress(
    self, repo_root: str, environment: Dict[str, str], timeout_seconds: float
) -> bool:
    return conflict_ops.merge_in_progress(
        self_obj=self,
        repo_root=repo_root,
        environment=environment,
        timeout_seconds=timeout_seconds,
        run_git_fn=run_git,
    )


def conflicted_files(
    self, repo_root: str, environment: Dict[str, str], timeout_seconds: float
) -> list[str]:
    return conflict_ops.conflicted_files(
        self_obj=self,
        repo_root=repo_root,
        environment=environment,
        timeout_seconds=timeout_seconds,
        run_git_fn=run_git,
    )


def auto_resolve_merge_conflicts(
    self,
    repo_root: str,
    environment: Dict[str, str],
    timeout_seconds: float,
    autoresolve_mode: MergeAutoresolveMode,
    failure_is_deferred: bool = False,
) -> bool:
    return conflict_ops.auto_resolve_merge_conflicts(
        self_obj=self,
        repo_root=repo_root,
        environment=environment,
        timeout_seconds=timeout_seconds,
        autoresolve_mode=autoresolve_mode,
        run_git_fn=run_git,
        union_resolve_text_fn=union_resolve_text,
        logger=logger,
        failure_is_deferred=failure_is_deferred,
    )


def resolve_merge_conflicts_with_fallback(
    self,
    repo_root: str,
    environment: Dict[str, str],
    timeout_seconds: float,
    autoresolve_mode: MergeAutoresolveMode,
    failure_is_deferred: bool = False,
) -> bool:
    def _auto_resolve(
        self_obj,
        root: str,
        git_environment_value: Dict[str, str],
        command_timeout_seconds: float,
        autoresolve_mode: MergeAutoresolveMode,
    ) -> bool:
        if not failure_is_deferred:
            return auto_resolve_merge_conflicts(
                self_obj,
                root,
                git_environment_value,
                command_timeout_seconds,
                autoresolve_mode,
            )
        return auto_resolve_merge_conflicts(
            self_obj,
            root,
            git_environment_value,
            command_timeout_seconds,
            autoresolve_mode,
            failure_is_deferred=failure_is_deferred,
        )

    return conflict_ops.resolve_merge_conflicts_with_fallback(
        self_obj=self,
        repo_root=repo_root,
        environment=environment,
        timeout_seconds=timeout_seconds,
        autoresolve_mode=autoresolve_mode,
        auto_resolve_fn=_auto_resolve,
        logger=logger,
    )


@dataclass(frozen=True)
class _PullPlan:
    command: list[str]
    remote_name: Optional[str]


def _resolve_pull_plan(
    self,
    repo_root: str,
    environment: Dict[str, str],
    pull_timeout_seconds: float,
    operation_timeout_seconds: float,
    auto_set_upstream: bool,
    network_probe_timeout_seconds: float,
    remote_already_reached: bool,
) -> Optional[_PullPlan]:
    if has_upstream(self, repo_root, environment, operation_timeout_seconds):
        remote_name = upstream_remote_name(
            self, repo_root, environment, operation_timeout_seconds
        )
        if (
            remote_name
            and not remote_already_reached
            and not remote_is_reachable(
                self=self,
                repo_root=repo_root,
                remote_name=remote_name,
                environment=environment,
                timeout_seconds=operation_timeout_seconds,
                network_probe_timeout_seconds=network_probe_timeout_seconds,
            )
        ):
            return None
        return _PullPlan(
            command=["pull", "--no-rebase", "--no-edit"],
            remote_name=remote_name,
        )

    branch_name = current_branch(
        self, repo_root, environment, operation_timeout_seconds
    )
    remote_name = pick_remote(self, repo_root, environment, operation_timeout_seconds)
    if not branch_name or not remote_name:
        logger.warning(
            log_record(
                "git.pull_skip",
                reason="missing_upstream_and_remote",
                repo=repo_root,
            )
        )
        return None

    if not remote_already_reached and not remote_is_reachable(
        self=self,
        repo_root=repo_root,
        remote_name=remote_name,
        environment=environment,
        timeout_seconds=operation_timeout_seconds,
        network_probe_timeout_seconds=network_probe_timeout_seconds,
    ):
        return None

    remote_branch_exists_value = remote_branch_exists(
        self,
        repo_root,
        remote_name,
        branch_name,
        environment,
        timeout_seconds=pull_timeout_seconds,
    )
    if not remote_branch_exists_value:
        logger.warning(
            log_record(
                "git.pull_skip",
                reason="remote_branch_missing",
                repo=repo_root,
                remote=remote_name,
                branch=branch_name,
            )
        )
        return None

    if auto_set_upstream:
        try_set_upstream(
            self,
            repo_root,
            remote_name,
            branch_name,
            environment,
            timeout_seconds=operation_timeout_seconds,
        )

    return _PullPlan(
        command=["pull", "--no-rebase", "--no-edit", remote_name, branch_name],
        remote_name=remote_name,
    )


def _handle_pull_timeout(
    self,
    repo_root: str,
    environment: Dict[str, str],
    operation_timeout_seconds: float,
    network_probe_timeout_seconds: float,
    remote_name: Optional[str],
) -> bool:
    if remote_name and not remote_is_reachable(
        self=self,
        repo_root=repo_root,
        remote_name=remote_name,
        environment=environment,
        timeout_seconds=operation_timeout_seconds,
        network_probe_timeout_seconds=network_probe_timeout_seconds,
    ):
        logger.warning(
            log_record(
                "git.pull_wait_network",
                reason="timeout_remote_offline",
                repo=repo_root,
                remote=remote_name,
            )
        )
        return False

    logger.warning(log_record("git.pull_failed", reason="timeout", repo=repo_root))
    return False


def _pull_error_text(pull_result: subprocess.CompletedProcess[str]) -> str:
    error_text = combined_output(pull_result).strip()
    if not error_text:
        return "git pull failed"
    return error_text


def _handle_pull_failure(
    self,
    repo_root: str,
    environment: Dict[str, str],
    operation_timeout_seconds: float,
    autoresolve_mode: MergeAutoresolveMode,
    pull_result: subprocess.CompletedProcess[str],
    pull_offline_error_markers: list[str] | None,
    args: ParsedArgs,
    failure_notifier: SyncFailureNotifier | None,
) -> bool:
    if merge_in_progress(self, repo_root, environment, operation_timeout_seconds):
        resolved = resolve_merge_conflicts_with_fallback(
            self,
            repo_root,
            environment,
            operation_timeout_seconds,
            autoresolve_mode=autoresolve_mode,
            failure_is_deferred=failure_notifier is not None,
        )
        if resolved:
            return True

        pull_error = _pull_error_text(pull_result)
        abort_ok = abort_merge_safely(
            self=self,
            repo_root=repo_root,
            environment=environment,
            timeout_seconds=operation_timeout_seconds,
            failure_is_deferred=failure_notifier is not None,
        )
        log_method = logger.warning if failure_notifier is not None else logger.error
        log_method(
            log_record(
                "git.pull_conflict",
                autoresolve="failed",
                abort="failed" if not abort_ok else "done",
                repo=repo_root,
                error=pull_error[:1200],
            )
        )
        merge_abort_note = (
            ""
            if abort_ok
            else "\n\nMerge abort failed or timed out; manual cleanup may be required."
        )
        summary_text = (
            "Auto-merge conflict resolution failed; merge aborted "
            "(no rebase / no force)."
        )
        details_text = f"{pull_error[:1200]}{merge_abort_note}"
        if failure_notifier is not None:
            failure_notifier(summary_text, details_text)
        else:
            safe_notify(
                name=f"git-sync:{repo_root}",
                message=(
                    f"Repository:\n{repo_root}\n\n"
                    f"{summary_text}\n\n"
                    f"Error:\n{details_text}"
                ),
                args=args,
                use_rare_mode=True,
            )
        return False

    pull_error = _pull_error_text(pull_result)
    if failure_looks_like_network_issue(
        output_text=pull_error,
        error_markers=pull_offline_error_markers,
    ):
        logger.warning(
            log_record(
                "git.pull_wait_network",
                reason="offline_marker",
                repo=repo_root,
            )
        )
        return False

    log_method = logger.warning if failure_notifier is not None else logger.error
    log_method(log_record("git.pull_failed", repo=repo_root, error=pull_error[:1200]))
    summary_text = "git pull before push failed."
    if failure_notifier is not None:
        failure_notifier(summary_text, pull_error)
    else:
        safe_notify(
            name=f"git-sync:{repo_root}",
            message=(
                f"Repository:\n{repo_root}\n\n"
                f"{summary_text}\n\n"
                f"Error:\n{pull_error[:1200]}"
            ),
            args=args,
            use_rare_mode=True,
        )
    return False


def safe_pull_merge(
    self,
    repo_root: str,
    environment: Dict[str, str],
    pull_timeout_seconds: float,
    operation_timeout_seconds: float,
    autoresolve_mode: MergeAutoresolveMode,
    args: ParsedArgs,
    auto_set_upstream: bool = True,
    network_probe_timeout_seconds: float = 0.0,
    pull_offline_error_markers: list[str] | None = None,
    remote_already_reached: bool = False,
    failure_notifier: SyncFailureNotifier | None = None,
) -> bool:
    pull_plan = _resolve_pull_plan(
        self=self,
        repo_root=repo_root,
        environment=environment,
        pull_timeout_seconds=pull_timeout_seconds,
        operation_timeout_seconds=operation_timeout_seconds,
        auto_set_upstream=auto_set_upstream,
        network_probe_timeout_seconds=network_probe_timeout_seconds,
        remote_already_reached=remote_already_reached,
    )
    if pull_plan is None:
        return False

    try:
        pull_result = run_git(
            self,
            repo_root,
            pull_plan.command,
            environment,
            timeout_seconds=pull_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return _handle_pull_timeout(
            self=self,
            repo_root=repo_root,
            environment=environment,
            operation_timeout_seconds=operation_timeout_seconds,
            network_probe_timeout_seconds=network_probe_timeout_seconds,
            remote_name=pull_plan.remote_name,
        )

    if pull_result.returncode == 0:
        return True

    return _handle_pull_failure(
        self=self,
        repo_root=repo_root,
        environment=environment,
        operation_timeout_seconds=operation_timeout_seconds,
        autoresolve_mode=autoresolve_mode,
        pull_result=pull_result,
        pull_offline_error_markers=pull_offline_error_markers,
        args=args,
        failure_notifier=failure_notifier,
    )
