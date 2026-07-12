from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Union

from demon_lucy.lib.args.parser import StrEnum

PathLike = Union[str, bytes]


class MergeAutoresolveMode(StrEnum):
    NONE = "none"
    OURS = "ours"
    THEIRS = "theirs"
    UNION = "union"
    MARKERS = "markers"


class GitCommitMessageStyle(StrEnum):
    DETAILED = "detailed"
    COMPACT = "compact"


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
    commit_message_style: GitCommitMessageStyle
    commit_message_max_subject_files: int
    commit_message_max_body_files: int
    environment: Dict[str, str]

    git_timeout_seconds: float
    pull_timeout_seconds: float
    push_timeout_seconds: float
    sync_retry_window_seconds: float
    sync_retry_backoff_start_seconds: float
    sync_retry_backoff_max_seconds: float
    policy: GitPolicy = field(default_factory=GitPolicy)
