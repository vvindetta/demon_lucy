from __future__ import annotations

import logging
import os
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlparse

from lucy_notes_manager.lib import safe_notify
from lucy_notes_manager.modules.git.executor import GitExecutor, combined_output
from lucy_notes_manager.modules.git.helpers import union_resolve_text

logger = logging.getLogger(__name__)
_MIN_STALE_INDEX_LOCK_AGE_SECONDS = 60.0


def _index_lock_path(repo_root: str) -> str:
    return os.path.join(repo_root, ".git", "index.lock")


def failure_is_index_lock(error_text: str) -> bool:
    return "index.lock" in (error_text or "").lower()


def clear_stale_index_lock(repo_root: str) -> bool:
    lock_path = _index_lock_path(repo_root)

    try:
        lock_mtime_seconds = os.path.getmtime(lock_path)
    except FileNotFoundError:
        return False
    except OSError:
        logger.exception("failed to inspect git index.lock | repo=%s", repo_root)
        return False

    lock_age_seconds = time.time() - lock_mtime_seconds
    if lock_age_seconds < _MIN_STALE_INDEX_LOCK_AGE_SECONDS:
        logger.warning(
            "git index.lock is recent; skip auto-remove | repo=%s | age_seconds=%.1f",
            repo_root,
            lock_age_seconds,
        )
        return False

    try:
        os.remove(lock_path)
    except FileNotFoundError:
        return True
    except OSError:
        logger.exception("failed to remove stale git index.lock | repo=%s", repo_root)
        return False

    logger.warning(
        "removed stale git index.lock before retrying pull | repo=%s | age_seconds=%.1f",
        repo_root,
        lock_age_seconds,
    )
    return True


