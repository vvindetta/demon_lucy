from __future__ import annotations

from pathlib import Path

import pytest
from watchdog.events import FileModifiedEvent, FileOpenedEvent

import demon_lucy.module_manager as module_manager_mod
from demon_lucy.lib.args.models import (
    ArgSource,
    KnownArg,
    ParsedArgs,
)
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.lib.notifications import NotificationProvider
from demon_lucy.module_manager import ModuleManager
from demon_lucy.lib.dynamic_blocks.parser import format_dynamic_block
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    System,
)
from demon_lucy.runtime import DEMON_LUCY_STARTUP_TEMPLATE
from tests.args_support import make_args

_SYSTEM_ARGS = {
    "sys-notification-provider": NotificationProvider.TERMUX_API,
    "sys-notification-min-interval-seconds": 0.0,
    "sys-ignore-paths": [],
}


def _startup_args(
    *,
    values: dict[str, object] | None = None,
    config_args: list[str] | None = None,
    cli_args: list[str] | None = None,
) -> ParsedArgs:
    startup_args = make_args(
        DEMON_LUCY_STARTUP_TEMPLATE,
        _SYSTEM_ARGS if values is None else values,
        source=ArgSource.CONFIG,
    )
    config_unknown = parse_args(
        args=config_args or [],
        template=[],
        source=ArgSource.CONFIG,
        include_defaults=False,
    )
    cli_unknown = parse_args(
        args=cli_args or [],
        template=[],
        source=ArgSource.CLI,
        include_defaults=False,
    )
    return startup_args.merged_with(config_unknown).merged_with(cli_unknown)


