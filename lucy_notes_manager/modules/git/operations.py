from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from lucy_notes_manager.lib import safe_notify
from lucy_notes_manager.modules.git.executor import GitExecutor, combined_output
from lucy_notes_manager.modules.git.helpers import union_resolve_text
from lucy_notes_manager.modules.git.helpers import failure_looks_like_network_issue
from lucy_notes_manager.modules.git.ops import command_ops, conflict_ops, network_ops

logger = logging.getLogger(__name__)
_MIN_STALE_INDEX_LOCK_AGE_SECONDS = 60.0
_RECENT_INDEX_LOCK_RETRY_MAX_ATTEMPTS = 15
_RECENT_INDEX_LOCK_RETRY_SLEEP_SECONDS = 1.0


def _index_lock_path(repo_root: str) -> str:
    return command_ops.index_lock_path(repo_root)


def failure_is_index_lock(error_text: str) -> bool:
    return command_ops.failure_is_index_lock(error_text)


def clear_stale_index_lock(repo_root: str) -> bool:
    return command_ops.clear_stale_index_lock(
        repo_root,
        min_stale_age_seconds=_MIN_STALE_INDEX_LOCK_AGE_SECONDS,
        logger=logger,
    )


def _index_lock_age_seconds(repo_root: str) -> float | None:
    return command_ops.index_lock_age_seconds(repo_root, logger=logger)


def _resolve_address_infos(
    host_name: str,
    port_number: int,
    timeout_seconds: float,
) -> tuple[list[tuple], bool]:
    return network_ops.resolve_address_infos(
        host_name=host_name,
        port_number=port_number,
        timeout_seconds=timeout_seconds,
    )


def abort_merge_safely(
    self,
    repo_root: str,
    environment: Dict[str, str],
    timeout_seconds: float,
) -> bool:
    return conflict_ops.abort_merge_safely(
        self_obj=self,
        repo_root=repo_root,
        environment=environment,
        timeout_seconds=timeout_seconds,
        run_git_fn=run_git,
        combined_output_fn=combined_output,
        logger=logger,
    )


def git_environment(self, config: dict) -> Dict[str, str]:
    _ = self
    _ = config
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
        failure_is_index_lock_fn=failure_is_index_lock,
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


def parse_remote_endpoint(remote_url_value: str) -> tuple[Optional[str], Optional[int]]:
    return network_ops.parse_remote_endpoint(remote_url_value)


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
        parse_remote_endpoint_fn=parse_remote_endpoint,
        resolve_address_infos_fn=_resolve_address_infos,
        logger=logger,
    )


def pull_failure_looks_offline(
    output_text: str,
    offline_error_markers: list[str] | None = None,
) -> bool:
    return failure_looks_like_network_issue(
        output_text=output_text,
        error_markers=offline_error_markers,
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
    autoresolve_mode: str,
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
    )


def resolve_merge_conflicts_with_fallback(
    self,
    repo_root: str,
    environment: Dict[str, str],
    timeout_seconds: float,
    autoresolve_mode: str,
) -> bool:
    return conflict_ops.resolve_merge_conflicts_with_fallback(
        self_obj=self,
        repo_root=repo_root,
        environment=environment,
        timeout_seconds=timeout_seconds,
        autoresolve_mode=autoresolve_mode,
        auto_resolve_fn=auto_resolve_merge_conflicts,
        logger=logger,
    )


@dataclass(frozen=True)
class _PullPlan:
    command: list[str]
    command_for_notification: str
    remote_name: Optional[str]


def _notify_pull_waiting_for_network(
    repo_root: str,
    remote_name: Optional[str],
    reason: str,
    notify_config: Mapping[str, Any],
) -> None:
    remote_label = remote_name or "unknown"
    safe_notify(
        name=f"git-network:{repo_root}",
        message=(
            f"Repository:\n{repo_root}\n\n"
            f"Remote:\n{remote_label}\n\n"
            f"Git sync waiting for network.\n\n"
            f"Reason:\n{reason[:600]}"
        ),
        config=notify_config,
        use_rare_mode=True,
    )


