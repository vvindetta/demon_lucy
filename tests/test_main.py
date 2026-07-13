from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import main_daemon


@dataclass
class _ObserverState:
    scheduled: list[tuple[Any, str, bool]] = field(default_factory=list)
    started: bool = False
    stopped: bool = False
    joined: bool = False
    return_code: int | None = None


def _run_main_with_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    watch_paths: list[str] | None = None,
    sys_modules: list[str] | None = None,
    runtime_system: str = "linux",
    disable_opened_events: bool = True,
) -> _ObserverState:
    state = _ObserverState()

    class FakeObserver:
        def schedule(self, handler, path, recursive):
            state.scheduled.append((handler, path, recursive))

        def start(self):
            state.started = True

        def stop(self):
            state.stopped = True

        def join(self):
            state.joined = True

    class FakeFileHandler:
        def __init__(
            self,
            modules,
            open_cooldown_seconds,
            process_opened_events=True,
        ):
            self.modules = modules
            self.open_cooldown_seconds = open_cooldown_seconds
            self.process_opened_events = process_opened_events

    class FakeModuleManager:
        def __init__(
            self,
            modules,
            args,
            system_config=None,
            run_mode="daemon",
        ):
            self.modules = modules
            self.args = args
            self.system_config = system_config
            self.run_mode = run_mode
            self.runtime_system = runtime_system

    class FakeEvent:
        def wait(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(main_daemon, "Observer", FakeObserver)
    monkeypatch.setattr(main_daemon, "FileHandler", FakeFileHandler)
    monkeypatch.setattr(main_daemon, "ModuleManager", FakeModuleManager)
    monkeypatch.setattr(main_daemon.threading, "Event", FakeEvent)
    monkeypatch.setattr(main_daemon, "run_config_migrations", lambda _path: [])

    monkeypatch.setattr(
        main_daemon,
        "setup_config_and_cli_args",
        lambda template: (
            {
                "sys_log_level": "info",
                "sys_log_format": "%(message)s",
                "sys_watch_paths": (
                    watch_paths if watch_paths is not None else [str(tmp_path)]
                ),
                "sys_opened_event_cooldown_seconds": 20,
                "sys_disable_opened_events": disable_opened_events,
                "sys_notification_provider": "auto",
                "sys_notification_min_interval_seconds": 10.0,
                "sys_modules": list(sys_modules or []),
                "sys_modules_exclude": [],
            },
            [],
        ),
    )
    state.return_code = main_daemon.main()
    return state


def test_main_schedules_observer_and_modules(
    tmp_path: Path,
    monkeypatch,
):
    state = _run_main_with_flag(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert state.started is True
    assert state.stopped is True
    assert state.joined is True
    assert len(state.scheduled) == 1
    handler, scheduled_path, recursive = state.scheduled[0]
    assert scheduled_path == str(tmp_path)
    assert recursive is True
    assert handler.open_cooldown_seconds == 20
    assert handler.process_opened_events is False
    assert handler.modules.run_mode == "daemon"
    assert [m.name for m in handler.modules.modules] == [
        "alias",
        "workspace",
        "banner",
        "renamer",
        "status",
        "linker",
        "dropdir",
        "formatter",
        "graph",
        "include",
        "archive",
        "sys",
        "kdeconnect_sync",
        "git",
        "plasma_widget",
        "voice",
    ]


def test_main_logs_when_native_opened_events_are_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    _run_main_with_flag(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        runtime_system="macos",
        disable_opened_events=False,
    )

    captured = capsys.readouterr()
    assert "watcher.opened_events_unavailable" in captured.err
    assert "system=macOS" in captured.err
    assert "Lucy cannot detect when a file is only opened on macOS." in captured.err
    assert (
        "Created, modified, moved, and deleted files are still processed."
        in captured.err
    )


def test_main_does_not_log_unavailable_opened_events_when_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    _run_main_with_flag(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        runtime_system="windows",
        disable_opened_events=True,
    )

    captured = capsys.readouterr()
    assert "watcher.opened_events_unavailable" not in captured.err


def test_main_raises_when_notes_dirs_are_missing(monkeypatch):
    monkeypatch.setattr(main_daemon, "run_config_migrations", lambda _path: [])
    monkeypatch.setattr(
        main_daemon,
        "setup_config_and_cli_args",
        lambda template: (
            {
                "sys_log_level": "info",
                "sys_log_format": "%(message)s",
                "sys_watch_paths": None,
                "sys_opened_event_cooldown_seconds": 20,
                "sys_disable_opened_events": False,
                "sys_notification_provider": "auto",
                "sys_notification_min_interval_seconds": 10.0,
                "sys_modules": [],
                "sys_modules_exclude": [],
            },
            [],
        ),
    )

    with pytest.raises(ValueError):
        main_daemon.main()


def test_main_raises_when_startup_args_are_invalid(monkeypatch):
    monkeypatch.setattr(main_daemon, "run_config_migrations", lambda _path: [])
    monkeypatch.setattr(
        main_daemon,
        "setup_config_and_cli_args",
        lambda template: ({}, []),
    )

    with pytest.raises(ValueError):
        main_daemon.main()


def test_main_continues_when_module_is_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
):
    state = _run_main_with_flag(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        sys_modules=["missing"],
    )

    assert state.return_code == 0
    assert state.started is True
    assert len(state.scheduled) == 1
    handler, _scheduled_path, _recursive = state.scheduled[0]
    assert handler.modules.modules == []
    captured = capsys.readouterr()
    assert "runtime.module_unknown" in captured.err
    assert "modules=missing" in captured.err
    assert "Traceback" not in captured.err


def test_main_expands_user_paths_before_scheduling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    home_dir = tmp_path / "home"
    notes_dir = home_dir / "notes"
    notes_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))

    state = _run_main_with_flag(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        watch_paths=["~/notes"],
    )

    assert len(state.scheduled) == 1
    _handler, scheduled_path, _recursive = state.scheduled[0]
    assert scheduled_path == str(notes_dir.resolve())