class _ModA(AbstractModule):
    name = "a"
    priority = 20

    def __init__(self):
        self.calls = 0
        self.last_run_mode = None
        self.last_runtime_started_at_monotonic = None

    def modified(self, ctx: Context, system: System):
        self.calls += 1
        self.last_run_mode = ctx.run_mode
        self.last_runtime_started_at_monotonic = system.runtime_started_at_monotonic
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
        KnownArg(
            name="required-path",
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
    template = [KnownArg(name="items", value_type=str, default=[], description="items")]

    def __init__(self):
        self.seen_argument = None

    def modified(self, ctx: Context, system: System):
        self.seen_argument = ctx.args.require("items")
        return None


def _render_test_block(block, _target_path: str, _args) -> str:
    return f"rendered {block.params['value']}"


class _DynamicBlockMod(AbstractModule):
    name = "dynamic_block"
    dynamic_block_renderers = {"example": _render_test_block}


class _DuplicateDynamicBlockMod(AbstractModule):
    name = "duplicate_dynamic_block"
    dynamic_block_renderers = {"example": _render_test_block}


class _CliMod(AbstractModule):
    name = "cli"
    template = [
        KnownArg(
            name="run-cli",
            description="run from CLI",
        )
    ]

    def __init__(self):
        self.context = None
        self.system = None

    def cli(self, ctx: Context, system: System):
        self.context = ctx
        self.system = system
        return {ctx.path: 1}


@pytest.mark.parametrize(
    "value",
    ["broken-item", "a=not-int", "=5"],
)
def test_parse_priority_list_rejects_bad_items(value: str):
    manager = ModuleManager(
        modules=[_ModA()],
        startup_args=_startup_args(),
    )
    with pytest.raises(ValueError):
        manager._parse_priority_list([value])


def test_parse_priority_list_accepts_negative_priority():
    manager = ModuleManager(
        modules=[_ModA()],
        startup_args=_startup_args(),
    )

    assert manager._parse_priority_list(["a=-1"]) == {"a": -1}


def test_module_manager_priority_flag_uses_sys_prefix():
    manager = ModuleManager(
        modules=[_ModA()],
        startup_args=_startup_args(),
    )
    flags = [item.name for item in manager.template]

    assert "sys-modules-priority" in flags
    assert "modules-priority" not in flags
    assert manager.args.find("sys-modules-priority") is not None
    assert manager.args.find("modules-priority") is None


def test_module_manager_includes_startup_template_flags():
    manager = ModuleManager(
        modules=[_ModA()],
        startup_args=_startup_args(),
    )
    flags = [item.name for item in manager.template]

    assert "sys-notification-provider" in flags
    assert "sys-opened-event-cooldown-seconds" in flags
    assert (
        manager.args.require("sys-notification-provider").value
        is NotificationProvider.TERMUX_API
    )
    assert manager.args.require("sys-opened-event-cooldown-seconds").value == 60


def test_init_sorts_modules_by_priority_override():
    a, c = _ModA(), _ModC()
    manager = ModuleManager(
        modules=[c, a],
        startup_args=_startup_args(
            cli_args=["--sys-modules-priority", "c=1", "a=9"],
        ),
    )
    assert [m.name for m in manager.modules] == ["c", "a"]


def test_init_rejects_duplicate_dynamic_block_args():
    with pytest.raises(ValueError, match="Duplicate dynamic block arg 'example'"):
        ModuleManager(
            modules=[_DynamicBlockMod(), _DuplicateDynamicBlockMod()],
            startup_args=_startup_args(),
        )


def test_file_list_args_override_config_list_args(tmp_path: Path):
    note = tmp_path / "n.md"
    note.write_text("--items note-a note-b\n", encoding="utf-8")
    module = _ListMod()
    manager = ModuleManager(
        modules=[module],
        startup_args=_startup_args(
            config_args=["--items", "config-a", "config-b"],
        ),
    )

    manager.run(str(note), FileModifiedEvent(str(note)), event_id="evt-test")

    assert module.seen_argument is not None
    assert module.seen_argument.value == ["note-a", "note-b"]
    assert module.seen_argument.source is ArgSource.FILE


def test_module_manager_tracks_config_and_cli_arg_sources(tmp_path: Path):
    note = tmp_path / "n.md"
    note.write_text("hello\n", encoding="utf-8")
    module = _ListMod()
    manager = ModuleManager(
        modules=[module],
        startup_args=_startup_args(
            cli_args=["--items", "cli-a"],
            config_args=["--items", "config-a"],
        ),
    )

    manager.run(str(note), FileModifiedEvent(str(note)), event_id="evt-test")

    assert module.seen_argument is not None
    assert module.seen_argument.value == ["cli-a"]
    assert module.seen_argument.source is ArgSource.CLI


def test_run_respects_event_implementation(tmp_path: Path):
    note = tmp_path / "n.md"
    note.write_text("hello\n", encoding="utf-8")
    event = FileModifiedEvent(str(note))

    a, b, c = _ModA(), _ModB(), _ModC()
    manager = ModuleManager(
        modules=[a, b, c],
        startup_args=_startup_args(),
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
        lambda name, message, args, **_kwargs: notifications.append((name, message)),
    )

    required_mod = _RequiredMod()
    manager_missing = ModuleManager(
        modules=[required_mod],
        startup_args=_startup_args(),
    )

    ignore_missing = manager_missing.run(str(note), event)
    assert required_mod.calls == 0
    assert ignore_missing is None
    assert notifications
    assert "--required-path" in notifications[0][1]

    required_mod_ok = _RequiredMod()
    manager_ok = ModuleManager(
        modules=[required_mod_ok],
        startup_args=_startup_args(cli_args=["--required-path", "value"]),
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
        startup_args=_startup_args(
            values={
                **_SYSTEM_ARGS,
                "sys-ignore-paths": [str(blacklisted_dir)],
            },
        ),
    )

    ignore_paths = manager.run(str(note), event)
    assert a.calls == 0
    assert c.calls == 0
    assert ignore_paths is None


def test_run_passes_oneshot_run_mode_to_context(tmp_path: Path):
    note = tmp_path / "n.md"
    note.write_text("hello\n", encoding="utf-8")
    event = FileModifiedEvent(str(note))
    a = _ModA()

    manager = ModuleManager(
        modules=[a],
        startup_args=_startup_args(),
        run_mode="oneshot",
    )

    manager.run(str(note), event)

    assert a.calls == 1
    assert a.last_run_mode == "oneshot"
    assert a.last_runtime_started_at_monotonic == manager.runtime_started_at_monotonic


def test_run_cli_automatically_runs_module_with_cli_argument(
    tmp_path: Path,
    monkeypatch,
):
    module = _CliMod()
    manager = ModuleManager(
        modules=[module],
        startup_args=_startup_args(
            cli_args=["--run-cli", "value"],
        ),
        run_mode="cli",
    )
    monkeypatch.chdir(tmp_path)

    ignore, modules_run = manager.run_cli(event_id="evt-cli")

    assert modules_run == 1
    assert ignore == {str(tmp_path.resolve()): 1}
    assert module.context is not None
    assert module.context.path == str(tmp_path.resolve())
    assert module.context.args is manager.args
    assert module.context.args.require("run-cli").value == "value"
    assert module.context.run_mode == "cli"
    assert module.context.event is None
    assert module.context.event_id == "evt-cli"
    assert module.system is not None


def test_run_cli_rejects_unknown_cli_arguments() -> None:
    manager = ModuleManager(
        modules=[_CliMod()],
        startup_args=_startup_args(
            cli_args=["--run-cli", "value", "--typo"],
        ),
        run_mode="cli",
    )

    with pytest.raises(ValueError, match=r"Unknown CLI arguments: --typo"):
        manager.run_cli()


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
        startup_args=_startup_args(),
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
        startup_args=_startup_args(),
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
        startup_args=_startup_args(),
    )

    ignore = manager.run(
        str(note),
        FileModifiedEvent(str(note)),
        event_id="evt-test",
    )

    assert ignore is None
    assert note.read_text(encoding="utf-8") == original
