from __future__ import annotations

from pathlib import Path

import pytest
from watchdog.events import FileModifiedEvent, FileOpenedEvent

import demon_lucy.module_manager as module_manager_mod
from demon_lucy.lib.args.parser import ArgTemplate
from demon_lucy.module_manager import ModuleManager
from demon_lucy.lib.dynamic_blocks.parser import format_dynamic_block
from demon_lucy.modules.abstract_module import AbstractModule, Context, System

_SYSTEM_CONFIG = {
    "sys_notification_provider": "termuxapi",
    "sys_notification_min_interval_seconds": 0.0,
    "sys_ignore_paths": [],
}


class _ModA(AbstractModule):
    name = "a"
    priority = 20

    def __init__(self):
        self.calls = 0
        self.last_run_mode = None

    def modified(self, ctx: Context, system: System):
        self.calls += 1
        self.last_run_mode = system.run_mode
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
    template = [
        ArgTemplate(
            name="--required-path",
            description="required value",
            required=True,
        )
    ]

    def __init__(self):
        self.calls = 0

    def modified(self, ctx: Context, system: System):
        self.calls += 1
        return {ctx.path: 1}


class _ListMod(AbstractModule):
    name = "list_mod"
    priority = 60
    template = [
        ArgTemplate(name="--items", value_type=str, default=[], description="items")
    ]

    def __init__(self):
        self.seen_config = None

    def modified(self, ctx: Context, system: System):
        self.seen_config = dict(ctx.config)
        return None


def _render_test_block(block, _target_path: str) -> str:
    return f"rendered {block.params['value']}"


class _DynamicBlockMod(AbstractModule):
    name = "dynamic_block"
    dynamic_block_renderers = {"example": _render_test_block}


class _DuplicateDynamicBlockMod(AbstractModule):
    name = "duplicate_dynamic_block"
    dynamic_block_renderers = {"example": _render_test_block}


@pytest.mark.parametrize(
    "value",
    ["broken-item", "a=not-int", "=5"],
)
def test_parse_priority_list_rejects_bad_items(value: str):
    manager = ModuleManager(
        modules=[_ModA()],
        args=[],
        system_config=_SYSTEM_CONFIG,
    )
    with pytest.raises(ValueError):
        manager._parse_priority_list([value])


def test_parse_priority_list_accepts_negative_priority():
    manager = ModuleManager(
        modules=[_ModA()],
        args=[],
        system_config=_SYSTEM_CONFIG,
    )

    assert manager._parse_priority_list(["a=-1"]) == {"a": -1}


def test_module_manager_priority_flag_uses_sys_prefix():
    manager = ModuleManager(
        modules=[_ModA()],
        args=[],
        system_config=_SYSTEM_CONFIG,
    )
    flags = [item.name for item in manager.template]

    assert "--sys-modules-priority" in flags
    assert "--modules-priority" not in flags
    assert "sys_modules_priority" in manager.config
    assert "modules_priority" not in manager.config


def test_module_manager_includes_startup_template_flags():
    manager = ModuleManager(
        modules=[_ModA()],
        args=[],
        system_config=_SYSTEM_CONFIG,
    )
    flags = [item.name for item in manager.template]

    assert "--sys-notification-provider" in flags
    assert "--sys-opened-event-cooldown-seconds" in flags
    assert manager.config["sys_notification_provider"] == "termuxapi"
    assert manager.config["sys_opened_event_cooldown_seconds"] == 60


def test_init_sorts_modules_by_priority_override():
    a, c = _ModA(), _ModC()
    manager = ModuleManager(
        modules=[c, a],
        args=["--sys-modules-priority", "c=1", "a=9"],
        system_config=_SYSTEM_CONFIG,
    )
    assert [m.name for m in manager.modules] == ["c", "a"]


def test_init_rejects_duplicate_dynamic_block_args():
    with pytest.raises(ValueError, match="Duplicate dynamic block arg 'example'"):
        ModuleManager(
            modules=[_DynamicBlockMod(), _DuplicateDynamicBlockMod()],
            args=[],
            system_config=_SYSTEM_CONFIG,
        )


