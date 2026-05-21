from __future__ import annotations

from typing import Any, Mapping

from lucy_notes_manager.modules.git.types import (
    GitPolicy,
    parse_merge_autoresolve_mode,
    _RepoBatch,
)

ConfigSnapshot = Mapping[str, Any]


def _repo_batch_kwargs(
    *,
    repo_root: str,
    event_type: str,
    paths: list[str],
    config_snapshot: ConfigSnapshot,
    environment: dict[str, str],
    wants_pull: bool,
) -> dict[str, Any]:
    hinted_paths = [path_item for path_item in paths if path_item]
    policy = GitPolicy(
        auto_merge_on_push=bool(config_snapshot["git_push_auto_merge"]),
        auto_set_upstream=bool(config_snapshot["git_upstream_auto_set"]),
        autoresolve_mode=parse_merge_autoresolve_mode(
            str(config_snapshot["git_merge_autoresolve"])
        ),
        network_probe_timeout_seconds=float(
            config_snapshot["git_network_probe_timeout_seconds"]
        ),
        pull_offline_error_markers=tuple(config_snapshot["git_pull_offline_error_markers"]),
    )
    return {
        "repo_root": repo_root,
        "event_type": event_type,
        "hinted_paths": hinted_paths,
        "wants_pull": bool(wants_pull),
        "base_message": config_snapshot["git_commit_message"],
        "add_timestamp_to_message": config_snapshot["git_commit_message_timestamp"],
        "timestamp_format": config_snapshot["git_commit_message_timestamp_format"],
        "environment": environment,
        "git_timeout_seconds": config_snapshot["git_command_timeout_seconds"],
        "pull_timeout_seconds": config_snapshot["git_pull_timeout_seconds"],
        "push_timeout_seconds": config_snapshot["git_push_timeout_seconds"],
        "sync_retry_window_seconds": config_snapshot["git_sync_retry_window_seconds"],
        "sync_retry_backoff_start_seconds": config_snapshot[
            "git_sync_retry_backoff_start_seconds"
        ],
        "sync_retry_backoff_max_seconds": config_snapshot[
            "git_sync_retry_backoff_max_seconds"
        ],
        "notify_provider": config_snapshot["sys_notification_provider"],
        "notify_min_interval_sec": config_snapshot["sys_notification_min_interval_seconds"],
        "notify_error_backoff_base_seconds": config_snapshot[
            "sys_notification_error_backoff_base_seconds"
        ],
        "notify_error_backoff_max_seconds": config_snapshot[
            "sys_notification_error_backoff_max_seconds"
        ],
        "notify_error_burst_limit": config_snapshot["sys_notification_error_burst_limit"],
        "notify_error_burst_window_seconds": config_snapshot[
            "sys_notification_error_burst_window_seconds"
        ],
        "policy": policy,
    }


def make_repo_batch(
    *,
    repo_root: str,
    event_type: str,
    paths: list[str],
    config_snapshot: ConfigSnapshot,
    environment: dict[str, str],
    wants_pull: bool,
) -> _RepoBatch:
    kwargs = _repo_batch_kwargs(
        repo_root=repo_root,
        event_type=event_type,
        paths=paths,
        config_snapshot=config_snapshot,
        environment=environment,
        wants_pull=wants_pull,
    )
    return _RepoBatch(**kwargs)
