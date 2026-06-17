from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Union

PathLike = Union[str, bytes]


class MergeAutoresolveMode(str, Enum):
    NONE = "none"
    OURS = "ours"
    THEIRS = "theirs"
    UNION = "union"
    MARKERS = "markers"


def parse_merge_autoresolve_mode(raw_value: str) -> MergeAutoresolveMode:
    normalized = str(raw_value or "").strip().lower()
    for candidate in MergeAutoresolveMode:
        if candidate.value == normalized:
            return candidate
    return MergeAutoresolveMode.NONE


@dataclass(frozen=True)
class GitPolicy:
    auto_merge_on_push: bool = True
    auto_set_upstream: bool = True
    autoresolve_mode: MergeAutoresolveMode = MergeAutoresolveMode.UNION
    network_probe_timeout_seconds: float = 0.0
    pull_offline_error_markers: tuple[str, ...] = ()


@dataclass
class _RepoBatch:
    repo_root: str
    event_type: str
    hinted_paths: list[str]

    base_message: str
    add_timestamp_to_message: bool
    timestamp_format: str
    commit_message_style: str
    commit_message_max_subject_files: int
    commit_message_max_body_files: int
    environment: Dict[str, str]

    git_timeout_seconds: float
    pull_timeout_seconds: float
    push_timeout_seconds: float
    sync_retry_window_seconds: float
    sync_retry_backoff_start_seconds: float
    sync_retry_backoff_max_seconds: float

    notify_provider: str
    notify_min_interval_sec: float
    notify_error_backoff_base_seconds: float
    notify_error_backoff_max_seconds: float
    notify_error_burst_limit: int
    notify_error_burst_window_seconds: float
    policy: GitPolicy = field(default_factory=GitPolicy)
