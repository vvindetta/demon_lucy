from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime

import pytest
from watchdog.events import FileMovedEvent, FileOpenedEvent

import lucy_notes_manager.modules.git as git_mod
import lucy_notes_manager.modules.git.helpers as git_helpers
import lucy_notes_manager.modules.git.operations as git_ops
import lucy_notes_manager.modules.git.worker as git_worker
from lucy_notes_manager.modules.abstract_module import Context, System
from lucy_notes_manager.modules.git import Git, _RepoBatch
from lucy_notes_manager.modules.git.worker import should_force_flush_batch


@pytest.fixture
def git_module(monkeypatch):
    class _DummyThread:
        def __init__(self, target=None, daemon=None):
            self.target = target
            self.daemon = daemon
            self.started = False

        def start(self):
            self.started = True

    monkeypatch.setattr(git_mod.threading, "Thread", _DummyThread)
    return Git()


def _mk_batch(**overrides) -> _RepoBatch:
    values = {
        "repo_root": "/repo",
        "base_message": "Auto",
        "add_timestamp_to_message": False,
        "timestamp_format": "%Y",
        "environment": {},
        "debounce_seconds": 0.5,
        "git_timeout_seconds": 5.0,
        "pull_timeout_seconds": 6.0,
        "push_timeout_seconds": 7.0,
        "backoff_start_seconds": 2.0,
        "backoff_max_seconds": 8.0,
        "pull_cooldown_min_seconds": 1.0,
        "pull_cooldown_max_seconds": 4.0,
        "max_batch_seconds": 8.0,
    }
    values.update(overrides)
    return _RepoBatch(**values)


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


def test_git_environment_forces_c_locale_and_disables_prompt(git_module, monkeypatch):
    monkeypatch.setenv("LANG", "ru_RU.UTF-8")
    monkeypatch.setenv("LC_ALL", "ru_RU.UTF-8")
    monkeypatch.setenv("LANGUAGE", "ru_RU:en_US")

    environment = git_ops.git_environment(git_module, {"git_key": ""})

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
        pull_timeout_seconds=5.0,
        push_timeout_seconds=5.0,
        event_types={"modified", "created"},
        hinted_paths={"/repo/hinted.md"},
    )

    msg = git_module._build_commit_message(batch, ["/repo/a.md", "/repo/b.md"])
    assert msg.startswith("Auto: ")
    assert "a.md, b.md" in msg
    assert msg.endswith("[2026]")


def test_pull_allowed_with_progression(git_module, monkeypatch):
    times = iter([0.0, 1.0, 30.0])
    monkeypatch.setattr(git_mod.time, "time", lambda: next(times))

    assert git_module._pull_allowed_with_progression("/r", 10.0, 40.0) is True
    assert git_module._pull_allowed_with_progression("/r", 10.0, 40.0) is False
    assert git_module._pull_allowed_with_progression("/r", 10.0, 40.0) is True


def test_register_push_failure_updates_backoff(git_module, monkeypatch):
    monkeypatch.setattr(git_mod.time, "time", lambda: 100.0)
    git_module._register_push_failure("/repo", backoff_start_seconds=5.0, backoff_max_seconds=20.0)

    assert git_module._push_backoff_seconds["/repo"] == 10.0
    assert git_module._push_next_allowed_at["/repo"] == 110.0


def test_update_periodic_pull_state_default_disabled(git_module):
    git_worker.update_periodic_pull_state(
        git_module,
        repo_root="/repo",
        config_snapshot={"git_auto_pull_every_hours": 0.0},
        now_timestamp=100.0,
    )
    assert "/repo" not in git_module._periodic_pull_next_at
    assert "/repo" not in git_module._periodic_pull_intervals_seconds
    assert "/repo" not in git_module._periodic_pull_configs


