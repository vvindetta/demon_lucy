from __future__ import annotations

from pathlib import Path

import pytest
from watchdog.events import FileModifiedEvent

import lucy_notes_manager.module_manager as module_manager_mod
from lucy_notes_manager.module_manager import ModuleManager
from lucy_notes_manager.modules.abstract_module import AbstractModule, Context, System

_SYSTEM_CONFIG = {
    "sys_notify_provider": "termuxapi",
    "sys_notify_min_interval_sec": 0.0,
    "sys_blacklist_paths": [],
}


class _ModA(AbstractModule):
    name = "a"
    priority = 20

    def __init__(self):
        self.calls = 0

    def modified(self, ctx: Context, system: System):
        self.calls += 1
        return {ctx.path: 1}


class _ModB(AbstractModule):
    name = "b"
    priority = 30

    # no modified() override on purpose


class _ModC(AbstractModule):
    name = "c"
    priority = 40

    def __init__(self):
        self.calls = 0

    def modified(self, ctx: Context, system: System):
        self.calls += 1
        return {ctx.path: 2}


class _RequiredMod(AbstractModule):
    name = "required_mod"
    priority = 50
    template = [("--required-path", str, None, "required value", True)]

    def __init__(self):
        self.calls = 0

    def modified(self, ctx: Context, system: System):
        self.calls += 1
        return {ctx.path: 1}


@pytest.mark.parametrize(
    "value",
    ["broken-item", "a=not-int", "=5"],
)
def test_parse_priority_list_rejects_bad_items(value: str):
    manager = ModuleManager(modules=[_ModA()], args=[], system_config=_SYSTEM_CONFIG)
    with pytest.raises(ValueError):
        manager._parse_priority_list([value])


def test_init_sorts_modules_by_priority_override():
    a, c = _ModA(), _ModC()
    manager = ModuleManager(
        modules=[c, a],
        args=["--sys-priority", "c=1", "a=9"],
        system_config=_SYSTEM_CONFIG,
    )
    assert [m.name for m in manager.modules] == ["c", "a"]


def test_run_respects_exclude_and_force_and_event_implementation(tmp_path: Path):
    note = tmp_path / "n.md"
    note.write_text("hello\n", encoding="utf-8")
    event = FileModifiedEvent(str(note))

    a, b, c = _ModA(), _ModB(), _ModC()
    manager = ModuleManager(
        modules=[a, b, c],
        args=["--exclude", "a"],
        system_config=_SYSTEM_CONFIG,
    )
    ignore_paths = manager.run(str(note), event)

    assert a.calls == 0
    assert c.calls == 1
    assert ignore_paths == {str(note.resolve()): 2}

    a2, c2 = _ModA(), _ModC()
    manager_force = ModuleManager(
        modules=[a2, c2],
        args=["--exclude", "a", "--force", "a"],
        system_config=_SYSTEM_CONFIG,
    )
    ignore_paths_force = manager_force.run(str(note), event)

    assert a2.calls == 1
    assert c2.calls == 1
    assert ignore_paths_force == {str(note.resolve()): 3}


def test_run_skips_module_when_required_args_missing_and_notifies(tmp_path: Path, monkeypatch):
    note = tmp_path / "n.md"
    note.write_text("hello\n", encoding="utf-8")
    event = FileModifiedEvent(str(note))

    notifications: list[tuple[str, str]] = []
    monkeypatch.setattr(
        module_manager_mod,
        "safe_notify",
        lambda name, message, config: notifications.append((name, message)),
    )

    required_mod = _RequiredMod()
    manager_missing = ModuleManager(
        modules=[required_mod],
        args=[],
        system_config=_SYSTEM_CONFIG,
    )

    ignore_missing = manager_missing.run(str(note), event)
    assert required_mod.calls == 0
    assert ignore_missing is None
    assert notifications
    assert "--required-path" in notifications[0][1]

    required_mod_ok = _RequiredMod()
    manager_ok = ModuleManager(
        modules=[required_mod_ok],
        args=["--required-path", "value"],
        system_config=_SYSTEM_CONFIG,
    )
    ignore_ok = manager_ok.run(str(note), event)
    assert required_mod_ok.calls == 1
    assert ignore_ok == {str(note.resolve()): 1}


def test_run_skips_all_modules_for_blacklisted_paths(tmp_path: Path):
    blacklisted_dir = tmp_path / "private"
    blacklisted_dir.mkdir(parents=True, exist_ok=True)
    note = blacklisted_dir / "n.md"
    note.write_text("hello\n", encoding="utf-8")
    event = FileModifiedEvent(str(note))

    a, c = _ModA(), _ModC()
    manager = ModuleManager(
        modules=[a, c],
        args=[],
        system_config={
            **_SYSTEM_CONFIG,
            "sys_blacklist_paths": [str(blacklisted_dir)],
        },
    )

    ignore_paths = manager.run(str(note), event)
    assert a.calls == 0
    assert c.calls == 0
    assert ignore_paths is None
