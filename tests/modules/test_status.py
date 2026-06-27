from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from watchdog.events import FileModifiedEvent, FileOpenedEvent

import demon_lucy.modules.status as status_mod
from demon_lucy.modules.abstract_module import Context, System
from demon_lucy.modules.git.sync_marker import write_sync_success_timestamp
from demon_lucy.modules.status import Status


def _inv(text: str) -> str:
    return text


@pytest.fixture(autouse=True)
def _disable_status_ticker(monkeypatch):
    class _DummyThread:
        def __init__(self, target=None, daemon=None):
            self.target = target
            self.daemon = daemon

        def start(self):
            return None

    monkeypatch.setattr(status_mod.threading, "Thread", _DummyThread)


class _FakeDateTime:
    _value = datetime(2026, 5, 17, 8, 9, 0)

    @classmethod
    def now(cls):
        return cls._value


def _ctx_for(
    path: Path,
    *,
    status_values: list[str] | None = None,
    status_banner_text: str = "",
    status_banner_speed_milliseconds: int = 500,
    status_banner_max_characters: int = 0,
    status_prefix: str = "",
    status_animation: list[str] | None = None,
    status_animation_speed_milliseconds: int = 500,
    status_git_sync_prefix_cycle_pause_seconds: float = 1.0,
    status_opened_events: bool = False,
    sys_watch_paths: list[str] | None = None,
) -> Context:
    return Context(
        path=str(path),
        config={
            "sys_watch_paths": list(sys_watch_paths or [str(path.parent)]),
            "status": list(status_values or []),
            "status_banner": status_banner_text,
            "status_banner_speed_milliseconds": status_banner_speed_milliseconds,
            "status_banner_max_characters": status_banner_max_characters,
            "status_prefix": status_prefix,
            "status_animation": list(status_animation or []),
            "status_animation_speed_milliseconds": (
                status_animation_speed_milliseconds
            ),
            "status_opened_events": status_opened_events,
            "status_tick_interval_seconds": 60.0,
            "status_git_fast_tick_interval_seconds": 0.5,
            "status_git_fast_tick_window_seconds": 120.0,
            "status_git_sync_prefix_cycle_pause_seconds": (
                status_git_sync_prefix_cycle_pause_seconds
            ),
        },
        arg_lines={},
    )


def test_status_date_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    path = tmp_path / "note.md"
    path.write_text("--status date\n", encoding="utf-8")

    module = Status()
    ctx = _ctx_for(path, status_values=["date"])
    system = System(
        event=FileModifiedEvent(str(path)), global_template=[], modules=[module]
    )

    changed = module.modified(ctx, system)

    new_path = tmp_path / "17-05"
    assert changed == {str(path.resolve()): 1, str(new_path.resolve()): 1}
    assert new_path.exists()
    assert not path.exists()


