from __future__ import annotations

import time
from typing import Any, Mapping

from lucy_notes_manager.modules.git.types import _RepoBatch

ConfigSnapshot = Mapping[str, Any]

_SNAPSHOT_MUTABLE_BATCH_FIELDS = (
    "base_message",
    "add_timestamp_to_message",
    "timestamp_format",
    "environment",
    "debounce_seconds",
    "git_timeout_seconds",
    "pull_timeout_seconds",
    "push_timeout_seconds",
    "backoff_start_seconds",
    "backoff_max_seconds",
    "pull_cooldown_min_seconds",
    "pull_cooldown_max_seconds",
    "max_batch_seconds",
    "network_probe_timeout_seconds",
    "pull_offline_error_markers",
    "notify_provider",
    "notify_min_interval_sec",
    "auto_merge_on_push",
    "auto_set_upstream",
    "autoresolve_mode",
)


def _value_or_override(value: Any, override: float | None) -> float:
    if override is None:
        return float(value)
    return float(override)


def _repo_batch_kwargs(
    *,
    repo_root: str,
    config_snapshot: ConfigSnapshot,
    environment: dict[str, str],
    wants_pull: bool,
    debounce_seconds_override: float | None,
    pull_cooldown_min_seconds_override: float | None,
    max_batch_seconds_override: float | None,
) -> dict[str, Any]:
    return {
        "repo_root": repo_root,
        "base_message": config_snapshot["git_msg"],
        "add_timestamp_to_message": config_snapshot["git_tsmsg"],
        "timestamp_format": config_snapshot["git_tsfmt"],
        "environment": environment,
        "debounce_seconds": _value_or_override(
            config_snapshot["git_debounce_seconds"],
            debounce_seconds_override,
        ),
        "git_timeout_seconds": config_snapshot["git_timeout_sec"],
        "pull_timeout_seconds": config_snapshot["git_pull_timeout_sec"],
        "push_timeout_seconds": config_snapshot["git_push_timeout_sec"],
        "backoff_start_seconds": config_snapshot["git_push_backoff_start_sec"],
        "backoff_max_seconds": config_snapshot["git_push_backoff_max_sec"],
        "pull_cooldown_min_seconds": _value_or_override(
            config_snapshot["git_pull_cooldown_min_sec"],
            pull_cooldown_min_seconds_override,
        ),
        "pull_cooldown_max_seconds": config_snapshot["git_pull_cooldown_max_sec"],
        "max_batch_seconds": _value_or_override(
            config_snapshot["git_max_batch_seconds"],
            max_batch_seconds_override,
        ),
        "network_probe_timeout_seconds": config_snapshot["git_network_probe_timeout_sec"],
        "pull_offline_error_markers": list(config_snapshot["git_pull_offline_error_marker"]),
        "notify_provider": config_snapshot["sys_notify_provider"],
        "notify_min_interval_sec": config_snapshot["sys_notify_min_interval_sec"],
        "wants_pull": bool(wants_pull),
        "auto_merge_on_push": config_snapshot["git_auto_merge_on_push"],
        "auto_set_upstream": config_snapshot["git_auto_set_upstream"],
        "autoresolve_mode": config_snapshot["git_autoresolve"],
    }


def make_repo_batch(
    *,
    repo_root: str,
    config_snapshot: ConfigSnapshot,
    environment: dict[str, str],
    wants_pull: bool,
    debounce_seconds_override: float | None = None,
    pull_cooldown_min_seconds_override: float | None = None,
    max_batch_seconds_override: float | None = None,
) -> _RepoBatch:
    kwargs = _repo_batch_kwargs(
        repo_root=repo_root,
        config_snapshot=config_snapshot,
        environment=environment,
        wants_pull=wants_pull,
        debounce_seconds_override=debounce_seconds_override,
        pull_cooldown_min_seconds_override=pull_cooldown_min_seconds_override,
        max_batch_seconds_override=max_batch_seconds_override,
    )
    return _RepoBatch(**kwargs)


def refresh_repo_batch_from_snapshot(
    *,
    batch: _RepoBatch,
    config_snapshot: ConfigSnapshot,
    environment: dict[str, str],
) -> None:
    values = _repo_batch_kwargs(
        repo_root=batch.repo_root,
        config_snapshot=config_snapshot,
        environment=environment,
        wants_pull=batch.wants_pull,
        debounce_seconds_override=None,
        pull_cooldown_min_seconds_override=None,
        max_batch_seconds_override=None,
    )
    for field_name in _SNAPSHOT_MUTABLE_BATCH_FIELDS:
        setattr(batch, field_name, values[field_name])


def add_event_to_batch(
    *,
    batch: _RepoBatch,
    event_type: str,
    paths: list[str],
    wants_pull: bool,
    now_timestamp: float | None = None,
) -> None:
    batch.wants_pull = batch.wants_pull or bool(wants_pull)
    batch.last_event_at = time.time() if now_timestamp is None else float(now_timestamp)
    batch.event_types.add(event_type)
    for path_item in paths:
        if path_item:
            batch.hinted_paths.add(path_item)
