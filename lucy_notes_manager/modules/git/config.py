from __future__ import annotations

from lucy_notes_manager.lib.args import Template

GIT_TEMPLATE: Template = [
    (
        "--git-commit-message",
        str,
        "Auto-commit",
        "Base commit message. Example: --git-commit-message 'Notes update'.",
        False,
    ),
    (
        "--git-commit-message-timestamp",
        bool,
        False,
        "Append a timestamp to the commit message.",
        False,
    ),
    (
        "--git-commit-message-timestamp-format",
        str,
        "%Y-%m-%d_%H-%M-%S",
        "Timestamp format for --git-commit-message-timestamp (Python strftime).",
        False,
    ),
    (
        "--git-sync-on-opened-disable",
        bool,
        False,
        "Disable git sync reaction for opened events. If enabled, opened events are ignored.",
        False,
    ),
    (
        "--git-push-auto-merge",
        bool,
        True,
        "If 'git push' is rejected because the remote is ahead, automatically run 'git pull --no-rebase' (merge) and retry push. No rebase, no force.",
        False,
    ),
    (
        "--git-upstream-auto-set",
        bool,
        True,
        "If the current branch has no upstream, try to set it to <remote>/<branch> (prefer remote 'origin') when that remote branch exists.",
        False,
    ),
    (
        "--git-merge-autoresolve",
        str,
        "union",
        "How to auto-resolve merge conflicts during auto-merge: "
        "'none' (do not resolve), 'ours' (keep local), 'theirs' (keep remote), "
        "'union' (keep both sides, remove markers), 'markers' (keep conflict markers and commit merge).",
        False,
    ),
    (
        "--git-command-timeout-seconds",
        float,
        8.0,
        "Timeout (seconds) for git add/status/commit operations.",
        False,
    ),
    (
        "--git-pull-timeout-seconds",
        float,
        30.0,
        "Timeout (seconds) for git pull (merge). Increase for slow networks or large repos.",
        False,
    ),
    (
        "--git-network-probe-timeout-seconds",
        float,
        2.0,
        "Timeout (seconds) for remote host network probe before pull. "
        "Used to decide whether to wait for network and skip pull/notify when offline.",
        False,
    ),
    (
        "--git-pull-offline-error-markers",
        str,
        [
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
        "Error markers treated as offline/network failures for git pull. "
        "Provide one or more markers to customize detection.",
        False,
    ),
    ("--git-push-timeout-seconds", float, 20.0, "Timeout (seconds) for git push.", False),
    (
        "--git-sync-retry-window-seconds",
        float,
        120.0,
        "How long background git sync retries pull/push failures before giving up. Set 0 to disable retries.",
        False,
    ),
    (
        "--git-sync-retry-backoff-start-seconds",
        float,
        5.0,
        "Initial retry delay in seconds for background git sync retries.",
        False,
    ),
    (
        "--git-sync-retry-backoff-max-seconds",
        float,
        60.0,
        "Maximum retry delay cap in seconds for background git sync retries.",
        False,
    ),
]