def _remote_is_reachable_or_wait(
    self,
    repo_root: str,
    remote_name: str,
    environment: Dict[str, str],
    operation_timeout_seconds: float,
    network_probe_timeout_seconds: float,
) -> bool:
    return remote_is_reachable(
        self=self,
        repo_root=repo_root,
        remote_name=remote_name,
        environment=environment,
        timeout_seconds=operation_timeout_seconds,
        network_probe_timeout_seconds=network_probe_timeout_seconds,
    )


def _resolve_pull_plan(
    self,
    repo_root: str,
    environment: Dict[str, str],
    pull_timeout_seconds: float,
    operation_timeout_seconds: float,
    auto_set_upstream: bool,
    network_probe_timeout_seconds: float,
    notify_config: Mapping[str, Any],
) -> Optional[_PullPlan]:
    if has_upstream(self, repo_root, environment, operation_timeout_seconds):
        remote_name = upstream_remote_name(
            self, repo_root, environment, operation_timeout_seconds
        )
        if remote_name and not _remote_is_reachable_or_wait(
            self=self,
            repo_root=repo_root,
            remote_name=remote_name,
            environment=environment,
            operation_timeout_seconds=operation_timeout_seconds,
            network_probe_timeout_seconds=network_probe_timeout_seconds,
        ):
            _notify_pull_waiting_for_network(
                repo_root=repo_root,
                remote_name=remote_name,
                reason="Remote endpoint is unreachable.",
                notify_config=notify_config,
            )
            return None
        return _PullPlan(
            command=["pull", "--no-rebase", "--no-edit"],
            command_for_notification="git pull --no-rebase",
            remote_name=remote_name,
        )

    branch_name = current_branch(
        self, repo_root, environment, operation_timeout_seconds
    )
    remote_name = pick_remote(self, repo_root, environment, operation_timeout_seconds)
    if not branch_name or not remote_name:
        logger.warning(
            "no upstream and cannot infer remote/branch; skip auto-pull | repo=%s",
            repo_root,
        )
        safe_notify(
            name=f"pull-noupstream:{repo_root}",
            message=(
                f"Repository:\n{repo_root}\n\n"
                f"No upstream configured and cannot infer remote/branch; skip pull."
            ),
            config=notify_config,
            use_rare_mode=True,
        )
        return None

    if not _remote_is_reachable_or_wait(
        self=self,
        repo_root=repo_root,
        remote_name=remote_name,
        environment=environment,
        operation_timeout_seconds=operation_timeout_seconds,
        network_probe_timeout_seconds=network_probe_timeout_seconds,
    ):
        _notify_pull_waiting_for_network(
            repo_root=repo_root,
            remote_name=remote_name,
            reason="Remote endpoint is unreachable.",
            notify_config=notify_config,
        )
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
            "no upstream and remote branch missing; skip pull | repo=%s | remote=%s | branch=%s",
            repo_root,
            remote_name,
            branch_name,
        )
        safe_notify(
            name=f"pull-noremotebranch:{repo_root}",
            message=(
                f"Repository:\n{repo_root}\n\n"
                f"No upstream configured and remote branch does not exist:\n"
                f"{remote_name}/{branch_name}\n\n"
                f"Skip pull."
            ),
            config=notify_config,
            use_rare_mode=True,
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
        command_for_notification=f"git pull --no-rebase {remote_name} {branch_name}",
        remote_name=remote_name,
    )