def test_status_date_time_order(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    path = tmp_path / "note.md"
    path.write_text("--status date time\n", encoding="utf-8")

    module = Status()
    ctx = _ctx_for(path, status_values=["date", "time"])
    system = System(
        event=FileModifiedEvent(str(path)), global_template=[], modules=[module]
    )

    changed = module.modified(ctx, system)

    new_path = tmp_path / _inv("17-05 08:09")
    assert changed == {str(path.resolve()): 1, str(new_path.resolve()): 1}
    assert new_path.exists()
    assert not path.exists()


def test_status_time_date_order(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    path = tmp_path / "note.md"
    path.write_text("--status time date\n", encoding="utf-8")

    module = Status()
    ctx = _ctx_for(path, status_values=["time", "date"])
    system = System(
        event=FileModifiedEvent(str(path)), global_template=[], modules=[module]
    )

    changed = module.modified(ctx, system)

    new_path = tmp_path / _inv("08:09 17-05")
    assert changed == {str(path.resolve()): 1, str(new_path.resolve()): 1}
    assert new_path.exists()
    assert not path.exists()


def test_status_time_with_seconds_order(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    path = tmp_path / "note.md"
    path.write_text("--status date time-with-seconds\n", encoding="utf-8")

    module = Status()
    ctx = _ctx_for(path, status_values=["date", "time-with-seconds"])
    system = System(
        event=FileModifiedEvent(str(path)), global_template=[], modules=[module]
    )

    changed = module.modified(ctx, system)

    new_path = tmp_path / _inv("17-05 08:09:00")
    assert changed == {str(path.resolve()): 1, str(new_path.resolve()): 1}
    assert new_path.exists()
    assert not path.exists()


def test_status_banner_renames_and_rotates_with_speed(
    tmp_path: Path, monkeypatch
) -> None:
    now_state = {"value": 10.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])

    path = tmp_path / "note.md"
    path.write_text(
        '--status-banner "Work sentence"\n--status-banner-speed-milliseconds 2000\n',
        encoding="utf-8",
    )

    module = Status()
    system = System(
        event=FileModifiedEvent(str(path)), global_template=[], modules=[module]
    )

    first_changed = module.modified(
        _ctx_for(
            path,
            status_banner_text="Work sentence",
            status_banner_speed_milliseconds=2000,
        ),
        system,
    )
    first_path = tmp_path / _inv(".Work sentence")
    assert first_changed == {str(path.resolve()): 1, str(first_path.resolve()): 1}
    assert first_path.exists()

    module._tick_once()
    assert first_path.exists()

    now_state["value"] = 12.1
    module._tick_once()
    second_path = tmp_path / _inv(".ork sentenceW")
    assert second_path.exists()
    assert not first_path.exists()


def test_status_banner_combines_with_status_tokens(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    path = tmp_path / "note.md"
    path.write_text(
        '--status date time\n--status-banner "Focus now"\n--status-banner-speed-milliseconds 3000\n',
        encoding="utf-8",
    )

    module = Status()
    ctx = _ctx_for(
        path,
        status_values=["date", "time"],
        status_banner_text="Focus now",
        status_banner_speed_milliseconds=3000,
    )
    system = System(
        event=FileModifiedEvent(str(path)), global_template=[], modules=[module]
    )

    changed = module.modified(ctx, system)

    new_path = tmp_path / _inv(".17-05 08:09 Focus now")
    assert changed == {str(path.resolve()): 1, str(new_path.resolve()): 1}
    assert new_path.exists()
    assert not path.exists()


def test_status_banner_preserves_multi_spaces(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    banner_text = "s               Demon Lucy is a demon octopus controlling puppet-files from the depth"
    path.write_text(
        f'--status-banner "{banner_text}"\n',
        encoding="utf-8",
    )

    module = Status()
    system = System(
        event=FileModifiedEvent(str(path)), global_template=[], modules=[module]
    )

    changed = module.modified(
        _ctx_for(path, status_banner_text=banner_text),
        system,
    )
    new_path = tmp_path / f".{banner_text}"
    assert changed == {str(path.resolve()): 1, str(new_path.resolve()): 1}
    assert new_path.exists()
    assert not path.exists()


def test_status_git_uses_upstream_timestamp_and_refreshes_after_sync(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "note.md"
    path.write_text("--status git\n", encoding="utf-8")

    upstream_values = [1_800_000_000, 1_800_000_000, 1_800_001_000]
    calls = {"upstream": 0}

    monkeypatch.setattr(status_mod, "find_parent_with", lambda _path, _marker: "/repo")
    monkeypatch.setattr(
        status_mod,
        "read_sync_success_timestamp",
        lambda _repo_root: None,
    )

    def _run(cmd, **_kwargs):
        if cmd[-1] == "@{u}":
            index = min(calls["upstream"], len(upstream_values) - 1)
            calls["upstream"] += 1
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=f"{upstream_values[index]}\n",
                stderr="",
            )
        if cmd[-1] == "HEAD":
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="1900000000\n",
                stderr="",
            )
        raise AssertionError(f"unexpected git command: {cmd}")

    monkeypatch.setattr(status_mod.subprocess, "run", _run)

    module = Status()
    system = System(
        event=FileModifiedEvent(str(path)), global_template=[], modules=[module]
    )

    first_changed = module.modified(_ctx_for(path, status_values=["git"]), system)
    first_path = tmp_path / _inv("1800000000")

    assert first_changed == {str(path.resolve()): 1, str(first_path.resolve()): 1}
    assert first_path.exists()

    second_changed = module.modified(
        _ctx_for(first_path, status_values=["git"]), system
    )
    assert second_changed is None
    assert first_path.exists()

    third_changed = module.modified(_ctx_for(first_path, status_values=["git"]), system)
    third_path = tmp_path / _inv("1800001000")
    assert third_changed == {str(first_path.resolve()): 1, str(third_path.resolve()): 1}
    assert third_path.exists()
    assert not first_path.exists()


def test_status_git_update_uses_compact_units_and_ticks(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "note.md"
    path.write_text("--status git update\n", encoding="utf-8")

    now_state = {"value": 200000.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])
    monkeypatch.setattr(status_mod, "find_parent_with", lambda _path, _marker: "/repo")
    monkeypatch.setattr(
        status_mod,
        "read_sync_success_timestamp",
        lambda _repo_root: None,
    )
    monkeypatch.setattr(
        status_mod.subprocess,
        "run",
        lambda cmd, **_kwargs: subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=f"{int(200000.0 - (3.5 * 3600.0))}\n",
            stderr="",
        ),
    )

    module = Status()
    system = System(
        event=FileModifiedEvent(str(path)), global_template=[], modules=[module]
    )

    first_changed = module.modified(
        _ctx_for(path, status_values=["git", "update"]), system
    )
    first_path = tmp_path / _inv("Sync 3h")
    second_path = tmp_path / _inv("Sync 4h")

    assert first_changed == {str(path.resolve()): 1, str(first_path.resolve()): 1}
    assert first_path.exists()

    now_state["value"] = 203600.0
    module._tick_once()

    assert second_path.exists()
    assert not first_path.exists()


def test_status_git_update_sync_prefix_animates_each_half_second(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "note.md"
    path.write_text("--status git update\n", encoding="utf-8")

    now_state = {"value": 200000.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])
    monkeypatch.setattr(status_mod, "find_parent_with", lambda _path, _marker: "/repo")
    monkeypatch.setattr(
        status_mod,
        "read_sync_success_timestamp",
        lambda _repo_root: None,
    )
    monkeypatch.setattr(
        status_mod.subprocess,
        "run",
        lambda cmd, **_kwargs: subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=f"{int(200000.0 - (3.5 * 3600.0))}\n",
            stderr="",
        ),
    )

    module = Status()
    system = System(
        event=FileModifiedEvent(str(path)), global_template=[], modules=[module]
    )

    first_changed = module.modified(
        _ctx_for(path, status_values=["git", "update"]), system
    )
    first_path = tmp_path / _inv("Sync 3h")
    assert first_changed == {str(path.resolve()): 1, str(first_path.resolve()): 1}
    assert first_path.exists()

    now_state["value"] = 200000.6
    module._tick_once()
    second_path = tmp_path / _inv("sYnc 3h")
    assert second_path.exists()
    assert not first_path.exists()

    now_state["value"] = 200001.2
    module._tick_once()
    third_path = tmp_path / _inv("syNc 3h")
    assert third_path.exists()
    assert not second_path.exists()

    now_state["value"] = 200001.8
    module._tick_once()
    fourth_path = tmp_path / _inv("synC 3h")
    assert fourth_path.exists()
    assert not third_path.exists()


def test_status_git_update_sync_prefix_waits_one_second_between_cycles(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "note.md"
    path.write_text("--status git update\n", encoding="utf-8")

    now_state = {"value": 200000.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])
    monkeypatch.setattr(status_mod, "find_parent_with", lambda _path, _marker: "/repo")
    monkeypatch.setattr(
        status_mod,
        "read_sync_success_timestamp",
        lambda _repo_root: None,
    )
    monkeypatch.setattr(
        status_mod.subprocess,
        "run",
        lambda cmd, **_kwargs: subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=f"{int(200000.0 - (3.5 * 3600.0))}\n",
            stderr="",
        ),
    )

    module = Status()
    system = System(
        event=FileModifiedEvent(str(path)), global_template=[], modules=[module]
    )

    first_changed = module.modified(
        _ctx_for(path, status_values=["git", "update"]), system
    )
    first_path = tmp_path / _inv("Sync 3h")
    assert first_changed == {str(path.resolve()): 1, str(first_path.resolve()): 1}
    assert first_path.exists()

    now_state["value"] = 200000.6
    module._tick_once()
    second_path = tmp_path / _inv("sYnc 3h")
    assert second_path.exists()
    assert not first_path.exists()

    now_state["value"] = 200001.2
    module._tick_once()
    third_path = tmp_path / _inv("syNc 3h")
    assert third_path.exists()
    assert not second_path.exists()

    now_state["value"] = 200001.8
    module._tick_once()
    fourth_path = tmp_path / _inv("synC 3h")
    assert fourth_path.exists()
    assert not third_path.exists()

    now_state["value"] = 200002.4
    module._tick_once()
    pause_path = tmp_path / _inv("sync 3h")
    assert pause_path.exists()
    assert not fourth_path.exists()
    assert not (tmp_path / _inv("Sync 3h")).exists()

    now_state["value"] = 200003.3
    module._tick_once()
    assert pause_path.exists()

    now_state["value"] = 200003.5
    module._tick_once()
    restart_path = tmp_path / _inv("Sync 3h")
    assert restart_path.exists()
    assert not pause_path.exists()