def _resolve_address_infos(
    host_name: str,
    port_number: int,
    timeout_seconds: float,
) -> tuple[list[tuple], bool]:
    resolver_result: dict[str, object] = {}
    resolver_done = threading.Event()

    def _resolve() -> None:
        try:
            resolver_result["address_infos"] = socket.getaddrinfo(
                host_name,
                port_number,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
        except OSError as exception:
            resolver_result["error"] = exception
        finally:
            resolver_done.set()

    resolver_thread = threading.Thread(target=_resolve, daemon=True)
    resolver_thread.start()
    if not resolver_done.wait(timeout_seconds):
        return [], True

    error_value = resolver_result.get("error")
    if isinstance(error_value, OSError):
        raise error_value

    address_infos = resolver_result.get("address_infos")
    if isinstance(address_infos, list):
        return address_infos, False
    return [], False


def _abort_merge_safely(
    self,
    repo_root: str,
    environment: Dict[str, str],
    timeout_seconds: float,
) -> bool:
    try:
        abort_result = run_git(
            self,
            repo_root,
            ["merge", "--abort"],
            environment,
            timeout_seconds=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.error("git merge --abort timed out | repo=%s", repo_root)
        return False
    except Exception:
        logger.exception("git merge --abort crashed | repo=%s", repo_root)
        return False

    if abort_result.returncode == 0:
        return True

    abort_error = combined_output(abort_result) or "git merge --abort failed"
    logger.error(
        "git merge --abort failed | repo=%s | error=%s",
        repo_root,
        abort_error[:1200],
    )
    return False


def git_environment(self, config: dict) -> Dict[str, str]:
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
    executor = GitExecutor(repo_root=repo_root, environment=environment)
    result = executor.run(arguments=arguments, timeout_seconds=timeout_seconds)
    if result.returncode == 0:
        return result

    if not failure_is_index_lock(combined_output(result)):
        return result

    if not clear_stale_index_lock(repo_root):
        return result

    logger.warning(
        "retrying git command after stale index.lock cleanup | repo=%s | args=%s",
        repo_root,
        " ".join(arguments[:4]),
    )
    return executor.run(arguments=arguments, timeout_seconds=timeout_seconds)


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
    remote_url_text = (remote_url_value or "").strip()
    if not remote_url_text:
        return None, None

    # Local remotes do not need network checks.
    if remote_url_text.startswith("file://"):
        return None, None
    if remote_url_text.startswith("/") or remote_url_text.startswith("./"):
        return None, None
    if remote_url_text.startswith("../"):
        return None, None
    if remote_url_text.startswith("\\\\") or remote_url_text.startswith("//"):
        return None, None
    if (
        len(remote_url_text) >= 3
        and remote_url_text[0].isalpha()
        and remote_url_text[1] == ":"
        and remote_url_text[2] in {"/", "\\"}
    ):
        return None, None

    if "://" in remote_url_text:
        parsed = urlparse(remote_url_text)
        host_name = parsed.hostname
        if not host_name:
            return None, None

        scheme_name = (parsed.scheme or "").lower()

        if parsed.port is not None:
            return host_name, parsed.port

        default_port_by_scheme = {
            "http": 80,
            "https": 443,
            "ssh": 22,
            "git": 9418,
            "git+ssh": 22,
            "ssh+git": 22,
            "sftp": 22,
        }
        return host_name, default_port_by_scheme.get(scheme_name, 22)

    # SCP-like syntax: [user@]host:path
    if ":" in remote_url_text:
        host_part, _, _path_part = remote_url_text.partition(":")
        if "@" in host_part:
            host_part = host_part.rsplit("@", 1)[1]
        if host_part and "/" not in host_part and "\\" not in host_part:
            return host_part, 22

    return None, None


def remote_is_reachable(
    self,
    repo_root: str,
    remote_name: str,
    environment: Dict[str, str],
    timeout_seconds: float,
    network_probe_timeout_seconds: float = 0.0,
) -> bool:
    remote_url_value = remote_url(
        self,
        repo_root,
        remote_name,
        environment,
        timeout_seconds,
    )
    if not remote_url_value:
        return True

    host_name, port_number = parse_remote_endpoint(remote_url_value)
    if not host_name or not port_number:
        return True

    timeout_candidates = [
        candidate
        for candidate in (timeout_seconds, network_probe_timeout_seconds)
        if candidate > 0.0
    ]
    if not timeout_candidates:
        logger.info(
            "invalid network probe timeout; waiting for network before pull | repo=%s | remote=%s",
            repo_root,
            remote_name,
        )
        return False
    connect_timeout_seconds = min(timeout_candidates)

    try:
        address_infos, dns_resolution_timed_out = _resolve_address_infos(
            host_name=host_name,
            port_number=port_number,
            timeout_seconds=connect_timeout_seconds,
        )
    except OSError:
        logger.info(
            "remote host resolution failed; waiting for network before pull | repo=%s | remote=%s | host=%s",
            repo_root,
            remote_name,
            host_name,
        )
        return False
    if dns_resolution_timed_out:
        logger.info(
            "remote host resolution timed out; waiting for network before pull | repo=%s | remote=%s | host=%s",
            repo_root,
            remote_name,
            host_name,
        )
        return False

    seen_sockaddrs = set()
    for family, socktype, proto, _canonname, sockaddr in address_infos:
        if sockaddr in seen_sockaddrs:
            continue
        seen_sockaddrs.add(sockaddr)
        probe_socket: Optional[socket.socket] = None
        try:
            probe_socket = socket.socket(family, socktype, proto)
            probe_socket.settimeout(connect_timeout_seconds)
            probe_socket.connect(sockaddr)
            probe_socket.close()
            probe_socket = None
            return True
        except OSError:
            if probe_socket is not None:
                try:
                    probe_socket.close()
                except OSError:
                    pass
            continue

    logger.info(
        "remote endpoint unreachable; waiting for network before pull | repo=%s | remote=%s | host=%s | port=%s",
        repo_root,
        remote_name,
        host_name,
        port_number,
    )
    return False


def pull_failure_looks_offline(
    output_text: str,
    offline_error_markers: list[str] | None = None,
) -> bool:
    output_lower = (output_text or "").lower()
    if not offline_error_markers:
        return False
    return any(indicator.lower() in output_lower for indicator in offline_error_markers)


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
    result = run_git(
        self,
        repo_root,
        ["rev-parse", "-q", "--verify", "MERGE_HEAD"],
        environment,
        timeout_seconds,
    )
    return result.returncode == 0 and bool((result.stdout or "").strip())


def conflicted_files(
    self, repo_root: str, environment: Dict[str, str], timeout_seconds: float
) -> list[str]:
    result = run_git(
        self,
        repo_root,
        ["diff", "--name-only", "--diff-filter=U"],
        environment,
        timeout_seconds,
    )
    if result.returncode != 0:
        return []
    return [
        line_text.strip()
        for line_text in (result.stdout or "").splitlines()
        if line_text.strip()
    ]


def auto_resolve_merge_conflicts(
    self,
    repo_root: str,
    environment: Dict[str, str],
    timeout_seconds: float,
    autoresolve_mode: str,
) -> bool:
    normalized_mode = (autoresolve_mode or "none").strip().lower()
    if normalized_mode not in {"none", "ours", "theirs", "union"}:
        normalized_mode = "none"

    conflicted_paths = conflicted_files(self, repo_root, environment, timeout_seconds)
    if not conflicted_paths or normalized_mode == "none":
        return False

    for relative_path in conflicted_paths:
        absolute_path = os.path.join(repo_root, relative_path)

        if normalized_mode in {"ours", "theirs"}:
            side_argument = "--ours" if normalized_mode == "ours" else "--theirs"
            checkout_result = run_git(
                self,
                repo_root,
                ["checkout", side_argument, "--", relative_path],
                environment,
                timeout_seconds,
            )
            if checkout_result.returncode != 0:
                logger.error(
                    "auto-resolve checkout failed | repo=%s | file=%s | mode=%s | err=%s",
                    repo_root,
                    relative_path,
                    normalized_mode,
                    (checkout_result.stderr or checkout_result.stdout or "")[:1200],
                )
                return False

        elif normalized_mode == "union":
            try:
                if os.path.isfile(absolute_path):
                    with open(
                        absolute_path,
                        "r",
                        encoding="utf-8",
                        errors="surrogateescape",
                    ) as file_obj:
                        file_text = file_obj.read()
                    resolved_text = union_resolve_text(file_text)
                    if resolved_text is None:
                        checkout_result = run_git(
                            self,
                            repo_root,
                            ["checkout", "--ours", "--", relative_path],
                            environment,
                            timeout_seconds,
                        )
                        if checkout_result.returncode != 0:
                            logger.error(
                                "auto-resolve union fallback checkout failed | repo=%s | file=%s | err=%s",
                                repo_root,
                                relative_path,
                                (
                                    checkout_result.stderr
                                    or checkout_result.stdout
                                    or ""
                                )[:1200],
                            )
                            return False
                    else:
                        with open(
                            absolute_path,
                            "w",
                            encoding="utf-8",
                            errors="surrogateescape",
                        ) as file_obj:
                            file_obj.write(resolved_text)
                else:
                    checkout_result = run_git(
                        self,
                        repo_root,
                        ["checkout", "--ours", "--", relative_path],
                        environment,
                        timeout_seconds,
                    )
                    if checkout_result.returncode != 0:
                        logger.error(
                            "auto-resolve union non-file checkout failed | repo=%s | path=%s | err=%s",
                            repo_root,
                            relative_path,
                            (
                                checkout_result.stderr or checkout_result.stdout or ""
                            )[:1200],
                        )
                        return False
            except OSError:
                logger.exception(
                    "auto-resolve union IO failed | repo=%s | file=%s",
                    repo_root,
                    relative_path,
                )
                return False

        add_result = run_git(
            self,
            repo_root, ["add", "--", relative_path], environment, timeout_seconds
        )
        if add_result.returncode != 0:
            logger.error(
                "auto-resolve git add failed | repo=%s | file=%s | err=%s",
                repo_root,
                relative_path,
                (add_result.stderr or add_result.stdout or "")[:1200],
            )
            return False

    commit_result = run_git(
        self,
        repo_root, ["commit", "--no-edit"], environment, timeout_seconds
    )
    if commit_result.returncode != 0:
        logger.error(
            "auto-resolve commit failed | repo=%s | err=%s",
            repo_root,
            (commit_result.stderr or commit_result.stdout or "")[:1200],
        )
    return commit_result.returncode == 0


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
        name=f"pullwait:{repo_root}",
        message=(
            f"Repository:\n{repo_root}\n\n"
            f"Remote:\n{remote_label}\n\n"
            f"Pull waiting for network.\n\n"
            f"Reason:\n{reason[:600]}"
        ),
        config=notify_config,
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

    branch_name = current_branch(self, repo_root, environment, operation_timeout_seconds)
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
    safe_notify(
        name=f"timeout:pull:{repo_root}",
        message=f"git pull timed out:\n{repo_root}",
        config=notify_config,
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
        resolved = auto_resolve_merge_conflicts(
            self,
            repo_root,
            environment,
            operation_timeout_seconds,
            autoresolve_mode=autoresolve_mode,
        )
        if resolved:
            return True

        pull_error = _pull_error_text(pull_result)
        abort_ok = _abort_merge_safely(
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
