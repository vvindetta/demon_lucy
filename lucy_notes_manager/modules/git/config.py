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
        "--git-ssh-key-path",
        str,
        "",
        "Path to SSH private key for Git operations (no .pub). Used via GIT_SSH_COMMAND. Example: --git-ssh-key-path ~/.ssh/id_ed25519.",
        False,
    ),
    (
        "--git-pull-on-opened-event",
        bool,
        True,
        "Automatically run 'git pull --no-rebase' when a repo is opened. Never uses rebase or force.",
        False,
    ),
    (
        "--git-pull-interval-hours",
        float,
        0.0,
        "Run pull-only sync every N hours for active repos. Set 0 to disable (default).",
        False,
    ),
    (
        "--git-pull-cooldown-min-seconds",
        float,
        10.0,
        "Minimum cooldown (seconds) between auto-pulls triggered by opened.",
        False,
    ),
    (
        "--git-pull-cooldown-max-seconds",
        float,
        200.0,
        "Maximum cooldown cap (seconds). Cooldown progresses (doubles) if opened triggers too often.",
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
        "'none' (do not resolve), 'ours' (keep local), 'theirs' (keep remote), 'union' (keep both sides, remove markers).",
        False,
    ),
    (
        "--git-batch-debounce-seconds",
        float,
        0.8,
        "Debounce window in seconds: group file events and commit/push once after changes calm down.",
        False,
    ),
    (
        "--git-batch-max-seconds",
        float,
        8.0,
        "Maximum time to keep a non-pull batch pending while new events keep arriving. "
        "Set 0 to disable forced flush.",
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
        "--git-push-backoff-start-seconds",
        float,
        5.0,
        "Initial backoff (seconds) before retrying push after a failure.",
        False,
    ),
    (
        "--git-push-backoff-max-seconds",
        float,
        120.0,
        "Maximum backoff (seconds) cap for repeated push failures.",
        False,
    ),
]