def test_status_git_update_sync_prefix_cycle_pause_from_config(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "note.md"
    path.write_text("--status git update\n", encoding="utf-8")

    now_state = {"value": 200000.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])
    monkeypatch.setattr(status_mod, "find_parent_with", lambda _path, _marker: "/repo")
    monkeypatch.setattr(
        status_mod,
        "read_sync_success_timestamp",
        lambda _repo_root: None,
    )
    monkeypatch.setattr(
        status_mod.subprocess,
        "run",
        lambda cmd, **_kwargs: subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=f"{int(200000.0 - (3.5 * 3600.0))}\n",
            stderr="",
        ),
    )

    module = Status()
    system = System(
        event=FileModifiedEvent(str(path)), global_template=[], modules=[module]
    )

    first_changed = module.modified(
        _ctx_for(
            path,
            status_values=["git", "update"],
            status_git_sync_prefix_cycle_pause_seconds=2.0,
        ),
        system,
    )
    first_path = tmp_path / _inv("Sync 3h")
    assert first_changed == {str(path.resolve()): 1, str(first_path.resolve()): 1}
    assert first_path.exists()

    now_state["value"] = 200000.6
    module._tick_once()
    second_path = tmp_path / _inv("sYnc 3h")
    assert second_path.exists()
    assert not first_path.exists()

    now_state["value"] = 200001.2
    module._tick_once()
    third_path = tmp_path / _inv("syNc 3h")
    assert third_path.exists()
    assert not second_path.exists()

    now_state["value"] = 200001.8
    module._tick_once()
    fourth_path = tmp_path / _inv("synC 3h")
    assert fourth_path.exists()
    assert not third_path.exists()

    now_state["value"] = 200002.4
    module._tick_once()
    pause_path = tmp_path / _inv("sync 3h")
    assert pause_path.exists()
    assert not fourth_path.exists()

    now_state["value"] = 200004.3
    module._tick_once()
    assert pause_path.exists()

    now_state["value"] = 200004.5
    module._tick_once()
    restart_path = tmp_path / _inv("Sync 3h")
    assert restart_path.exists()
    assert not pause_path.exists()