def _handle_pull_timeout(
    self,
    repo_root: str,
    environment: Dict[str, str],
    operation_timeout_seconds: float,
    network_probe_timeout_seconds: float,
    remote_name: Optional[str],
    notify_config: Mapping[str, Any],
) -> bool:
    if remote_name and not _remote_is_reachable_or_wait(
        self=self,
        repo_root=repo_root,
        remote_name=remote_name,
        environment=environment,
        operation_timeout_seconds=operation_timeout_seconds,
        network_probe_timeout_seconds=network_probe_timeout_seconds,
    ):
        logger.info(
            "git pull timed out while network is offline; waiting for network | repo=%s",
            repo_root,
        )
        _notify_pull_waiting_for_network(
            repo_root=repo_root,
            remote_name=remote_name,
            reason="git pull timed out and remote looks offline.",
            notify_config=notify_config,
        )
        return False

    logger.error("git pull timed out | repo=%s", repo_root)
    _notify_pull_waiting_for_network(
        repo_root=repo_root,
        remote_name=remote_name,
        reason="git pull timed out.",
        notify_config=notify_config,
    )
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
    autoresolve_mode: str,
    pull_result: subprocess.CompletedProcess[str],
    pull_offline_error_markers: list[str] | None,
    command_for_notification: str,
    notify_config: Mapping[str, Any],
) -> bool:
    if merge_in_progress(self, repo_root, environment, operation_timeout_seconds):
        resolved = resolve_merge_conflicts_with_fallback(
            self,
            repo_root,
            environment,
            operation_timeout_seconds,
            autoresolve_mode=autoresolve_mode,
        )
        if resolved:
            return True

        pull_error = _pull_error_text(pull_result)
        abort_ok = abort_merge_safely(
            self=self,
            repo_root=repo_root,
            environment=environment,
            timeout_seconds=operation_timeout_seconds,
        )
        abort_suffix = "" if abort_ok else " | merge abort failed"
        logger.error(
            "git pull conflict; auto-resolve failed; merge aborted%s | repo=%s | error=%s",
            abort_suffix,
            repo_root,
            pull_error[:1200],
        )
        merge_abort_note = (
            ""
            if abort_ok
            else "\n\nMerge abort failed or timed out; manual cleanup may be required."
        )
        safe_notify(
            name=f"pull-conflict:{repo_root}",
            message=(
                f"Repository:\n{repo_root}\n\n"
                f"Auto-merge conflict resolution failed.\n"
                f"Merge aborted (no rebase / no force).\n\n"
                f"Error:\n{pull_error[:1200]}"
                f"{merge_abort_note}"
            ),
            config=notify_config,
            use_rare_mode=True,
        )
        return False

    pull_error = _pull_error_text(pull_result)
    if pull_failure_looks_offline(
        pull_error,
        pull_offline_error_markers,
    ):
        logger.info(
            "git pull failed because network is offline; waiting for network | repo=%s",
            repo_root,
        )
        _notify_pull_waiting_for_network(
            repo_root=repo_root,
            remote_name=None,
            reason=f"git pull failed with offline marker.\n{pull_error[:600]}",
            notify_config=notify_config,
        )
        return False

    logger.error("git pull failed | repo=%s | error=%s", repo_root, pull_error[:1200])
    safe_notify(
        name=f"pullfail:{repo_root}",
        message=(
            f"Repository:\n{repo_root}\n\n"
            f"Command:\n{command_for_notification}\n\n"
            f"Error:\n{pull_error[:1200]}"
        ),
        config=notify_config,
        use_rare_mode=True,
    )
    return False


def safe_pull_merge(
    self,
    repo_root: str,
    environment: Dict[str, str],
    pull_timeout_seconds: float,
    operation_timeout_seconds: float,
    autoresolve_mode: str,
    notify_config: Mapping[str, Any],
    auto_set_upstream: bool = True,
    network_probe_timeout_seconds: float = 0.0,
    pull_offline_error_markers: list[str] | None = None,
) -> bool:
    pull_plan = _resolve_pull_plan(
        self=self,
        repo_root=repo_root,
        environment=environment,
        pull_timeout_seconds=pull_timeout_seconds,
        operation_timeout_seconds=operation_timeout_seconds,
        auto_set_upstream=auto_set_upstream,
        network_probe_timeout_seconds=network_probe_timeout_seconds,
        notify_config=notify_config,
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
            notify_config=notify_config,
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
        command_for_notification=pull_plan.command_for_notification,
        notify_config=notify_config,
    )
