from __future__ import annotations

import runpy
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import lucy_notes_manager.file_handler as file_handler_mod
import lucy_notes_manager.lib.args as args_mod
import lucy_notes_manager.module_manager as module_manager_mod
import watchdog.observers as observers_mod


def _main_path() -> str:
    return str((Path(__file__).resolve().parents[1] / "main.py"))


@dataclass
class _ObserverState:
    scheduled: list[tuple[Any, str, bool]] = field(default_factory=list)
    started: bool = False
    stopped: bool = False
    joined: bool = False


def _run_main_with_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, enable_experimental: bool
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
        def __init__(self, modules, open_cooldown_seconds):
            self.modules = modules
            self.open_cooldown_seconds = open_cooldown_seconds

    class FakeModuleManager:
        def __init__(self, modules, args):
            self.modules = modules
            self.args = args

    monkeypatch.setattr(observers_mod, "Observer", FakeObserver)
    monkeypatch.setattr(file_handler_mod, "FileHandler", FakeFileHandler)
    monkeypatch.setattr(module_manager_mod, "ModuleManager", FakeModuleManager)
    monkeypatch.setattr(
        args_mod,
        "setup_config_and_cli_args",
        lambda template: (
            {
                "sys_debug": False,
                "sys_logging_format": "%(message)s",
                "sys_notes_dirs": [str(tmp_path)],
                "sys_on_open_cooldown": 20,
                "sys_enable_experimental_modules": enable_experimental,
            },
            [],
        ),
    )
    monkeypatch.setattr(time, "sleep", lambda _sec: (_ for _ in ()).throw(KeyboardInterrupt()))

    runpy.run_path(_main_path(), run_name="__main__")
    return state


@pytest.mark.parametrize(
    ("enable_experimental", "expected_modules"),
    [
        (False, ["banner", "renamer", "todo_formatter", "today", "sys"]),
        (
            True,
            ["banner", "renamer", "todo_formatter", "today", "sys", "git", "plasma_sync"],
        ),
    ],
)
def test_main_schedules_observer_and_modules(
    tmp_path: Path,
    monkeypatch,
    enable_experimental: bool,
    expected_modules: list[str],
):
    state = _run_main_with_flag(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        enable_experimental=enable_experimental,
    )

    assert state.started is True
    assert state.stopped is True
    assert state.joined is True
    assert len(state.scheduled) == 1
    handler, scheduled_path, recursive = state.scheduled[0]
    assert scheduled_path == str(tmp_path)
    assert recursive is True
    assert handler.open_cooldown_seconds == 20
    assert [m.name for m in handler.modules.modules] == expected_modules


def test_main_raises_when_notes_dirs_are_missing(monkeypatch):
    monkeypatch.setattr(
        args_mod,
        "setup_config_and_cli_args",
        lambda template: (
            {
                "sys_debug": False,
                "sys_logging_format": "%(message)s",
                "sys_notes_dirs": None,
                "sys_on_open_cooldown": 20,
                "sys_enable_experimental_modules": False,
            },
            [],
        ),
    )

    with pytest.raises(ValueError):
        runpy.run_path(_main_path(), run_name="__main__")