def test_file_list_args_override_config_list_args(tmp_path: Path):
    note = tmp_path / "n.md"
    note.write_text("--items note-a note-b\n", encoding="utf-8")
    module = _ListMod()
    manager = ModuleManager(
        modules=[module],
        args=[],
        system_config={
            **_SYSTEM_CONFIG,
            "items": ["config-a", "config-b"],
        },
    )

    manager.run(str(note), FileModifiedEvent(str(note)), event_id="evt-test")

    assert module.seen_config is not None
    assert module.seen_config["items"] == ["note-a", "note-b"]


def test_run_respects_event_implementation(tmp_path: Path):
    note = tmp_path / "n.md"
    note.write_text("hello\n", encoding="utf-8")
    event = FileModifiedEvent(str(note))

    a, b, c = _ModA(), _ModB(), _ModC()
    manager = ModuleManager(
        modules=[a, b, c],
        args=[],
        system_config=_SYSTEM_CONFIG,
    )
    ignore_paths = manager.run(str(note), event)

    assert a.calls == 1
    assert c.calls == 1
    assert ignore_paths == {str(note.resolve()): 3}


def test_run_skips_module_when_required_args_missing_and_notifies(
    tmp_path: Path, monkeypatch
):
    note = tmp_path / "n.md"
    note.write_text("hello\n", encoding="utf-8")
    event = FileModifiedEvent(str(note))

    notifications: list[tuple[str, str]] = []
    monkeypatch.setattr(
        module_manager_mod,
        "safe_notify",
        lambda name, message, config, **_kwargs: notifications.append((name, message)),
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
            "sys_ignore_paths": [str(blacklisted_dir)],
        },
    )

    ignore_paths = manager.run(str(note), event)
    assert a.calls == 0
    assert c.calls == 0
    assert ignore_paths is None


def test_run_passes_oneshot_run_mode_to_system(tmp_path: Path):
    note = tmp_path / "n.md"
    note.write_text("hello\n", encoding="utf-8")
    event = FileModifiedEvent(str(note))
    a = _ModA()

    manager = ModuleManager(
        modules=[a],
        args=[],
        system_config=_SYSTEM_CONFIG,
        run_mode="oneshot",
    )

    manager.run(str(note), event)

    assert a.calls == 1
    assert a.last_run_mode == "oneshot"


def test_run_refreshes_dynamic_blocks_after_module_pipeline(tmp_path: Path):
    note = tmp_path / "n.md"
    note.write_text(
        format_dynamic_block(
            arg="example",
            params={"value": "one"},
            body="old",
        ),
        encoding="utf-8",
    )
    manager = ModuleManager(
        modules=[_DynamicBlockMod()],
        args=[],
        system_config=_SYSTEM_CONFIG,
    )

    ignore = manager.run(
        str(note),
        FileModifiedEvent(str(note)),
        event_id="evt-test",
    )

    assert ignore == {str(note.resolve()): 1}
    assert "rendered one\n\n--- example end ---" in note.read_text(encoding="utf-8")


def test_run_does_not_refresh_dynamic_blocks_on_opened(tmp_path: Path):
    note = tmp_path / "n.md"
    original = format_dynamic_block(
        arg="example",
        params={"value": "one"},
        body="old",
    )
    note.write_text(original, encoding="utf-8")
    manager = ModuleManager(
        modules=[_DynamicBlockMod()],
        args=[],
        system_config=_SYSTEM_CONFIG,
    )

    ignore = manager.run(str(note), FileOpenedEvent(str(note)), event_id="evt-test")

    assert ignore is None
    assert note.read_text(encoding="utf-8") == original


def test_run_leaves_malformed_dynamic_block_file_unchanged(tmp_path: Path):
    note = tmp_path / "n.md"
    original = "--- example begin ---\n- value: one\n"
    note.write_text(original, encoding="utf-8")
    manager = ModuleManager(
        modules=[_DynamicBlockMod()],
        args=[],
        system_config=_SYSTEM_CONFIG,
    )

    ignore = manager.run(
        str(note),
        FileModifiedEvent(str(note)),
        event_id="evt-test",
    )

    assert ignore is None
    assert note.read_text(encoding="utf-8") == original
