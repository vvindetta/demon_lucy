from __future__ import annotations

import logging
import os
import socket
import subprocess
from typing import Dict, Optional
from urllib.parse import urlparse

from lucy_notes_manager.lib import safe_notify
from lucy_notes_manager.lib.path import abs_expand_path
from lucy_notes_manager.modules.git.helpers import union_resolve_text

logger = logging.getLogger(__name__)


def git_environment(self, config: dict) -> Dict[str, str]:
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"

    key_path_raw = config["git_key"].strip()
    if not key_path_raw:
        return environment

    key_path = abs_expand_path(key_path_raw)
    environment["GIT_SSH_COMMAND"] = (
        f'ssh -i "{key_path}" '
        f"-o IdentitiesOnly=yes "
        f"-o BatchMode=yes "
        f"-o StrictHostKeyChecking=accept-new"
    )
    return environment


def run_git(
    self,
    repo_root: str,
    arguments: list[str],
    environment: Dict[str, str],
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git"] + arguments,
        cwd=repo_root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=timeout_seconds,
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
    if len(remote_url_text) >= 3 and remote_url_text[1:3] == ":/":
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
        address_infos = socket.getaddrinfo(
            host_name,
            port_number,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except OSError:
        logger.info(
            "remote host resolution failed; waiting for network before pull | repo=%s | remote=%s | host=%s",
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


def safe_pull_merge(
    self,
    repo_root: str,
    environment: Dict[str, str],
    pull_timeout_seconds: float,
    operation_timeout_seconds: float,
    autoresolve_mode: str,
    auto_set_upstream: bool = True,
    network_probe_timeout_seconds: float = 0.0,
    pull_offline_error_markers: list[str] | None = None,
) -> bool:
    if not has_upstream(self, repo_root, environment, operation_timeout_seconds):
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
            )
            return False

        if not remote_is_reachable(
            self=self,
            repo_root=repo_root,
            remote_name=remote_name,
            environment=environment,
            timeout_seconds=operation_timeout_seconds,
            network_probe_timeout_seconds=network_probe_timeout_seconds,
        ):
            return False

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
            )
            return False

        if auto_set_upstream:
            try_set_upstream(
                self,
                repo_root,
                remote_name,
                branch_name,
                environment,
                timeout_seconds=operation_timeout_seconds,
            )

        try:
            pull_result = run_git(
                self,
                repo_root,
                ["pull", "--no-rebase", "--no-edit", remote_name, branch_name],
                environment,
                timeout_seconds=pull_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            if not remote_is_reachable(
                self=self,
                repo_root=repo_root,
                remote_name=remote_name,
                environment=environment,
                timeout_seconds=operation_timeout_seconds,
                network_probe_timeout_seconds=network_probe_timeout_seconds,
            ):
                logger.info(
                    "git pull timed out while network is offline; waiting for network | repo=%s",
                    repo_root,
                )
                return False
            logger.error("git pull timed out | repo=%s", repo_root)
            safe_notify(
                name=f"timeout:pull:{repo_root}",
                message=f"git pull timed out:\n{repo_root}",
            )
            return False

        if pull_result.returncode == 0:
            return True

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

            run_git(
                self,
                repo_root,
                ["merge", "--abort"],
                environment,
                timeout_seconds=operation_timeout_seconds,
            )
            pull_error = (pull_result.stderr or pull_result.stdout or "git pull failed").strip()
            logger.error(
                "git pull conflict; auto-resolve failed; merge aborted | repo=%s | error=%s",
                repo_root,
                pull_error[:1200],
            )
            safe_notify(
                name=f"pull-conflict:{repo_root}",
                message=(
                    f"Repository:\n{repo_root}\n\n"
                    f"Auto-merge conflict resolution failed.\n"
                    f"Merge aborted (no rebase / no force).\n\n"
                    f"Error:\n{pull_error[:1200]}"
                ),
            )
            return False

        pull_error = (pull_result.stderr or pull_result.stdout or "git pull failed").strip()
        if pull_failure_looks_offline(
            pull_error,
            pull_offline_error_markers,
        ):
            logger.info(
                "git pull failed because network is offline; waiting for network | repo=%s",
                repo_root,
            )
            return False
        logger.error("git pull failed | repo=%s | error=%s", repo_root, pull_error[:1200])
        safe_notify(
            name=f"pullfail:{repo_root}",
            message=(
                f"Repository:\n{repo_root}\n\n"
                f"Command:\ngit pull --no-rebase {remote_name} {branch_name}\n\n"
                f"Error:\n{pull_error[:1200]}"
            ),
        )
        return False

    remote_name = upstream_remote_name(self, repo_root, environment, operation_timeout_seconds)
    if remote_name and not remote_is_reachable(
        self=self,
        repo_root=repo_root,
        remote_name=remote_name,
        environment=environment,
        timeout_seconds=operation_timeout_seconds,
        network_probe_timeout_seconds=network_probe_timeout_seconds,
    ):
        return False

    try:
        pull_result = run_git(
            self,
            repo_root,
            ["pull", "--no-rebase", "--no-edit"],
            environment,
            timeout_seconds=pull_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        if remote_name and not remote_is_reachable(
            self=self,
            repo_root=repo_root,
            remote_name=remote_name,
            environment=environment,
            timeout_seconds=operation_timeout_seconds,
            network_probe_timeout_seconds=network_probe_timeout_seconds,
        ):
            logger.info(
                "git pull timed out while network is offline; waiting for network | repo=%s",
                repo_root,
            )
            return False
        logger.error("git pull timed out | repo=%s", repo_root)
        safe_notify(
            name=f"timeout:pull:{repo_root}",
            message=f"git pull timed out:\n{repo_root}",
        )
        return False

    if pull_result.returncode == 0:
        return True

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

        run_git(
            self,
            repo_root,
            ["merge", "--abort"],
            environment,
            timeout_seconds=operation_timeout_seconds,
        )
        pull_error = (pull_result.stderr or pull_result.stdout or "git pull failed").strip()
        logger.error(
            "git pull conflict; auto-resolve failed; merge aborted | repo=%s | error=%s",
            repo_root,
            pull_error[:1200],
        )
        safe_notify(
            name=f"pull-conflict:{repo_root}",
            message=(
                f"Repository:\n{repo_root}\n\n"
                f"Auto-merge conflict resolution failed.\n"
                f"Merge aborted (no rebase / no force).\n\n"
                f"Error:\n{pull_error[:1200]}"
            ),
        )
        return False

    pull_error = (pull_result.stderr or pull_result.stdout or "git pull failed").strip()
    if pull_failure_looks_offline(
        pull_error,
        pull_offline_error_markers,
    ):
        logger.info(
            "git pull failed because network is offline; waiting for network | repo=%s",
            repo_root,
        )
        return False
    logger.error("git pull failed | repo=%s | error=%s", repo_root, pull_error[:1200])
    safe_notify(
        name=f"pullfail:{repo_root}",
        message=(
            f"Repository:\n{repo_root}\n\n"
            f"Command:\ngit pull --no-rebase\n\n"
            f"Error:\n{pull_error[:1200]}"
        ),
    )
    return False