def test_update_periodic_pull_state_enables_and_emits_due_event(git_module):
    git_worker.update_periodic_pull_state(
        git_module,
        repo_root="/repo",
        config_snapshot={"git_auto_pull_every_hours": 2.0},
        now_timestamp=100.0,
    )

    assert git_module._periodic_pull_intervals_seconds["/repo"] == 7200.0
    assert git_module._periodic_pull_next_at["/repo"] == 7300.0

    assert (
        git_worker.collect_due_periodic_pull_events(git_module, now_timestamp=7299.0)
        == []
    )

    events = git_worker.collect_due_periodic_pull_events(git_module, now_timestamp=7300.0)
    assert events == [
        ("/repo", "scheduled_pull", [], {"git_auto_pull_every_hours": 2.0}, True)
    ]
    assert git_module._periodic_pull_next_at["/repo"] == 14500.0


def test_update_periodic_pull_state_turns_off_existing_schedule(git_module):
    git_worker.update_periodic_pull_state(
        git_module,
        repo_root="/repo",
        config_snapshot={"git_auto_pull_every_hours": 1.0},
        now_timestamp=100.0,
    )
    git_worker.update_periodic_pull_state(
        git_module,
        repo_root="/repo",
        config_snapshot={"git_auto_pull_every_hours": 0.0},
        now_timestamp=200.0,
    )

    assert "/repo" not in git_module._periodic_pull_next_at
    assert "/repo" not in git_module._periodic_pull_intervals_seconds
    assert "/repo" not in git_module._periodic_pull_configs


def test_scheduled_pull_batch_only_runs_pull(git_module, monkeypatch):
    pull_calls: list[tuple[str, float, float]] = []

    monkeypatch.setattr(git_worker, "merge_in_progress", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        git_module,
        "_pull_allowed_with_progression",
        lambda **_kwargs: True,
    )

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
            AssertionError("scheduled pull-only batches must not run add/commit/push")
        ),
    )

    batch = _mk_batch(
        wants_pull=True,
        event_types={"scheduled_pull"},
    )

    git_worker.process_batch(git_module, batch)
    assert pull_calls == [("/repo", 6.0, 5.0)]


def test_should_force_flush_batch_for_non_pull_batches():
    batch = _mk_batch(
        max_batch_seconds=5.0,
        first_event_at=10.0,
        event_types={"opened"},
    )

    assert should_force_flush_batch(batch, now_timestamp=20.0) is False

    batch.event_types = {"opened", "modified"}
    assert should_force_flush_batch(batch, now_timestamp=20.0) is True


def test_opened_enqueues_when_repo_exists(git_module, monkeypatch):
    recorded = {}
    monkeypatch.setattr(git_mod, "find_parent_with", lambda _p, _m: "/repo")
    monkeypatch.setattr(
        git_mod,
        "enqueue",
        lambda _self, **kwargs: recorded.update(kwargs),
    )

    ctx = Context(path="/repo/note.md", config={"git_auto_pull": True}, arg_lines={})
    system = System(
        event=FileOpenedEvent("/repo/note.md"),
        global_template=[],
        modules=[git_module],
    )
    git_module.opened(ctx, system)

    assert recorded["repo_root"] == "/repo"
    assert recorded["event_type"] == "opened"
    assert recorded["wants_pull"] is True


def test_handle_moved_uses_src_and_dest_paths_for_hints(git_module, monkeypatch):
    recorded = {}
    monkeypatch.setattr(git_mod, "find_parent_with", lambda _p, _m: "/repo")
    monkeypatch.setattr(
        git_mod,
        "enqueue",
        lambda _self, **kwargs: recorded.update(kwargs),
    )

    event = FileMovedEvent("/repo/old.md", "/repo/new.md")
    ctx = Context(path="/repo/new.md", config={}, arg_lines={})
    system = System(event=event, global_template=[], modules=[git_module])

    git_module._handle(ctx, system, "moved")
    assert recorded["paths"] == ["/repo/old.md", "/repo/new.md"]
    assert recorded["event_type"] == "moved"


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


