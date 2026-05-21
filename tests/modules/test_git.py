from __future__ import annotations

import os
import subprocess
import time
from dataclasses import replace
from datetime import datetime

import pytest
from watchdog.events import FileMovedEvent, FileOpenedEvent

import lucy_notes_manager.modules.git as git_mod
import lucy_notes_manager.modules.git.helpers as git_helpers
import lucy_notes_manager.modules.git.operations as git_ops
import lucy_notes_manager.modules.git.worker as git_worker
from lucy_notes_manager.modules.abstract_module import Context, System
from lucy_notes_manager.modules.git import Git, _RepoBatch
from lucy_notes_manager.modules.git.types import (
    GitPolicy,
    MergeAutoresolveMode,
    parse_merge_autoresolve_mode,
)

_NOTIFY_CFG = {
    "sys_notification_provider": "termuxapi",
    "sys_notification_min_interval_seconds": 10.0,
    "sys_notification_error_backoff_base_seconds": 10.0,
    "sys_notification_error_backoff_max_seconds": 1800.0,
    "sys_notification_error_burst_limit": 3,
    "sys_notification_error_burst_window_seconds": 600.0,
}


@pytest.fixture
def git_module():
    return Git()


def _mk_batch(**overrides) -> _RepoBatch:
    policy = GitPolicy(
        auto_merge_on_push=True,
        auto_set_upstream=True,
        autoresolve_mode=MergeAutoresolveMode.UNION,
        network_probe_timeout_seconds=1.0,
        pull_offline_error_markers=(),
    )
    if "policy" in overrides:
        policy = overrides.pop("policy")
    if "auto_merge_on_push" in overrides:
        policy = replace(
            policy,
            auto_merge_on_push=bool(overrides.pop("auto_merge_on_push")),
        )
    if "auto_set_upstream" in overrides:
        policy = replace(
            policy,
            auto_set_upstream=bool(overrides.pop("auto_set_upstream")),
        )
    if "autoresolve_mode" in overrides:
        policy = replace(
            policy,
            autoresolve_mode=parse_merge_autoresolve_mode(
                str(overrides.pop("autoresolve_mode"))
            ),
        )
    if "network_probe_timeout_seconds" in overrides:
        policy = replace(
            policy,
            network_probe_timeout_seconds=float(
                overrides.pop("network_probe_timeout_seconds")
            ),
        )
    if "pull_offline_error_markers" in overrides:
        policy = replace(
            policy,
            pull_offline_error_markers=tuple(overrides.pop("pull_offline_error_markers")),
        )

    values = {
        "repo_root": "/repo",
        "event_type": "modified",
        "hinted_paths": ["/repo/note.md"],
        "wants_pull": False,
        "base_message": "Auto",
        "add_timestamp_to_message": False,
        "timestamp_format": "%Y",
        "environment": {},
        "git_timeout_seconds": 5.0,
        "pull_timeout_seconds": 6.0,
        "push_timeout_seconds": 7.0,
        "sync_retry_window_seconds": 10.0,
        "sync_retry_backoff_start_seconds": 1.0,
        "sync_retry_backoff_max_seconds": 4.0,
        "notify_provider": "termuxapi",
        "notify_min_interval_sec": 10.0,
        "notify_error_backoff_base_seconds": 10.0,
        "notify_error_backoff_max_seconds": 1800.0,
        "notify_error_burst_limit": 3,
        "notify_error_burst_window_seconds": 600.0,
        "policy": policy,
    }
    values.update(overrides)
    return _RepoBatch(**values)


def test_notify_config_from_batch_includes_rare_notification_settings():
    batch = _mk_batch(
        notify_error_backoff_base_seconds=2.0,
        notify_error_backoff_max_seconds=30.0,
        notify_error_burst_limit=4,
        notify_error_burst_window_seconds=90.0,
    )

    assert git_worker._notify_config_from_batch(batch) == {
        "sys_notification_provider": "termuxapi",
        "sys_notification_min_interval_seconds": 10.0,
        "sys_notification_error_backoff_base_seconds": 2.0,
        "sys_notification_error_backoff_max_seconds": 30.0,
        "sys_notification_error_burst_limit": 4,
        "sys_notification_error_burst_window_seconds": 90.0,
    }


def test_parse_porcelain_paths_handles_regular_and_renamed(git_module):
    text = " M a.txt\nR  old.md -> new.md\n?? x.py\n"
    assert git_helpers.parse_porcelain_paths(text) == ["a.txt", "new.md", "x.py"]


def test_push_rejected_needs_pull_detects_common_messages(git_module):
    assert git_helpers.push_rejected_needs_pull("non-fast-forward update rejected")
    assert not git_helpers.push_rejected_needs_pull("everything up-to-date")


def test_union_resolve_text_merges_conflict_content(git_module):
    merged = git_helpers.union_resolve_text(
        "A\n<<<<<<< ours\none\n=======\ntwo\n>>>>>>> theirs\nB\n"
    )
    assert merged == "A\none\ntwo\nB\n"


