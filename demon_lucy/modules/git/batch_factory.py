from __future__ import annotations

from demon_lucy.lib.args.models import ParsedArgs
from demon_lucy.modules.git.types import (
    GitPolicy,
    _RepoBatch,
)


def make_repo_batch(
    *,
    repo_root: str,
    event_type: str,
    paths: list[str],
    args: ParsedArgs,
    environment: dict[str, str],
) -> _RepoBatch:
    policy = GitPolicy(
        auto_merge_on_push=args.require("git-push-auto-merge").value,
        auto_set_upstream=args.require("git-upstream-auto-set").value,
        autoresolve_mode=args.require("git-merge-autoresolve").value,
        network_probe_timeout_seconds=args.require(
            "git-network-probe-timeout-seconds"
        ).value,
        pull_offline_error_markers=tuple(
            args.require("git-pull-offline-error-markers").value
        ),
    )
    return _RepoBatch(
        repo_root=repo_root,
        event_type=event_type,
        hinted_paths=[path for path in paths if path],
        base_message=args.require("git-commit-message").value,
        add_timestamp_to_message=args.require("git-commit-message-timestamp").value,
        timestamp_format=args.require("git-commit-message-timestamp-format").value,
        commit_message_style=args.require("git-commit-message-style").value,
        commit_message_max_subject_files=args.require(
            "git-commit-message-max-subject-files"
        ).value,
        commit_message_max_body_files=args.require(
            "git-commit-message-max-body-files"
        ).value,
        environment=environment,
        git_timeout_seconds=args.require("git-command-timeout-seconds").value,
        pull_timeout_seconds=args.require("git-pull-timeout-seconds").value,
        push_timeout_seconds=args.require("git-push-timeout-seconds").value,
        sync_retry_window_seconds=args.require("git-sync-retry-window-seconds").value,
        sync_retry_backoff_start_seconds=args.require(
            "git-sync-retry-backoff-start-seconds"
        ).value,
        sync_retry_backoff_max_seconds=args.require(
            "git-sync-retry-backoff-max-seconds"
        ).value,
        policy=policy,
    )
