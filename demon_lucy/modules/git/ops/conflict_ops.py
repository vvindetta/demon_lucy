from __future__ import annotations

import os
import subprocess
from typing import Callable, Dict

from demon_lucy.lib.logfmt import log_record
from demon_lucy.modules.git.types import MergeAutoresolveMode


def abort_merge_safely(
    self_obj,
    repo_root: str,
    environment: Dict[str, str],
    timeout_seconds: float,
    *,
    run_git_fn: Callable,
    combined_output_fn: Callable[[subprocess.CompletedProcess[str]], str],
    logger,
    failure_is_deferred: bool = False,
) -> bool:
    try:
        abort_result = run_git_fn(
            self_obj,
            repo_root,
            ["merge", "--abort"],
            environment,
            timeout_seconds=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        log_method = logger.warning if failure_is_deferred else logger.error
        log_method(
            log_record("git.merge_abort_failed", reason="timeout", repo=repo_root)
        )
        return False
    except Exception:
        logger.exception(
            log_record("git.merge_abort_failed", reason="crashed", repo=repo_root)
        )
        return False

    if abort_result.returncode == 0:
        return True

    abort_error = combined_output_fn(abort_result) or "git merge --abort failed"
    log_method = logger.warning if failure_is_deferred else logger.error
    log_method(
        log_record(
            "git.merge_abort_failed",
            repo=repo_root,
            error=abort_error[:1200],
        )
    )
    return False


def merge_in_progress(
    self_obj,
    repo_root: str,
    environment: Dict[str, str],
    timeout_seconds: float,
    *,
    run_git_fn: Callable,
) -> bool:
    result = run_git_fn(
        self_obj,
        repo_root,
        ["rev-parse", "-q", "--verify", "MERGE_HEAD"],
        environment,
        timeout_seconds,
    )
    return result.returncode == 0 and bool((result.stdout or "").strip())


def conflicted_files(
    self_obj,
    repo_root: str,
    environment: Dict[str, str],
    timeout_seconds: float,
    *,
    run_git_fn: Callable,
) -> list[str]:
    result = run_git_fn(
        self_obj,
        repo_root,
        ["diff", "--name-only", "-z", "--diff-filter=U"],
        environment,
        timeout_seconds,
    )
    if result.returncode != 0:
        return []
    raw_output = result.stdout or ""
    return [item for item in raw_output.split("\x00") if item]


def auto_resolve_merge_conflicts(
    self_obj,
    repo_root: str,
    environment: Dict[str, str],
    timeout_seconds: float,
    autoresolve_mode: MergeAutoresolveMode,
    *,
    run_git_fn: Callable,
    union_resolve_text_fn: Callable[[str], str | None],
    logger,
    failure_is_deferred: bool = False,
) -> bool:
    conflicted_paths = conflicted_files(
        self_obj,
        repo_root,
        environment,
        timeout_seconds,
        run_git_fn=run_git_fn,
    )
    if not conflicted_paths or autoresolve_mode is MergeAutoresolveMode.NONE:
        return False

    for relative_path in conflicted_paths:
        absolute_path = os.path.join(repo_root, relative_path)

        if autoresolve_mode in {
            MergeAutoresolveMode.OURS,
            MergeAutoresolveMode.THEIRS,
        }:
            side_argument = (
                "--ours"
                if autoresolve_mode is MergeAutoresolveMode.OURS
                else "--theirs"
            )
            checkout_result = run_git_fn(
                self_obj,
                repo_root,
                ["checkout", side_argument, "--", relative_path],
                environment,
                timeout_seconds,
            )
            if checkout_result.returncode != 0:
                log_method = logger.warning if failure_is_deferred else logger.error
                log_method(
                    log_record(
                        "git.autoresolve_failed",
                        stage="checkout",
                        repo=repo_root,
                        file=relative_path,
                        mode=autoresolve_mode,
                        error=(checkout_result.stderr or checkout_result.stdout or "")[
                            :1200
                        ],
                    )
                )
                return False

        elif autoresolve_mode is MergeAutoresolveMode.UNION:
            try:
                if os.path.isfile(absolute_path):
                    with open(
                        absolute_path,
                        "r",
                        encoding="utf-8",
                        errors="surrogateescape",
                    ) as file_obj:
                        file_text = file_obj.read()
                    resolved_text = union_resolve_text_fn(file_text)
                    if resolved_text is not None:
                        with open(
                            absolute_path,
                            "w",
                            encoding="utf-8",
                            errors="surrogateescape",
                        ) as file_obj:
                            file_obj.write(resolved_text)
                    else:
                        logger.warning(
                            log_record(
                                "git.autoresolve_failed",
                                stage="union",
                                reason="unresolved_markers",
                                repo=repo_root,
                                file=relative_path,
                            )
                        )
                        return False
                else:
                    logger.warning(
                        log_record(
                            "git.autoresolve_failed",
                            stage="union",
                            reason="non_file_conflict",
                            repo=repo_root,
                            path=relative_path,
                        )
                    )
                    return False
            except OSError:
                logger.exception(
                    log_record(
                        "git.autoresolve_failed",
                        stage="union",
                        reason="io_error",
                        repo=repo_root,
                        file=relative_path,
                    )
                )
                return False

        elif autoresolve_mode is MergeAutoresolveMode.MARKERS:
            logger.warning(
                log_record(
                    "git.autoresolve_markers",
                    policy="keep_conflict_markers",
                    repo=repo_root,
                    file=relative_path,
                )
            )

        add_result = run_git_fn(
            self_obj,
            repo_root,
            ["add", "-A", "--", relative_path],
            environment,
            timeout_seconds,
        )
        if add_result.returncode != 0:
            log_method = logger.warning if failure_is_deferred else logger.error
            log_method(
                log_record(
                    "git.autoresolve_failed",
                    stage="add",
                    repo=repo_root,
                    file=relative_path,
                    error=(add_result.stderr or add_result.stdout or "")[:1200],
                )
            )
            return False

    commit_result = run_git_fn(
        self_obj,
        repo_root,
        ["commit", "--no-edit"],
        environment,
        timeout_seconds,
    )
    if commit_result.returncode != 0:
        log_method = logger.warning if failure_is_deferred else logger.error
        log_method(
            log_record(
                "git.autoresolve_failed",
                stage="commit",
                repo=repo_root,
                error=(commit_result.stderr or commit_result.stdout or "")[:1200],
            )
        )
    elif autoresolve_mode is MergeAutoresolveMode.MARKERS:
        logger.warning(
            log_record(
                "git.autoresolve_markers_committed",
                repo=repo_root,
                files=len(conflicted_paths),
            )
        )
    return commit_result.returncode == 0


def resolve_merge_conflicts_with_fallback(
    self_obj,
    repo_root: str,
    environment: Dict[str, str],
    timeout_seconds: float,
    autoresolve_mode: MergeAutoresolveMode,
    *,
    auto_resolve_fn: Callable,
    logger,
) -> bool:
    resolved = auto_resolve_fn(
        self_obj,
        repo_root,
        environment,
        timeout_seconds,
        autoresolve_mode=autoresolve_mode,
    )
    if resolved:
        return True

    if autoresolve_mode in {
        MergeAutoresolveMode.NONE,
        MergeAutoresolveMode.MARKERS,
    }:
        return False

    logger.warning(
        log_record(
            "git.autoresolve_retry",
            reason="fallback_to_markers",
            repo=repo_root,
            mode=autoresolve_mode,
        )
    )
    return auto_resolve_fn(
        self_obj,
        repo_root,
        environment,
        timeout_seconds,
        autoresolve_mode=MergeAutoresolveMode.MARKERS,
    )