def test_format_path_for_commit_message_decodes_git_quoted_path():
    formatted = git_helpers.format_path_for_commit_message('"\\342\\206\\222 now.md"')
    assert formatted == "now.md"


def test_auto_resolve_markers_stages_conflicts_and_commits(git_module, monkeypatch):
    calls: list[list[str]] = []

    def _run_git(_self, _repo_root, arguments, _environment, timeout_seconds):
        _ = timeout_seconds
        calls.append(list(arguments))
        if arguments == ["diff", "--name-only", "-z", "--diff-filter=U"]:
            return subprocess.CompletedProcess(
                args=["git"] + arguments,
                returncode=0,
                stdout="now.md\x00next.md\x00",
                stderr="",
            )
        if arguments[:2] == ["add", "-A"]:
            return subprocess.CompletedProcess(
                args=["git"] + arguments,
                returncode=0,
                stdout="",
                stderr="",
            )
        if arguments == ["commit", "--no-edit"]:
            return subprocess.CompletedProcess(
                args=["git"] + arguments,
                returncode=0,
                stdout="[main] merge commit",
                stderr="",
            )
        raise AssertionError(f"Unexpected command: {arguments}")

    monkeypatch.setattr(git_ops, "run_git", _run_git)

    resolved = git_ops.auto_resolve_merge_conflicts(
        git_module,
        repo_root="/repo",
        environment={},
        timeout_seconds=5.0,
        autoresolve_mode="markers",
    )

    assert resolved is True
    assert ["add", "-A", "--", "now.md"] in calls
    assert ["add", "-A", "--", "next.md"] in calls
    assert ["commit", "--no-edit"] in calls


def test_conflicted_files_parses_nul_separated_paths(git_module, monkeypatch):
    def _run_git(_self, _repo_root, arguments, _environment, timeout_seconds):
        _ = timeout_seconds
        if arguments == ["diff", "--name-only", "-z", "--diff-filter=U"]:
            return subprocess.CompletedProcess(
                args=["git"] + arguments,
                returncode=0,
                stdout='→ now.md\x00folder/"quoted".md\x00',
                stderr="",
            )
        raise AssertionError(f"Unexpected command: {arguments}")

    monkeypatch.setattr(git_ops, "run_git", _run_git)

    files = git_ops.conflicted_files(
        git_module,
        repo_root="/repo",
        environment={},
        timeout_seconds=5.0,
    )

    assert files == ['→ now.md', 'folder/"quoted".md']


def test_git_environment_forces_c_locale_and_disables_prompt(git_module, monkeypatch):
    monkeypatch.setenv("LANG", "ru_RU.UTF-8")
    monkeypatch.setenv("LC_ALL", "ru_RU.UTF-8")
    monkeypatch.setenv("LANGUAGE", "ru_RU:en_US")

    environment = git_ops.git_environment(git_module, {})

    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["LC_ALL"] == "C"
    assert environment["LANG"] == "C"
    assert environment["LANGUAGE"] == "C"


def test_build_commit_message_includes_event_summary_and_names(git_module, monkeypatch):
    class _FakeDateTime:
        @classmethod
        def now(cls):
            return datetime(2026, 4, 21, 12, 0, 0)

    monkeypatch.setattr(git_mod, "datetime", _FakeDateTime)

    batch = _mk_batch(
        add_timestamp_to_message=True,
        event_type="created",
        hinted_paths=["/repo/hinted.md"],
    )

    message = git_module._build_commit_message(batch, ["/repo/a.md", "/repo/b.md"])
    assert message.startswith("Auto: created")
    assert "a.md, b.md" in message
    assert message.endswith("[2026]")


def test_build_commit_message_sanitizes_git_escaped_file_names(git_module):
    batch = _mk_batch(event_type="modified")
    message = git_module._build_commit_message(batch, ['"\\342\\206\\222 now.md"'])
    assert message == "Auto: modified now.md"


def test_opened_processes_pull_when_repo_exists(git_module, monkeypatch):
    recorded = {}
    monkeypatch.setattr(git_mod, "find_parent_with", lambda _p, _m: "/repo")
    monkeypatch.setattr(
        git_mod,
        "process_event",
        lambda _self, **kwargs: recorded.update(kwargs),
    )

    ctx = Context(path="/repo/note.md", config={"git_pull_on_opened_event": True}, arg_lines={})
    system = System(
        event=FileOpenedEvent("/repo/note.md"),
        global_template=[],
        modules=[git_module],
    )
    git_module.opened(ctx, system)

    assert recorded["repo_root"] == "/repo"
    assert recorded["event_type"] == "opened"
    assert recorded["wants_pull"] is True
    assert recorded["paths"] == ["/repo/note.md"]
    assert recorded["run_in_background"] is True