def test_status_git_update_zero_minutes_disables_prefix_animation(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "note.md"
    path.write_text("--status git update\n", encoding="utf-8")

    now_state = {"value": 200000.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])
    monkeypatch.setattr(status_mod, "find_parent_with", lambda _path, _marker: "/repo")
    monkeypatch.setattr(
        status_mod,
        "read_sync_success_timestamp",
        lambda _repo_root: None,
    )
    monkeypatch.setattr(
        status_mod.subprocess,
        "run",
        lambda cmd, **_kwargs: subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=f"{int(200000.0 - 10.0)}\n",
            stderr="",
        ),
    )

    module = Status()
    system = System(
        event=FileModifiedEvent(str(path)), global_template=[], modules=[module]
    )

    first_changed = module.modified(
        _ctx_for(path, status_values=["git", "update"]), system
    )
    first_path = tmp_path / _inv("Sync 0m")
    assert first_changed == {str(path.resolve()): 1, str(first_path.resolve()): 1}
    assert first_path.exists()

    now_state["value"] = 200000.6
    module._tick_once()
    assert first_path.exists()
    assert not (tmp_path / _inv("sYnc 0m")).exists()


def test_status_git_update_animates_custom_prefix_phrase(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "note.md"
    path.write_text("--status git update\n", encoding="utf-8")

    now_state = {"value": 200000.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])
    monkeypatch.setattr(status_mod, "find_parent_with", lambda _path, _marker: "/repo")
    monkeypatch.setattr(
        status_mod,
        "read_sync_success_timestamp",
        lambda _repo_root: None,
    )
    monkeypatch.setattr(
        status_mod.subprocess,
        "run",
        lambda cmd, **_kwargs: subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=f"{int(200000.0 - (3.5 * 3600.0))}\n",
            stderr="",
        ),
    )

    module = Status()
    system = System(
        event=FileModifiedEvent(str(path)), global_template=[], modules=[module]
    )

    first_changed = module.modified(
        _ctx_for(path, status_values=["git", "update"], status_prefix="fresh sync "),
        system,
    )
    first_path = tmp_path / _inv("Fresh sync 3h")
    assert first_changed == {str(path.resolve()): 1, str(first_path.resolve()): 1}
    assert first_path.exists()

    now_state["value"] = 200000.6
    module._tick_once()
    second_path = tmp_path / _inv("fResh sync 3h")
    assert second_path.exists()
    assert not first_path.exists()


def test_status_git_update_prefers_upstream_timestamp(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "note.md"
    path.write_text("--status git update\n", encoding="utf-8")

    now_state = {"value": 200000.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])
    monkeypatch.setattr(status_mod, "find_parent_with", lambda _path, _marker: "/repo")
    monkeypatch.setattr(
        status_mod,
        "read_sync_success_timestamp",
        lambda _repo_root: None,
    )

    def _run(cmd, **_kwargs):
        if cmd[-1] == "@{u}":
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=f"{int(200000.0 - (5.2 * 3600.0))}\n",
                stderr="",
            )
        if cmd[-1] == "HEAD":
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=f"{int(200000.0 - 60.0)}\n",
                stderr="",
            )
        raise AssertionError(f"unexpected git command: {cmd}")

    monkeypatch.setattr(status_mod.subprocess, "run", _run)

    module = Status()
    system = System(
        event=FileModifiedEvent(str(path)), global_template=[], modules=[module]
    )
    changed = module.modified(_ctx_for(path, status_values=["git", "update"]), system)

    new_path = tmp_path / _inv("Sync 5h")
    assert changed == {str(path.resolve()): 1, str(new_path.resolve()): 1}
    assert new_path.exists()


def test_status_git_update_falls_back_to_head_when_upstream_missing(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "note.md"
    path.write_text("--status git update\n", encoding="utf-8")

    now_state = {"value": 200000.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])
    monkeypatch.setattr(status_mod, "find_parent_with", lambda _path, _marker: "/repo")
    monkeypatch.setattr(
        status_mod,
        "read_sync_success_timestamp",
        lambda _repo_root: None,
    )

    def _run(cmd, **_kwargs):
        if cmd[-1] == "@{u}":
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=128,
                stdout="",
                stderr="fatal: no upstream configured",
            )
        if cmd[-1] == "HEAD":
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=f"{int(200000.0 - (2.3 * 3600.0))}\n",
                stderr="",
            )
        raise AssertionError(f"unexpected git command: {cmd}")

    monkeypatch.setattr(status_mod.subprocess, "run", _run)

    module = Status()
    system = System(
        event=FileModifiedEvent(str(path)), global_template=[], modules=[module]
    )
    changed = module.modified(_ctx_for(path, status_values=["git", "update"]), system)

    new_path = tmp_path / _inv("Sync 2h")
    assert changed == {str(path.resolve()): 1, str(new_path.resolve()): 1}
    assert new_path.exists()


