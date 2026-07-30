from __future__ import annotations

from demon_lucy.lib.args.models import KnownArg, Template
from demon_lucy.modules.git.types import (
    GitCommitMessageStyle,
    MergeAutoresolveMode,
)

GIT_TEMPLATE: Template = [
    KnownArg(
        name="git-commit-message",
        value_type=str,
        default="Auto-commit",
        description="Base commit message. Example: --git-commit-message 'Notes update'.",
    ),
    KnownArg(
        name="git-commit-message-timestamp",
        value_type=bool,
        default=False,
        description="Append a timestamp to the commit message.",
    ),
    KnownArg(
        name="git-commit-message-timestamp-format",
        value_type=str,
        default="%Y-%m-%d_%H-%M-%S",
        description="Timestamp format for --git-commit-message-timestamp (Python strftime).",
    ),
    KnownArg(
        name="git-commit-message-style",
        value_type=GitCommitMessageStyle,
        default=GitCommitMessageStyle.DETAILED,
        description="Commit message style: detailed or compact. Detailed adds a commit body with staged file actions.",
    ),
    KnownArg(
        name="git-commit-message-max-subject-files",
        value_type=int,
        default=3,
        description="Maximum number of changed files named directly in the commit subject before using counts.",
    ),
    KnownArg(
        name="git-commit-message-max-body-files",
        value_type=int,
        default=30,
        description="Maximum number of changed files listed in the detailed commit message body.",
    ),
    KnownArg(
        name="git-sync-on-opened-disable",
        value_type=bool,
        default=False,
        description="Disable git sync reaction for opened events. If enabled, opened events are ignored.",
    ),
    KnownArg(
        name="git-push-auto-merge",
        value_type=bool,
        default=True,
        description="If 'git push' is rejected because the remote is ahead, automatically run 'git pull --no-rebase' (merge) and retry push. No rebase, no force.",
    ),
    KnownArg(
        name="git-upstream-auto-set",
        value_type=bool,
        default=True,
        description="If the current branch has no upstream, try to set it to <remote>/<branch> (prefer remote 'origin') when that remote branch exists.",
    ),
    KnownArg(
        name="git-merge-autoresolve",
        value_type=MergeAutoresolveMode,
        default=MergeAutoresolveMode.UNION,
        description="How to auto-resolve merge conflicts during auto-merge: "
        "'none' (do not resolve), 'ours' (keep local), 'theirs' (keep remote), "
        "'union' (keep both sides, remove markers), 'markers' (keep conflict markers and commit merge).",
    ),
    KnownArg(
        name="git-command-timeout-seconds",
        value_type=float,
        default=8.0,
        description="Timeout (seconds) for git add/status/commit operations.",
    ),
    KnownArg(
        name="git-pull-timeout-seconds",
        value_type=float,
        default=30.0,
        description="Timeout (seconds) for git pull (merge). Increase for slow networks or large repos.",
    ),
    KnownArg(
        name="git-network-probe-timeout-seconds",
        value_type=float,
        default=2.0,
        description="Timeout (seconds) for remote host network probe before pull. "
        "Used to decide whether to wait for network and skip pull while offline.",
    ),
    KnownArg(
        name="git-pull-offline-error-markers",
        value_type=str,
        default=[
            "could not resolve host",
            "temporary failure in name resolution",
            "name or service not known",
            "network is unreachable",
            "no route to host",
            "connection timed out",
            "operation timed out",
            "failed to connect",
            "connection refused",
        ],
        description="Error markers treated as offline/network failures for git pull. "
        "Provide one or more markers to customize detection.",
    ),
    KnownArg(
        name="git-push-timeout-seconds",
        value_type=float,
        default=20.0,
        description="Timeout (seconds) for git push.",
    ),
    KnownArg(
        name="git-sync-retry-window-seconds",
        value_type=float,
        default=120.0,
        description="How long background git sync retries pull/push failures before giving up. Set 0 to disable retries.",
    ),
    KnownArg(
        name="git-sync-retry-backoff-start-seconds",
        value_type=float,
        default=5.0,
        description="Initial retry delay in seconds for background git sync retries.",
    ),
    KnownArg(
        name="git-sync-retry-backoff-max-seconds",
        value_type=float,
        default=60.0,
        description="Maximum retry delay cap in seconds for background git sync retries.",
    ),
]