def test_opened_skips_when_pull_on_opened_disabled(git_module, monkeypatch):
    recorded = {"called": False}
    monkeypatch.setattr(git_mod, "find_parent_with", lambda _p, _m: "/repo")
    monkeypatch.setattr(
        git_mod,
        "process_event",
        lambda *_args, **_kwargs: recorded.__setitem__("called", True),
    )

    ctx = Context(path="/repo/note.md", config={"git_pull_on_opened_event": False}, arg_lines={})
    system = System(
        event=FileOpenedEvent("/repo/note.md"),
        global_template=[],
        modules=[git_module],
    )
    git_module.opened(ctx, system)

    assert recorded["called"] is False


def test_opened_runs_sync_in_oneshot_mode(git_module, monkeypatch):
    recorded = {}
    monkeypatch.setattr(git_mod, "find_parent_with", lambda _p, _m: "/repo")
    monkeypatch.setattr(
        git_mod,
        "process_event",
        lambda _self, **kwargs: recorded.update(kwargs),
    )

    ctx = Context(
        path="/repo/note.md",
        config={"git_pull_on_opened_event": True},
        arg_lines={},
    )
    system = System(
        event=FileOpenedEvent("/repo/note.md"),
        global_template=[],
        modules=[git_module],
        run_mode="oneshot",
    )
    git_module.opened(ctx, system)

    assert recorded["run_in_background"] is False


def test_handle_moved_uses_src_and_dest_paths_for_hints(git_module, monkeypatch):
    recorded = {}
    monkeypatch.setattr(git_mod, "find_parent_with", lambda _p, _m: "/repo")
    monkeypatch.setattr(
        git_mod,
        "process_event",
        lambda _self, **kwargs: recorded.update(kwargs),
    )

    event = FileMovedEvent("/repo/old.md", "/repo/new.md")
    ctx = Context(path="/repo/new.md", config={}, arg_lines={})
    system = System(event=event, global_template=[], modules=[git_module])

    git_module._handle(ctx, system, "moved")

    assert recorded["event_type"] == "moved"
    assert recorded["paths"] == ["/repo/old.md", "/repo/new.md"]
    assert recorded["run_in_background"] is True


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("git@github.com:owner/repo.git", ("github.com", 22)),
        ("https://github.com/owner/repo.git", ("github.com", 443)),
        ("ssh://git@example.com:2222/repo.git", ("example.com", 2222)),
        ("sftp://example.com/repo.git", ("example.com", 22)),
        ("file:///tmp/repo.git", (None, None)),
        (r"C:\notes\repo.git", (None, None)),
        (r"\\server\share\repo.git", (None, None)),
    ],
)
def test_parse_remote_endpoint_handles_common_git_remote_shapes(
    remote: str, expected: tuple[str | None, int | None]
):
    assert git_ops.parse_remote_endpoint(remote) == expected


def test_safe_pull_merge_waits_for_network_and_notifies_when_upstream(git_module, monkeypatch):
    notifications: list[dict] = []

    monkeypatch.setattr(git_ops, "safe_notify", lambda **kwargs: notifications.append(kwargs))
    monkeypatch.setattr(git_ops, "has_upstream", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(git_ops, "upstream_remote_name", lambda *_args, **_kwargs: "origin")
    monkeypatch.setattr(git_ops, "remote_is_reachable", lambda **_kwargs: False)
    monkeypatch.setattr(
        git_ops,
        "run_git",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("git pull should not run while remote is offline")
        ),
    )

    pulled = git_ops.safe_pull_merge(
        git_module,
        repo_root="/repo",
        environment={},
        pull_timeout_seconds=10.0,
        operation_timeout_seconds=5.0,
        autoresolve_mode="union",
        notify_config=_NOTIFY_CFG,
        auto_set_upstream=True,
    )

    assert pulled is False
    assert [item["name"] for item in notifications] == ["git-network:/repo"]


def test_safe_pull_merge_skips_remote_branch_lookup_and_notifies_when_offline(
    git_module, monkeypatch
):
    notifications: list[dict] = []

    monkeypatch.setattr(git_ops, "safe_notify", lambda **kwargs: notifications.append(kwargs))
    monkeypatch.setattr(git_ops, "has_upstream", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(git_ops, "current_branch", lambda *_args, **_kwargs: "main")
    monkeypatch.setattr(git_ops, "pick_remote", lambda *_args, **_kwargs: "origin")
    monkeypatch.setattr(git_ops, "remote_is_reachable", lambda **_kwargs: False)
    monkeypatch.setattr(
        git_ops,
        "remote_branch_exists",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("remote branch lookup should not run while remote is offline")
        ),
    )

    pulled = git_ops.safe_pull_merge(
        git_module,
        repo_root="/repo",
        environment={},
        pull_timeout_seconds=10.0,
        operation_timeout_seconds=5.0,
        autoresolve_mode="union",
        notify_config=_NOTIFY_CFG,
        auto_set_upstream=True,
    )

    assert pulled is False
    assert [item["name"] for item in notifications] == ["git-network:/repo"]