def test_safe_pull_merge_waits_for_network_without_notify_when_upstream(git_module, monkeypatch):
    notifications: list[dict] = []

    monkeypatch.setattr(git_ops, "safe_notify", lambda **kwargs: notifications.append(kwargs))
    monkeypatch.setattr(git_ops, "has_upstream", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        git_ops,
        "upstream_remote_name",
        lambda *_args, **_kwargs: "origin",
    )
    monkeypatch.setattr(
        git_ops,
        "remote_is_reachable",
        lambda **_kwargs: False,
    )
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
        auto_set_upstream=True,
    )

    assert pulled is False
    assert notifications == []


def test_safe_pull_merge_skips_remote_branch_lookup_when_offline(git_module, monkeypatch):
    notifications: list[dict] = []

    monkeypatch.setattr(git_ops, "safe_notify", lambda **kwargs: notifications.append(kwargs))
    monkeypatch.setattr(git_ops, "has_upstream", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(git_ops, "current_branch", lambda *_args, **_kwargs: "main")
    monkeypatch.setattr(git_ops, "pick_remote", lambda *_args, **_kwargs: "origin")
    monkeypatch.setattr(
        git_ops,
        "remote_is_reachable",
        lambda **_kwargs: False,
    )
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
        auto_set_upstream=True,
    )

    assert pulled is False
    assert notifications == []


def test_safe_pull_merge_timeout_while_offline_skips_notify(git_module, monkeypatch):
    notifications: list[dict] = []
    reachability_calls = {"count": 0}

    monkeypatch.setattr(git_ops, "safe_notify", lambda **kwargs: notifications.append(kwargs))
    monkeypatch.setattr(git_ops, "has_upstream", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        git_ops,
        "upstream_remote_name",
        lambda *_args, **_kwargs: "origin",
    )

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
        auto_set_upstream=True,
    )

    assert pulled is False
    assert notifications == []


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


def test_run_git_does_not_remove_recent_index_lock(git_module, monkeypatch, tmp_path):
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
    assert attempts["count"] == 1
    assert lock_path.exists()


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
    monkeypatch.setattr(
        git_ops,
        "upstream_remote_name",
        lambda *_args, **_kwargs: "origin",
    )
    monkeypatch.setattr(
        git_ops,
        "remote_is_reachable",
        lambda **_kwargs: True,
    )
    monkeypatch.setattr(git_ops, "merge_in_progress", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        git_ops,
        "auto_resolve_merge_conflicts",
        lambda *_args, **_kwargs: False,
    )

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
        auto_set_upstream=True,
    )

    assert pulled is False
    assert any(item["name"] == "pull-conflict:/repo" for item in notifications)


def test_ensure_merge_state_clean_handles_merge_abort_timeout(git_module, monkeypatch):
    notifications: list[dict] = []

    monkeypatch.setattr(git_worker, "safe_notify", lambda **kwargs: notifications.append(kwargs))
    monkeypatch.setattr(
        git_worker,
        "merge_in_progress",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        git_worker,
        "auto_resolve_merge_conflicts",
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
    )

    assert cleaned is False
    assert any(item["name"] == "merge-stuck:/repo" for item in notifications)


def test_process_due_batches_continues_after_batch_exception(git_module, monkeypatch):
    notifications: list[dict] = []
    processed_repos: list[str] = []

    monkeypatch.setattr(git_worker, "safe_notify", lambda **kwargs: notifications.append(kwargs))

    def _process_batch(_self, batch):
        processed_repos.append(batch.repo_root)
        if batch.repo_root == "/repo1":
            raise RuntimeError("boom")

    monkeypatch.setattr(git_worker, "process_batch", _process_batch)

    def _batch(repo_root: str) -> _RepoBatch:
        return _mk_batch(repo_root=repo_root)

    git_worker._process_due_batches(git_module, [_batch("/repo1"), _batch("/repo2")])

    assert processed_repos == ["/repo1", "/repo2"]
    assert any(item["name"] == "batch-crash:/repo1" for item in notifications)


