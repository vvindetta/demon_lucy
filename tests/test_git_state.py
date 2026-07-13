from __future__ import annotations

import os

from demon_lucy.lib import git_state


def test_pid_is_alive_rejects_non_positive_pids() -> None:
    assert git_state.pid_is_alive(0, runtime_system="linux") is False
    assert git_state.pid_is_alive(-1, runtime_system="windows") is False


def test_pid_is_alive_detects_current_process() -> None:
    assert git_state.pid_is_alive(os.getpid(), runtime_system="linux") is True


def test_pid_is_alive_uses_windows_api_branch_without_os_kill(monkeypatch) -> None:
    monkeypatch.setattr(
        git_state.os,
        "kill",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("Windows pid checks must not use os.kill")
        ),
    )
    monkeypatch.setattr(git_state, "_windows_pid_is_alive", lambda pid: pid == 123)

    assert git_state.pid_is_alive(123, runtime_system="windows") is True
    assert git_state.pid_is_alive(124, runtime_system="windows") is False