def test_safe_pull_merge_timeout_while_offline_notifies_waiting_state(git_module, monkeypatch):
    notifications: list[dict] = []
    reachability_calls = {"count": 0}

    monkeypatch.setattr(git_ops, "safe_notify", lambda **kwargs: notifications.append(kwargs))
    monkeypatch.setattr(git_ops, "has_upstream", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(git_ops, "upstream_remote_name", lambda *_args, **_kwargs: "origin")

    def _remote_is_reachable(**_kwargs):
        reachability_calls["count"] += 1
        return reachability_calls["count"] == 1

    monkeypatch.setattr(git_ops, "remote_is_reachable", _remote_is_reachable)
    monkeypatch.setattr(
        git_ops,
        "run_git",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(cmd=["git", "pull"], timeout=10.0)
        ),
    )

    pulled = git_ops.safe_pull_merge(
        git_module,
        repo_root="/repo",
        environment={},
        pull_timeout_seconds=10.0,
        operation_timeout_seconds=5.0,
        autoresolve_mode="union",
        notify_config=_NOTIFY_CFG,
        auto_set_upstream=True,
    )

    assert pulled is False
    assert [item["name"] for item in notifications] == ["git-network:/repo"]


def test_safe_pull_merge_offline_marker_notifies_waiting_state(git_module, monkeypatch):
    notifications: list[dict] = []

    monkeypatch.setattr(git_ops, "safe_notify", lambda **kwargs: notifications.append(kwargs))
    monkeypatch.setattr(git_ops, "has_upstream", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(git_ops, "upstream_remote_name", lambda *_args, **_kwargs: "origin")
    monkeypatch.setattr(git_ops, "remote_is_reachable", lambda **_kwargs: True)
    monkeypatch.setattr(
        git_ops,
        "run_git",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["git", "pull"],
            returncode=1,
            stdout="",
            stderr="ssh: connect to host example.com port 22: Connection timed out",
        ),
    )

    pulled = git_ops.safe_pull_merge(
        git_module,
        repo_root="/repo",
        environment={},
        pull_timeout_seconds=10.0,
        operation_timeout_seconds=5.0,
        autoresolve_mode="union",
        notify_config=_NOTIFY_CFG,
        auto_set_upstream=True,
        pull_offline_error_markers=["connection timed out"],
    )

    assert pulled is False
    assert [item["name"] for item in notifications] == ["git-network:/repo"]


