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
        description="Base text for automatic commit messages. Example: --git-commit-message \"Notes update\".",
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
        description="Commit message layout. Detailed messages include staged file actions in the commit body. Values: detailed, compact.",
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
        description="Ignore opened events when scheduling Git sync.",
    ),
    KnownArg(
        name="git-push-auto-merge",
        value_type=bool,
        default=True,
        description="Merge remote changes and retry when a push is rejected because the remote is ahead. Uses git pull --no-rebase; never rebases or force-pushes.",
    ),
    KnownArg(
        name="git-upstream-auto-set",
        value_type=bool,
        default=True,
        description="Set an upstream when the current branch has none and a matching remote branch exists. Prefer origin.",
    ),
    KnownArg(
        name="git-merge-autoresolve",
        value_type=MergeAutoresolveMode,
        default=MergeAutoresolveMode.UNION,
        description="Conflict handling during automatic merges. None leaves conflicts unresolved; ours keeps local changes; theirs keeps remote changes; union keeps both sides without markers; markers commits both sides with conflict markers.",
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
        description="Timeout in seconds for checking the remote host before pulling. Failed probes postpone sync while offline.",
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
        description="Error text fragments that identify offline or network failures during a pull.",
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