def test_status_git_update_uses_recent_sync_success_marker(
    tmp_path: Path, monkeypatch
) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / ".git").mkdir(parents=True)
    (repo_root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    path = repo_root / "note.md"
    path.write_text("--status git update\n", encoding="utf-8")

    now_state = {"value": 200000.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])
    assert write_sync_success_timestamp(
        str(repo_root),
        timestamp_seconds=now_state["value"] - (7.0 * 60.0),
    )

    def _unexpected_git_call(_cmd, **_kwargs):
        raise AssertionError(
            "git subprocess should not run when sync marker is available"
        )

    monkeypatch.setattr(status_mod.subprocess, "run", _unexpected_git_call)

    module = Status()
    system = System(
        event=FileModifiedEvent(str(path)), global_template=[], modules=[module]
    )
    changed = module.modified(_ctx_for(path, status_values=["git", "update"]), system)

    new_path = repo_root / _inv("Sync 7m")
    assert changed == {str(path.resolve()): 1, str(new_path.resolve()): 1}
    assert new_path.exists()


def test_status_git_prefers_sync_success_marker_timestamp(
    tmp_path: Path, monkeypatch
) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / ".git").mkdir(parents=True)
    (repo_root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    path = repo_root / "note.md"
    path.write_text("--status git\n", encoding="utf-8")

    marker_timestamp = 1800000123.0
    assert write_sync_success_timestamp(
        str(repo_root),
        timestamp_seconds=marker_timestamp,
    )

    def _unexpected_git_call(_cmd, **_kwargs):
        raise AssertionError(
            "git subprocess should not run when sync marker is available"
        )

    monkeypatch.setattr(status_mod.subprocess, "run", _unexpected_git_call)

    module = Status()
    system = System(
        event=FileModifiedEvent(str(path)), global_template=[], modules=[module]
    )
    changed = module.modified(_ctx_for(path, status_values=["git"]), system)

    new_path = repo_root / _inv(str(int(marker_timestamp)))
    assert changed == {str(path.resolve()): 1, str(new_path.resolve()): 1}
    assert new_path.exists()


def test_status_ticker_interval_keeps_git_fast_window_temporary(monkeypatch) -> None:
    now_state = {"value": 1000.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])

    module = Status()
    module._set_tracked_parts("/tmp/git-status-note", ["git_update"])

    assert module._ticker_interval_seconds() == 0.5

    now_state["value"] = 1121.0
    assert module._ticker_interval_seconds() == 60.0


def test_status_ticker_interval_prefers_second_precision(monkeypatch) -> None:
    now_state = {"value": 1000.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])

    module = Status()
    module._set_tracked_parts("/tmp/seconds-status-note", ["time_with_seconds"])

    assert module._ticker_interval_seconds() == 1.0


def test_status_ticker_interval_uses_banner_speed(monkeypatch) -> None:
    now_state = {"value": 1000.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])

    module = Status()
    module._set_tracked_parts(
        "/tmp/banner-status-note",
        [],
        banner_text="Focus",
        banner_speed_ms=5000,
    )

    assert module._ticker_interval_seconds() == 5.0


def test_status_animation_default_speed_is_500_ms() -> None:
    module = Status()
    frames, speed_ms = module._normalize_animation_settings(
        ["a", "b"],
        None,
    )

    assert frames == ["a", "b"]
    assert speed_ms == 500


def test_status_prefix_prepends_status_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    path = tmp_path / "note.md"
    path.write_text('--status date time\n--status-prefix "Work: "\n', encoding="utf-8")

    module = Status()
    ctx = _ctx_for(path, status_values=["date", "time"], status_prefix="Work: ")
    system = System(
        event=FileModifiedEvent(str(path)), global_template=[], modules=[module]
    )

    changed = module.modified(ctx, system)
    new_path = tmp_path / _inv("Work: 17-05 08:09")

    assert changed == {str(path.resolve()): 1, str(new_path.resolve()): 1}
    assert new_path.exists()
    assert not path.exists()


def test_status_animation_advance_per_pass_with_speed_and_prefix(
    tmp_path: Path, monkeypatch
) -> None:
    now_state = {"value": 10.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])

    path = tmp_path / "note.md"
    path.write_text(
        '--status-animation "pri" "prive" "privet"\n'
        "--status-animation-speed-milliseconds 1000\n"
        '--status-prefix ">>> "\n',
        encoding="utf-8",
    )

    module = Status()
    system = System(
        event=FileModifiedEvent(str(path)), global_template=[], modules=[module]
    )

    changed_first = module.modified(
        _ctx_for(
            path,
            status_prefix=">>> ",
            status_animation=["pri", "prive", "privet"],
            status_animation_speed_milliseconds=1000,
        ),
        system,
    )
    first_path = tmp_path / _inv(">>> pri")
    assert changed_first == {str(path.resolve()): 1, str(first_path.resolve()): 1}
    assert first_path.exists()

    changed_second = module.modified(
        _ctx_for(
            first_path,
            status_prefix=">>> ",
            status_animation=["pri", "prive", "privet"],
            status_animation_speed_milliseconds=1000,
        ),
        system,
    )
    assert changed_second is None
    assert first_path.exists()

    now_state["value"] = 11.1
    changed_third = module.modified(
        _ctx_for(
            first_path,
            status_prefix=">>> ",
            status_animation=["pri", "prive", "privet"],
            status_animation_speed_milliseconds=1000,
        ),
        system,
    )
    second_path = tmp_path / _inv(">>> prive")
    assert changed_third == {
        str(first_path.resolve()): 1,
        str(second_path.resolve()): 1,
    }
    assert second_path.exists()

    now_state["value"] = 12.2
    changed_fourth = module.modified(
        _ctx_for(
            second_path,
            status_prefix=">>> ",
            status_animation=["pri", "prive", "privet"],
            status_animation_speed_milliseconds=1000,
        ),
        system,
    )
    third_path = tmp_path / _inv(">>> privet")
    assert changed_fourth == {
        str(second_path.resolve()): 1,
        str(third_path.resolve()): 1,
    }
    assert third_path.exists()


