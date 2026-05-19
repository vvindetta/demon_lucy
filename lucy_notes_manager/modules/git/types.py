from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Union

PathLike = Union[str, bytes]


@dataclass
class _RepoBatch:
    repo_root: str
    event_type: str
    hinted_paths: list[str]
    wants_pull: bool

    base_message: str
    add_timestamp_to_message: bool
    timestamp_format: str
    environment: Dict[str, str]

    git_timeout_seconds: float
    pull_timeout_seconds: float
    push_timeout_seconds: float
    sync_retry_window_seconds: float
    sync_retry_backoff_start_seconds: float
    sync_retry_backoff_max_seconds: float

    notify_provider: str
    notify_min_interval_sec: float
    network_probe_timeout_seconds: float = 0.0
    pull_offline_error_markers: list[str] = field(default_factory=list)

    auto_merge_on_push: bool = True
    auto_set_upstream: bool = True
    autoresolve_mode: str = "union"  # none|ours|theirs|union