def test_attempt_push_with_retry_second_push_timeout_notifies_once(
    git_module, monkeypatch
):
    notifications: list[dict] = []
    register_calls: list[tuple[tuple, dict]] = []
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
    monkeypatch.setattr(
        git_module,
        "_register_push_failure",
        lambda *args, **kwargs: register_calls.append((args, kwargs)),
    )

    batch = _mk_batch(
        auto_merge_on_push=True,
        auto_set_upstream=True,
        autoresolve_mode="union",
        network_probe_timeout_seconds=1.0,
        pull_offline_error_markers=[],
    )

    git_worker._attempt_push_with_retry(
        self=git_module,
        batch=batch,
        repo_root="/repo",
        environment={},
        pull_timeout_seconds=6.0,
        push_timeout_seconds=7.0,
        git_timeout_seconds=5.0,
        backoff_start_seconds=2.0,
        backoff_max_seconds=8.0,
    )

    assert len(register_calls) == 1
    assert [item["name"] for item in notifications] == ["timeout:push:/repo"]


def test_attempt_push_with_retry_reports_second_push_error(git_module, monkeypatch):
    notifications: list[dict] = []
    register_calls: list[tuple[tuple, dict]] = []
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
    monkeypatch.setattr(
        git_module,
        "_register_push_failure",
        lambda *args, **kwargs: register_calls.append((args, kwargs)),
    )

    batch = _mk_batch(
        auto_merge_on_push=True,
        auto_set_upstream=True,
        autoresolve_mode="union",
        network_probe_timeout_seconds=1.0,
        pull_offline_error_markers=[],
    )

    git_worker._attempt_push_with_retry(
        self=git_module,
        batch=batch,
        repo_root="/repo",
        environment={},
        pull_timeout_seconds=6.0,
        push_timeout_seconds=7.0,
        git_timeout_seconds=5.0,
        backoff_start_seconds=2.0,
        backoff_max_seconds=8.0,
    )

    assert len(register_calls) == 1
    push_fail_notifications = [item for item in notifications if item["name"] == "pushfail:/repo"]
    assert len(push_fail_notifications) == 1
    assert "second push failed" in push_fail_notifications[0]["message"]
    assert "non-fast-forward" not in push_fail_notifications[0]["message"]


def test_attempt_push_with_retry_retries_plain_push_error_before_notify(
    git_module, monkeypatch
):
    notifications: list[dict] = []
    register_calls: list[tuple[tuple, dict]] = []
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
    monkeypatch.setattr(
        git_module,
        "_register_push_failure",
        lambda *args, **kwargs: register_calls.append((args, kwargs)),
    )

    batch = _mk_batch(
        auto_merge_on_push=False,
        auto_set_upstream=True,
        autoresolve_mode="union",
        network_probe_timeout_seconds=1.0,
        pull_offline_error_markers=[],
    )

    git_worker._attempt_push_with_retry(
        self=git_module,
        batch=batch,
        repo_root="/repo",
        environment={},
        pull_timeout_seconds=6.0,
        push_timeout_seconds=7.0,
        git_timeout_seconds=5.0,
        backoff_start_seconds=2.0,
        backoff_max_seconds=8.0,
    )

    assert push_attempts["count"] == 2
    assert notifications == []
    assert register_calls == []


def test_attempt_push_with_retry_retries_timeout_before_notify(git_module, monkeypatch):
    notifications: list[dict] = []
    register_calls: list[tuple[tuple, dict]] = []
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
    monkeypatch.setattr(
        git_module,
        "_register_push_failure",
        lambda *args, **kwargs: register_calls.append((args, kwargs)),
    )

    batch = _mk_batch(
        auto_merge_on_push=False,
        auto_set_upstream=True,
        autoresolve_mode="union",
        network_probe_timeout_seconds=1.0,
        pull_offline_error_markers=[],
    )

    git_worker._attempt_push_with_retry(
        self=git_module,
        batch=batch,
        repo_root="/repo",
        environment={},
        pull_timeout_seconds=6.0,
        push_timeout_seconds=7.0,
        git_timeout_seconds=5.0,
        backoff_start_seconds=2.0,
        backoff_max_seconds=8.0,
    )

    assert push_attempts["count"] == 2
    assert notifications == []
    assert register_calls == []