def test_status_opened_events_disabled_by_default_skips_opened_handler(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    path = tmp_path / "note.md"
    path.write_text("--status time\n", encoding="utf-8")

    module = Status()
    ctx = _ctx_for(path, status_values=["time"])
    system = System(
        event=FileOpenedEvent(str(path)), global_template=[], modules=[module]
    )

    changed = module.opened(ctx, system)

    assert changed is None
    assert path.exists()
    assert not (tmp_path / _inv("08:09")).exists()


def test_status_opened_events_flag_enables_opened_handler(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    path = tmp_path / "note.md"
    path.write_text("--status time\n", encoding="utf-8")

    module = Status()
    ctx = _ctx_for(path, status_values=["time"], status_opened_events=True)
    system = System(
        event=FileOpenedEvent(str(path)), global_template=[], modules=[module]
    )

    changed = module.opened(ctx, system)

    new_path = tmp_path / _inv("08:09")
    assert changed == {str(path.resolve()): 1, str(new_path.resolve()): 1}
    assert new_path.exists()
    assert not path.exists()


def test_status_from_file_does_not_treat_opened_events_as_ascii_frame(
    tmp_path: Path,
) -> None:
    path = tmp_path / "status-source.md"
    path.write_text(
        '--status-animation "pri" "prive" --status-opened-events\n',
        encoding="utf-8",
    )

    module = Status()
    (
        _parts,
        _banner_text,
        _banner_speed_ms,
        _banner_max_chars,
        _status_prefix,
        ascii_frames,
        _ascii_speed_ms,
    ) = module._status_from_file(str(path))

    assert ascii_frames == ["pri", "prive"]


def test_status_banner_uses_max_chars_window(tmp_path: Path, monkeypatch) -> None:
    now_state = {"value": 10.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])

    path = tmp_path / "note.md"
    path.write_text(
        '--status-banner "Working hard"\n--status-banner-speed-milliseconds 2000\n--status-banner-max-characters 4\n',
        encoding="utf-8",
    )

    module = Status()
    system = System(
        event=FileModifiedEvent(str(path)), global_template=[], modules=[module]
    )
    first_changed = module.modified(
        _ctx_for(
            path,
            status_banner_text="Working hard",
            status_banner_speed_milliseconds=2000,
            status_banner_max_characters=4,
        ),
        system,
    )
    first_path = tmp_path / _inv(".Work")
    assert first_changed == {str(path.resolve()): 1, str(first_path.resolve()): 1}
    assert first_path.exists()

    module._tick_once()
    assert first_path.exists()

    now_state["value"] = 12.1
    module._tick_once()
    second_path = tmp_path / _inv(".orki")
    assert second_path.exists()
    assert not first_path.exists()


def test_status_banner_fully_disappears_before_restart(
    tmp_path: Path, monkeypatch
) -> None:
    now_state = {"value": 10.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    path = tmp_path / "note.md"
    path.write_text(
        '--status date\n--status-banner "Working"\n--status-banner-speed-milliseconds 2000\n--status-banner-max-characters 4\n',
        encoding="utf-8",
    )

    module = Status()
    system = System(
        event=FileModifiedEvent(str(path)), global_template=[], modules=[module]
    )
    changed = module.modified(
        _ctx_for(
            path,
            status_values=["date"],
            status_banner_text="Working",
            status_banner_speed_milliseconds=2000,
            status_banner_max_characters=4,
        ),
        system,
    )
    first_path = tmp_path / _inv(".17-05 Work")
    assert changed == {str(path.resolve()): 1, str(first_path.resolve()): 1}
    assert first_path.exists()

    module._tick_once()  # init slot
    now_state["value"] = 12.1
    module._tick_once()  # 17-05 orki
    now_state["value"] = 14.1
    module._tick_once()  # 17-05 rkin
    now_state["value"] = 16.1
    module._tick_once()  # 17-05 king
    now_state["value"] = 18.1
    module._tick_once()  # 17-05 ing
    now_state["value"] = 20.1
    module._tick_once()  # 17-05 ng
    now_state["value"] = 22.1
    module._tick_once()  # 17-05 g
    now_state["value"] = 24.1
    module._tick_once()  # fully disappeared banner into spaces
    disappeared_path = tmp_path / _inv(".17-05     ")
    assert disappeared_path.exists()

    now_state["value"] = 26.1
    module._tick_once()  # still disappeared (blank tail)
    assert disappeared_path.exists()
    now_state["value"] = 32.1
    module._tick_once()  # restart after full blank tail
    restarted_path = tmp_path / _inv(".17-05 Work")
    assert restarted_path.exists()
    assert not disappeared_path.exists()


def test_status_ticker_starts_only_after_first_status_use(
    tmp_path: Path, monkeypatch
) -> None:
    starts = {"count": 0}

    class _CountThread:
        def __init__(self, target=None, daemon=None):
            self.target = target
            self.daemon = daemon

        def start(self):
            starts["count"] += 1

    monkeypatch.setattr(status_mod.threading, "Thread", _CountThread)
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    no_status_path = tmp_path / "plain.md"
    no_status_path.write_text("body\\n", encoding="utf-8")
    status_path = tmp_path / "tag.md"
    status_path.write_text("--status time\\n", encoding="utf-8")

    module = Status()
    assert starts["count"] == 0

    system = System(
        event=FileModifiedEvent(str(no_status_path)),
        global_template=[],
        modules=[module],
    )

    changed_plain = module.modified(_ctx_for(no_status_path), system)
    assert changed_plain is None
    assert starts["count"] == 0

    changed_status = module.modified(
        _ctx_for(status_path, status_values=["time"]), system
    )
    assert changed_status is not None
    assert starts["count"] == 1


def test_status_bootstrap_scans_dot_status_dir_after_restart_like_event(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    notes_root = tmp_path / "notes"
    status_dir = notes_root / ".status"
    status_dir.mkdir(parents=True, exist_ok=True)

    status_file = status_dir / "dead.md"
    status_file.write_text("--status time\n", encoding="utf-8")

    trigger_file = notes_root / "random.md"
    trigger_file.write_text("body\n", encoding="utf-8")

    module = Status()
    system = System(
        event=FileModifiedEvent(str(trigger_file)),
        global_template=[],
        modules=[module],
    )

    changed = module.modified(_ctx_for(trigger_file), system)

    revived_path = status_dir / "08:09"
    assert changed == {str(status_file.resolve()): 1, str(revived_path.resolve()): 1}
    assert revived_path.exists()
    assert not status_file.exists()


def test_status_bootstrap_ignores_nested_dot_status_dir(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    notes_root = tmp_path / "notes"
    notes_root.mkdir(parents=True, exist_ok=True)

    nested_dir = notes_root / "project"
    status_dir = nested_dir / ".status"
    status_dir.mkdir(parents=True, exist_ok=True)
    status_file = status_dir / "dead.md"
    status_file.write_text("--status time\n", encoding="utf-8")

    trigger_file = notes_root / "random.md"
    trigger_file.write_text("body\n", encoding="utf-8")

    module = Status()
    system = System(
        event=FileModifiedEvent(str(trigger_file)),
        global_template=[],
        modules=[module],
    )

    changed = module.modified(
        _ctx_for(trigger_file, sys_watch_paths=[str(notes_root)]),
        system,
    )

    assert changed is None
    assert status_file.exists()


def test_status_bootstrap_applies_ascii_animation_from_status_file(
    tmp_path: Path, monkeypatch
) -> None:
    now_state = {"value": 10.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])

    notes_root = tmp_path / "notes"
    status_dir = notes_root / ".status"
    status_dir.mkdir(parents=True, exist_ok=True)

    status_file = status_dir / "dead.md"
    status_file.write_text(
        '--status-animation "pri" "prive" "privet"\n'
        "--status-animation-speed-milliseconds 1000\n"
        '--status-prefix ">>> "\n',
        encoding="utf-8",
    )

    trigger_file = notes_root / "random.md"
    trigger_file.write_text("body\n", encoding="utf-8")

    module = Status()
    system = System(
        event=FileModifiedEvent(str(trigger_file)),
        global_template=[],
        modules=[module],
    )

    changed = module.modified(_ctx_for(trigger_file), system)

    revived_path = status_dir / ">>> pri"
    assert changed == {str(status_file.resolve()): 1, str(revived_path.resolve()): 1}
    assert revived_path.exists()
    assert not status_file.exists()


def test_status_bootstrap_handles_numeric_hyphen_file_name(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    notes_root = tmp_path / "notes"
    status_dir = notes_root / ".status"
    status_dir.mkdir(parents=True, exist_ok=True)

    status_file = status_dir / "21-05"
    status_file.write_text("--status date\n", encoding="utf-8")

    trigger_file = notes_root / "random.md"
    trigger_file.write_text("body\n", encoding="utf-8")

    module = Status()
    system = System(
        event=FileModifiedEvent(str(trigger_file)),
        global_template=[],
        modules=[module],
    )

    changed = module.modified(_ctx_for(trigger_file), system)

    revived_path = status_dir / "17-05"
    assert changed == {str(status_file.resolve()): 1, str(revived_path.resolve()): 1}
    assert revived_path.exists()
    assert not status_file.exists()


def test_status_sanitizes_unnameable_filename_tokens(
    tmp_path: Path, monkeypatch
) -> None:
    now_state = {"value": 10.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])

    notes_root = tmp_path / "notes"
    status_dir = notes_root / ".status"
    status_dir.mkdir(parents=True, exist_ok=True)

    status_file = status_dir / "dead.md"
    status_file.write_text(
        '--status-animation "pri/ve"\n' '--status-prefix "A/B "\n',
        encoding="utf-8",
    )

    trigger_file = notes_root / "random.md"
    trigger_file.write_text("body\n", encoding="utf-8")

    module = Status()
    system = System(
        event=FileModifiedEvent(str(trigger_file)),
        global_template=[],
        modules=[module],
    )

    changed = module.modified(_ctx_for(trigger_file), system)

    revived_path = status_dir / "A_B pri_ve"
    assert changed == {str(status_file.resolve()): 1, str(revived_path.resolve()): 1}
    assert revived_path.exists()
    assert not status_file.exists()


def test_status_uses_fallback_name_when_target_exists(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    notes_root = tmp_path / "notes"
    status_dir = notes_root / ".status"
    status_dir.mkdir(parents=True, exist_ok=True)

    status_file = status_dir / "dead.md"
    status_file.write_text("--status date\n", encoding="utf-8")
    (status_dir / "17-05").write_text("occupied\n", encoding="utf-8")

    trigger_file = notes_root / "random.md"
    trigger_file.write_text("body\n", encoding="utf-8")

    module = Status()
    system = System(
        event=FileModifiedEvent(str(trigger_file)),
        global_template=[],
        modules=[module],
    )

    changed = module.modified(_ctx_for(trigger_file), system)

    revived_path = status_dir / "17-05 (2)"
    assert changed == {str(status_file.resolve()): 1, str(revived_path.resolve()): 1}
    assert revived_path.exists()
    assert not status_file.exists()


def test_status_animation_advances_once_returns_to_first_and_restarts_on_new_event(
    tmp_path: Path, monkeypatch
) -> None:
    now_state = {"value": 10.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])

    notes_root = tmp_path / "notes"
    status_dir = notes_root / ".status"
    status_dir.mkdir(parents=True, exist_ok=True)

    status_file = status_dir / "dead.md"
    status_file.write_text(
        '--status-animation "pri" "prive" "privet"\n'
        "--status-animation-speed-milliseconds 1000\n"
        '--status-prefix ">>> "\n',
        encoding="utf-8",
    )

    trigger_file = notes_root / "random.md"
    trigger_file.write_text("body\n", encoding="utf-8")

    module = Status()
    system = System(
        event=FileModifiedEvent(str(trigger_file)),
        global_template=[],
        modules=[module],
    )

    first_changed = module.modified(_ctx_for(trigger_file), system)
    first_path = status_dir / ">>> pri"
    assert first_changed == {
        str(status_file.resolve()): 1,
        str(first_path.resolve()): 1,
    }
    assert first_path.exists()

    now_state["value"] = 11.2
    module._tick_once()
    second_path = status_dir / ">>> prive"
    assert second_path.exists()
    assert not first_path.exists()

    now_state["value"] = 12.4
    module._tick_once()
    third_path = status_dir / ">>> privet"
    assert third_path.exists()
    assert not second_path.exists()

    now_state["value"] = 13.6
    module._tick_once()
    restarted_first_path = status_dir / ">>> pri"
    assert restarted_first_path.exists()
    assert not third_path.exists()

    now_state["value"] = 14.8
    module._tick_once()
    assert restarted_first_path.exists()

    second_trigger = notes_root / "random-2.md"
    second_trigger.write_text("body\n", encoding="utf-8")
    now_state["value"] = 20.0
    module.modified(_ctx_for(second_trigger), system)
    assert restarted_first_path.exists()

    now_state["value"] = 21.2
    module._tick_once()
    restarted_second_path = status_dir / ">>> prive"
    assert restarted_second_path.exists()
    assert not restarted_first_path.exists()


def test_status_bootstrap_parses_ascii_frame_that_starts_with_double_dash(
    tmp_path: Path, monkeypatch
) -> None:
    now_state = {"value": 10.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])

    notes_root = tmp_path / "notes"
    status_dir = notes_root / ".status"
    status_dir.mkdir(parents=True, exist_ok=True)

    status_file = status_dir / "dead.md"
    status_file.write_text(
        '--status-animation "-- --- --" "-< --- >-"\n',
        encoding="utf-8",
    )

    trigger_file = notes_root / "random.md"
    trigger_file.write_text("body\n", encoding="utf-8")

    module = Status()
    system = System(
        event=FileModifiedEvent(str(trigger_file)),
        global_template=[],
        modules=[module],
    )

    first_changed = module.modified(_ctx_for(trigger_file), system)
    first_path = status_dir / "-- --- --"
    assert first_changed == {
        str(status_file.resolve()): 1,
        str(first_path.resolve()): 1,
    }
    assert first_path.exists()

    now_state["value"] = 11.2
    module._tick_once()
    second_path = status_dir / "-< --- >-"
    assert second_path.exists()
    assert not first_path.exists()


def test_status_bootstrap_scans_only_once_even_if_first_scan_found_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    notes_root = tmp_path / "notes"
    notes_root.mkdir(parents=True, exist_ok=True)
    trigger_file = notes_root / "random.md"
    trigger_file.write_text("body\n", encoding="utf-8")

    module = Status()
    system = System(
        event=FileModifiedEvent(str(trigger_file)),
        global_template=[],
        modules=[module],
    )

    first_changed = module.modified(_ctx_for(trigger_file), system)
    assert first_changed is None

    status_dir = notes_root / ".status"
    status_dir.mkdir(parents=True, exist_ok=True)
    status_file = status_dir / "late.md"
    status_file.write_text("--status time\n", encoding="utf-8")

    second_changed = module.modified(_ctx_for(trigger_file), system)
    assert second_changed is None
    assert status_file.exists()