def test_run_git_retries_after_stale_index_lock(git_module, monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    git_dir = repo_root / ".git"
    git_dir.mkdir(parents=True)
    lock_path = git_dir / "index.lock"
    lock_path.write_text("", encoding="utf-8")

    stale_timestamp = time.time() - 3600.0
    os.utime(lock_path, (stale_timestamp, stale_timestamp))

    attempts = {"count": 0}

    class _FakeExecutor:
        def __init__(self, repo_root: str, environment: dict[str, str]):
            self.repo_root = repo_root
            self.environment = environment

        def run(
            self,
            arguments: list[str],
            timeout_seconds: float,
        ) -> subprocess.CompletedProcess[str]:
            _ = timeout_seconds
            attempts["count"] += 1
            if attempts["count"] == 1:
                return subprocess.CompletedProcess(
                    args=["git"] + arguments,
                    returncode=1,
                    stdout="",
                    stderr=(
                        "fatal: Unable to create '/repo/.git/index.lock': File exists.\n"
                        "Another git process seems to be running."
                    ),
                )
            return subprocess.CompletedProcess(
                args=["git"] + arguments,
                returncode=0,
                stdout="Already up to date.",
                stderr="",
            )

    monkeypatch.setattr(git_ops, "GitExecutor", _FakeExecutor)

    result = git_ops.run_git(
        git_module,
        repo_root=str(repo_root),
        arguments=["pull", "--no-rebase"],
        environment={},
        timeout_seconds=10.0,
    )

    assert result.returncode == 0
    assert attempts["count"] == 2
    assert not lock_path.exists()


def test_run_git_removes_recent_index_lock(git_module, monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    git_dir = repo_root / ".git"
    git_dir.mkdir(parents=True)
    lock_path = git_dir / "index.lock"
    lock_path.write_text("", encoding="utf-8")

    attempts = {"count": 0}

    class _FakeExecutor:
        def __init__(self, repo_root: str, environment: dict[str, str]):
            self.repo_root = repo_root
            self.environment = environment

        def run(
            self,
            arguments: list[str],
            timeout_seconds: float,
        ) -> subprocess.CompletedProcess[str]:
            _ = timeout_seconds
            attempts["count"] += 1
            return subprocess.CompletedProcess(
                args=["git"] + arguments,
                returncode=1,
                stdout="",
                stderr=(
                    "fatal: Unable to create '/repo/.git/index.lock': File exists.\n"
                    "Another git process seems to be running."
                ),
            )

    monkeypatch.setattr(git_ops, "GitExecutor", _FakeExecutor)

    result = git_ops.run_git(
        git_module,
        repo_root=str(repo_root),
        arguments=["pull", "--no-rebase"],
        environment={},
        timeout_seconds=10.0,
    )

    assert result.returncode != 0
    assert attempts["count"] == 2
    assert not lock_path.exists()


def test_remote_is_reachable_dns_resolution_timeout_uses_probe_timeout(
    git_module, monkeypatch
):
    resolve_call: dict[str, object] = {}

    monkeypatch.setattr(
        git_ops,
        "remote_url",
        lambda *_args, **_kwargs: "https://example.com/owner/repo.git",
    )

    def _resolve_address_infos(host_name: str, port_number: int, timeout_seconds: float):
        resolve_call["host_name"] = host_name
        resolve_call["port_number"] = port_number
        resolve_call["timeout_seconds"] = timeout_seconds
        return [], True

    monkeypatch.setattr(git_ops, "_resolve_address_infos", _resolve_address_infos)

    reachable = git_ops.remote_is_reachable(
        git_module,
        repo_root="/repo",
        remote_name="origin",
        environment={},
        timeout_seconds=8.0,
        network_probe_timeout_seconds=1.25,
    )

    assert reachable is False
    assert resolve_call == {
        "host_name": "example.com",
        "port_number": 443,
        "timeout_seconds": 1.25,
    }


def test_safe_pull_merge_conflict_abort_timeout_does_not_raise(git_module, monkeypatch):
    notifications: list[dict] = []

    monkeypatch.setattr(git_ops, "safe_notify", lambda **kwargs: notifications.append(kwargs))
    monkeypatch.setattr(git_ops, "has_upstream", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(git_ops, "upstream_remote_name", lambda *_args, **_kwargs: "origin")
    monkeypatch.setattr(git_ops, "remote_is_reachable", lambda **_kwargs: True)
    monkeypatch.setattr(git_ops, "merge_in_progress", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(git_ops, "auto_resolve_merge_conflicts", lambda *_args, **_kwargs: False)

    def _run_git(_self, _repo_root, arguments, _environment, timeout_seconds):
        _ = timeout_seconds
        if arguments and arguments[0] == "pull":
            return subprocess.CompletedProcess(
                args=["git"] + arguments,
                returncode=1,
                stdout="",
                stderr="pull conflict",
            )
        if arguments[:2] == ["merge", "--abort"]:
            raise subprocess.TimeoutExpired(
                cmd=["git", "merge", "--abort"],
                timeout=5.0,
            )
        return subprocess.CompletedProcess(
            args=["git"] + arguments,
            returncode=0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(git_ops, "run_git", _run_git)

    pulled = git_ops.safe_pull_merge(
        git_module,
        repo_root="/repo",
        environment={},
        pull_timeout_seconds=10.0,
        operation_timeout_seconds=5.0,
        autoresolve_mode="union",
        notify_config=_NOTIFY_CFG,
        auto_set_upstream=True,
    )

    assert pulled is False
    assert any(item["name"] == "pull-conflict:/repo" for item in notifications)


def test_safe_pull_merge_conflict_union_falls_back_to_markers(git_module, monkeypatch):
    notifications: list[dict] = []
    resolve_modes: list[str] = []

    monkeypatch.setattr(git_ops, "safe_notify", lambda **kwargs: notifications.append(kwargs))
    monkeypatch.setattr(git_ops, "has_upstream", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(git_ops, "upstream_remote_name", lambda *_args, **_kwargs: "origin")
    monkeypatch.setattr(git_ops, "remote_is_reachable", lambda **_kwargs: True)
    monkeypatch.setattr(git_ops, "merge_in_progress", lambda *_args, **_kwargs: True)

    def _auto_resolve_merge_conflicts(
        _self,
        _repo_root,
        _environment,
        _timeout_seconds,
        autoresolve_mode,
    ):
        resolve_modes.append(autoresolve_mode)
        return autoresolve_mode == "markers"

    monkeypatch.setattr(git_ops, "auto_resolve_merge_conflicts", _auto_resolve_merge_conflicts)

    def _run_git(_self, _repo_root, arguments, _environment, timeout_seconds):
        _ = timeout_seconds
        if arguments and arguments[0] == "pull":
            return subprocess.CompletedProcess(
                args=["git"] + arguments,
                returncode=1,
                stdout="",
                stderr="pull conflict",
            )
        if arguments[:2] == ["merge", "--abort"]:
            raise AssertionError("merge abort must not run when markers fallback succeeds")
        return subprocess.CompletedProcess(
            args=["git"] + arguments,
            returncode=0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(git_ops, "run_git", _run_git)

    pulled = git_ops.safe_pull_merge(
        git_module,
        repo_root="/repo",
        environment={},
        pull_timeout_seconds=10.0,
        operation_timeout_seconds=5.0,
        autoresolve_mode="union",
        notify_config=_NOTIFY_CFG,
        auto_set_upstream=True,
    )

    assert pulled is True
    assert resolve_modes == ["union", "markers"]
    assert not any(item["name"] == "pull-conflict:/repo" for item in notifications)


def test_safe_pull_merge_conflict_markers_mode_commits_and_returns_true(
    git_module, monkeypatch
):
    notifications: list[dict] = []

    monkeypatch.setattr(git_ops, "safe_notify", lambda **kwargs: notifications.append(kwargs))
    monkeypatch.setattr(git_ops, "has_upstream", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(git_ops, "upstream_remote_name", lambda *_args, **_kwargs: "origin")
    monkeypatch.setattr(git_ops, "remote_is_reachable", lambda **_kwargs: True)
    monkeypatch.setattr(git_ops, "merge_in_progress", lambda *_args, **_kwargs: True)

    def _run_git(_self, _repo_root, arguments, _environment, timeout_seconds):
        _ = timeout_seconds
        if arguments and arguments[0] == "pull":
            return subprocess.CompletedProcess(
                args=["git"] + arguments,
                returncode=1,
                stdout="",
                stderr="pull conflict",
            )
        if arguments == ["diff", "--name-only", "-z", "--diff-filter=U"]:
            return subprocess.CompletedProcess(
                args=["git"] + arguments,
                returncode=0,
                stdout="now.md\x00",
                stderr="",
            )
        if arguments[:2] == ["add", "-A"]:
            return subprocess.CompletedProcess(
                args=["git"] + arguments,
                returncode=0,
                stdout="",
                stderr="",
            )
        if arguments == ["commit", "--no-edit"]:
            return subprocess.CompletedProcess(
                args=["git"] + arguments,
                returncode=0,
                stdout="[main] merge commit",
                stderr="",
            )
        raise AssertionError(f"Unexpected command: {arguments}")

    monkeypatch.setattr(git_ops, "run_git", _run_git)

    pulled = git_ops.safe_pull_merge(
        git_module,
        repo_root="/repo",
        environment={},
        pull_timeout_seconds=10.0,
        operation_timeout_seconds=5.0,
        autoresolve_mode="markers",
        notify_config=_NOTIFY_CFG,
        auto_set_upstream=True,
    )

    assert pulled is True
    assert not any(item["name"] == "pull-conflict:/repo" for item in notifications)


def test_ensure_merge_state_clean_handles_merge_abort_timeout(git_module, monkeypatch):
    notifications: list[dict] = []

    monkeypatch.setattr(git_worker, "safe_notify", lambda **kwargs: notifications.append(kwargs))
    monkeypatch.setattr(git_worker, "merge_in_progress", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        git_worker,
        "resolve_merge_conflicts_with_fallback",
        lambda *_args, **_kwargs: False,
    )

    def _run_git(_self, _repo_root, arguments, _environment, timeout_seconds):
        _ = timeout_seconds
        if arguments[:2] == ["merge", "--abort"]:
            raise subprocess.TimeoutExpired(
                cmd=["git", "merge", "--abort"],
                timeout=5.0,
            )
        raise AssertionError(f"Unexpected command: {arguments}")

    monkeypatch.setattr(git_worker, "run_git", _run_git)

    cleaned = git_worker._ensure_merge_state_clean(
        git_module,
        repo_root="/repo",
        environment={},
        git_timeout_seconds=5.0,
        autoresolve_mode="union",
        notify_config=_NOTIFY_CFG,
    )

    assert cleaned is False
    assert any(item["name"] == "merge-stuck:/repo" for item in notifications)


def test_pull_only_batch_runs_pull_and_skips_add_commit_push(git_module, monkeypatch):
    pull_calls: list[tuple[str, float, float]] = []

    monkeypatch.setattr(git_worker, "merge_in_progress", lambda *_args, **_kwargs: False)

    def _safe_pull_merge(
        _self,
        repo_root,
        _environment,
        pull_timeout_seconds,
        operation_timeout_seconds,
        **_kwargs,
    ):
        pull_calls.append((repo_root, pull_timeout_seconds, operation_timeout_seconds))
        return True

    monkeypatch.setattr(git_worker, "safe_pull_merge", _safe_pull_merge)
    monkeypatch.setattr(
        git_worker,
        "run_git",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("pull-only opened event must not run add/commit/push")
        ),
    )

    batch = _mk_batch(
        event_type="opened",
        wants_pull=True,
    )

    git_worker.process_batch(git_module, batch)
    assert pull_calls == [("/repo", 6.0, 5.0)]


def test_process_event_builds_batch_and_calls_process_batch(git_module, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        git_worker,
        "git_environment",
        lambda *_args, **_kwargs: {"X": "1"},
    )

    def _process_batch(_self, batch):
        captured["batch"] = batch

    monkeypatch.setattr(git_worker, "process_batch", _process_batch)

    git_worker.process_event(
        self=git_module,
        repo_root="/repo",
        event_type="modified",
        paths=["/repo/note.md"],
        config_snapshot={
            "git_commit_message": "Auto",
            "git_commit_message_timestamp": False,
            "git_commit_message_timestamp_format": "%Y",
            "git_command_timeout_seconds": 5.0,
            "git_pull_timeout_seconds": 6.0,
            "git_push_timeout_seconds": 7.0,
            "git_sync_retry_window_seconds": 120.0,
            "git_sync_retry_backoff_start_seconds": 5.0,
            "git_sync_retry_backoff_max_seconds": 60.0,
            "git_network_probe_timeout_seconds": 1.0,
            "git_pull_offline_error_markers": [],
            "sys_notification_provider": "termuxapi",
            "sys_notification_min_interval_seconds": 10.0,
            "sys_notification_error_backoff_base_seconds": 10.0,
            "sys_notification_error_backoff_max_seconds": 1800.0,
            "sys_notification_error_burst_limit": 3,
            "sys_notification_error_burst_window_seconds": 600.0,
            "git_push_auto_merge": True,
            "git_upstream_auto_set": True,
            "git_merge_autoresolve": "union",
        },
        wants_pull=False,
        run_in_background=False,
    )

    batch = captured["batch"]
    assert batch.repo_root == "/repo"
    assert batch.event_type == "modified"
    assert batch.hinted_paths == ["/repo/note.md"]


def test_process_event_runs_repo_operations_concurrently(git_module, monkeypatch):
    state = {
        "inflight": 0,
        "max_inflight": 0,
        "calls": 0,
    }

    monkeypatch.setattr(git_worker, "_build_batch", lambda **_kwargs: object())

    def _process_batch(_self, _batch):
        state["calls"] += 1
        state["inflight"] += 1
        state["max_inflight"] = max(state["max_inflight"], state["inflight"])
        time.sleep(0.05)
        state["inflight"] -= 1
        return True

    monkeypatch.setattr(git_worker, "process_batch", _process_batch)
    repo_root = f"/repo-queue-{time.time_ns()}"
    config_snapshot = {
        "git_sync_retry_window_seconds": 0.0,
        "git_sync_retry_backoff_start_seconds": 0.01,
        "git_sync_retry_backoff_max_seconds": 0.01,
    }

    git_worker.process_event(
        self=git_module,
        repo_root=repo_root,
        event_type="modified",
        paths=[f"{repo_root}/a.md"],
        config_snapshot=config_snapshot,
        wants_pull=False,
        run_in_background=True,
    )
    git_worker.process_event(
        self=git_module,
        repo_root=repo_root,
        event_type="modified",
        paths=[f"{repo_root}/b.md"],
        config_snapshot=config_snapshot,
        wants_pull=False,
        run_in_background=True,
    )

    deadline = time.monotonic() + 2.0
    while state["calls"] < 2 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert state["calls"] == 2
    assert state["max_inflight"] >= 2


def test_retry_window_retries_with_backoff_until_success(git_module, monkeypatch):
    attempts = {"count": 0}
    clock = {"t": 0.0}
    sleeps: list[float] = []

    def _process_once(*_args, **_kwargs):
        attempts["count"] += 1
        return attempts["count"] >= 3

    monkeypatch.setattr(git_worker, "_process_event_once", _process_once)
    monkeypatch.setattr(git_worker.time, "monotonic", lambda: clock["t"])

    def _sleep(seconds: float):
        sleeps.append(seconds)
        clock["t"] += seconds

    monkeypatch.setattr(git_worker.time, "sleep", _sleep)

    git_worker._run_event_with_retry_window(
        self=git_module,
        repo_root="/repo",
        event_type="modified",
        paths=["/repo/note.md"],
        config_snapshot={
            "git_sync_retry_window_seconds": 30.0,
            "git_sync_retry_backoff_start_seconds": 1.0,
            "git_sync_retry_backoff_max_seconds": 4.0,
        },
        wants_pull=False,
    )

    assert attempts["count"] == 3
    assert sleeps == [1.0, 2.0]


def test_attempt_push_with_retry_second_push_timeout_notifies_once(
    git_module, monkeypatch
):
    notifications: list[dict] = []
    push_attempts = {"count": 0}

    monkeypatch.setattr(git_worker, "safe_notify", lambda **kwargs: notifications.append(kwargs))
    monkeypatch.setattr(git_worker, "safe_pull_merge", lambda *_args, **_kwargs: True)

    def _run_git(_self, _repo_root, arguments, _environment, timeout_seconds):
        _ = timeout_seconds
        if arguments != ["push"]:
            raise AssertionError(f"Unexpected command: {arguments}")

        push_attempts["count"] += 1
        if push_attempts["count"] == 1:
            return subprocess.CompletedProcess(
                args=["git", "push"],
                returncode=1,
                stdout="",
                stderr="non-fast-forward",
            )
        raise subprocess.TimeoutExpired(cmd=["git", "push"], timeout=7.0)

    monkeypatch.setattr(git_worker, "run_git", _run_git)

    batch = _mk_batch(auto_merge_on_push=True)

    git_worker._attempt_push_with_retry(
        self=git_module,
        batch=batch,
        repo_root="/repo",
        environment={},
        pull_timeout_seconds=6.0,
        push_timeout_seconds=7.0,
        git_timeout_seconds=5.0,
        notify_config=_NOTIFY_CFG,
    )

    assert [item["name"] for item in notifications] == ["git-network:/repo"]


def test_attempt_push_with_retry_reports_second_push_error(git_module, monkeypatch):
    notifications: list[dict] = []
    push_attempts = {"count": 0}

    monkeypatch.setattr(git_worker, "safe_notify", lambda **kwargs: notifications.append(kwargs))
    monkeypatch.setattr(git_worker, "safe_pull_merge", lambda *_args, **_kwargs: True)

    def _run_git(_self, _repo_root, arguments, _environment, timeout_seconds):
        _ = timeout_seconds
        if arguments != ["push"]:
            raise AssertionError(f"Unexpected command: {arguments}")

        push_attempts["count"] += 1
        if push_attempts["count"] == 1:
            return subprocess.CompletedProcess(
                args=["git", "push"],
                returncode=1,
                stdout="",
                stderr="non-fast-forward",
            )
        return subprocess.CompletedProcess(
            args=["git", "push"],
            returncode=1,
            stdout="",
            stderr="second push failed",
        )

    monkeypatch.setattr(git_worker, "run_git", _run_git)

    batch = _mk_batch(auto_merge_on_push=True)

    git_worker._attempt_push_with_retry(
        self=git_module,
        batch=batch,
        repo_root="/repo",
        environment={},
        pull_timeout_seconds=6.0,
        push_timeout_seconds=7.0,
        git_timeout_seconds=5.0,
        notify_config=_NOTIFY_CFG,
    )

    push_fail_notifications = [item for item in notifications if item["name"] == "pushfail:/repo"]
    assert len(push_fail_notifications) == 1
    assert "second push failed" in push_fail_notifications[0]["message"]
    assert "non-fast-forward" not in push_fail_notifications[0]["message"]


def test_attempt_push_with_retry_retries_plain_push_error_before_notify(
    git_module, monkeypatch
):
    notifications: list[dict] = []
    push_attempts = {"count": 0}

    monkeypatch.setattr(git_worker, "safe_notify", lambda **kwargs: notifications.append(kwargs))
    monkeypatch.setattr(
        git_worker,
        "safe_pull_merge",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("safe_pull_merge must not run when auto_merge_on_push is disabled")
        ),
    )

    def _run_git(_self, _repo_root, arguments, _environment, timeout_seconds):
        _ = timeout_seconds
        if arguments != ["push"]:
            raise AssertionError(f"Unexpected command: {arguments}")

        push_attempts["count"] += 1
        if push_attempts["count"] == 1:
            return subprocess.CompletedProcess(
                args=["git", "push"],
                returncode=1,
                stdout="",
                stderr="Connection closed by 217.197.84.140 port 22",
            )
        return subprocess.CompletedProcess(
            args=["git", "push"],
            returncode=0,
            stdout="Everything up-to-date",
            stderr="",
        )

    monkeypatch.setattr(git_worker, "run_git", _run_git)

    batch = _mk_batch(auto_merge_on_push=False)

    git_worker._attempt_push_with_retry(
        self=git_module,
        batch=batch,
        repo_root="/repo",
        environment={},
        pull_timeout_seconds=6.0,
        push_timeout_seconds=7.0,
        git_timeout_seconds=5.0,
        notify_config=_NOTIFY_CFG,
    )

    assert push_attempts["count"] == 2
    assert notifications == []


def test_attempt_push_with_retry_retries_timeout_before_notify(git_module, monkeypatch):
    notifications: list[dict] = []
    push_attempts = {"count": 0}

    monkeypatch.setattr(git_worker, "safe_notify", lambda **kwargs: notifications.append(kwargs))
    monkeypatch.setattr(
        git_worker,
        "safe_pull_merge",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("safe_pull_merge must not run for timeout-first retry path")
        ),
    )

    def _run_git(_self, _repo_root, arguments, _environment, timeout_seconds):
        _ = timeout_seconds
        if arguments != ["push"]:
            raise AssertionError(f"Unexpected command: {arguments}")

        push_attempts["count"] += 1
        if push_attempts["count"] == 1:
            raise subprocess.TimeoutExpired(cmd=["git", "push"], timeout=7.0)
        return subprocess.CompletedProcess(
            args=["git", "push"],
            returncode=0,
            stdout="done",
            stderr="",
        )

    monkeypatch.setattr(git_worker, "run_git", _run_git)

    batch = _mk_batch(auto_merge_on_push=False)

    git_worker._attempt_push_with_retry(
        self=git_module,
        batch=batch,
        repo_root="/repo",
        environment={},
        pull_timeout_seconds=6.0,
        push_timeout_seconds=7.0,
        git_timeout_seconds=5.0,
        notify_config=_NOTIFY_CFG,
    )

    assert push_attempts["count"] == 2
    assert notifications == []
