from __future__ import annotations

from pathlib import Path

from watchdog.events import FileModifiedEvent

import demon_lucy.modules.alias as alias_mod
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.module_manager import ModuleManager
from demon_lucy.modules.alias import Alias
from demon_lucy.modules.abstract_module import AbstractModule, Context, System


_NOTIFICATION_CONFIG = {
    "sys_notification_provider": "disable",
    "sys_notification_min_interval_seconds": 0.0,
    "sys_notification_error_backoff_base_seconds": 0.0,
    "sys_notification_error_backoff_max_seconds": 0.0,
    "sys_notification_error_burst_limit": 0,
    "sys_notification_error_burst_window_seconds": 0.0,
}


def _config(args: list[str]) -> dict[str, object]:
    parsed, unknown = parse_args(args=args, template=Alias.template)
    assert unknown == []
    parsed.update(_NOTIFICATION_CONFIG)
    return parsed


def _system(module: Alias, path: Path, global_template=None) -> System:
    return System(
        event=FileModifiedEvent(str(path)),
        global_template=global_template
        or Alias.template
        + [
            ("--banner", str, None, "banner", False),
            ("--formatter-todo", bool, False, "todo", False),
            ("--rename", str, None, "rename", False),
        ],
        modules=[module],
        event_id="evt-test",
    )


def test_alias_rewrites_note_flags_to_canonical_flags(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text('--b "Daily notes" --todo\nbody\n', encoding="utf-8")

    module = Alias()
    ctx = Context(
        path=str(note),
        config=_config(
            [
                "--alias",
                "b=--banner {args}",
                "todo=--formatter-todo",
            ]
        ),
        arg_lines={},
    )

    changed = module.modified(ctx, _system(module, note))

    assert changed == {str(note): 1}
    assert note.read_text(encoding="utf-8") == (
        "--banner 'Daily notes' --formatter-todo\nbody\n"
    )


def test_alias_passes_inline_value_to_args_placeholder(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("--rn=done.md\n", encoding="utf-8")

    module = Alias()
    ctx = Context(
        path=str(note),
        config=_config(["--alias", "rn=--rename {args}"]),
        arg_lines={},
    )

    changed = module.modified(ctx, _system(module, note))

    assert changed == {str(note): 1}
    assert note.read_text(encoding="utf-8") == "--rename done.md\n"


def test_alias_dry_run_does_not_write(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("--b Hello\n", encoding="utf-8")

    module = Alias()
    ctx = Context(
        path=str(note),
        config=_config(["--alias", "b=--banner {args}", "--alias-dry-run"]),
        arg_lines={},
    )

    changed = module.modified(ctx, _system(module, note))

    assert changed is None
    assert note.read_text(encoding="utf-8") == "--b Hello\n"


def test_alias_rejects_system_target_without_rewrite(
    tmp_path: Path,
    monkeypatch,
):
    note = tmp_path / "note.md"
    note.write_text("--x debug\n", encoding="utf-8")
    notifications: list[tuple[str, str]] = []
    monkeypatch.setattr(
        alias_mod,
        "safe_notify",
        lambda name, message, config, **_kwargs: notifications.append((name, message)),
    )

    module = Alias()
    ctx = Context(
        path=str(note),
        config=_config(["--alias", "x=--sys-log-level {args}"]),
        arg_lines={},
    )
    system = _system(
        module,
        note,
        global_template=Alias.template
        + [("--sys-log-level", str, "warning", "log level", False)],
    )

    changed = module.modified(ctx, system)

    assert changed is None
    assert note.read_text(encoding="utf-8") == "--x debug\n"
    assert notifications
    assert "system_target_forbidden" in notifications[0][1]


def test_alias_rejects_cmd_target_without_rewrite(tmp_path: Path, monkeypatch):
    note = tmp_path / "note.md"
    note.write_text("--run echo hello\n", encoding="utf-8")
    notifications: list[tuple[str, str]] = []
    monkeypatch.setattr(
        alias_mod,
        "safe_notify",
        lambda name, message, config, **_kwargs: notifications.append((name, message)),
    )

    module = Alias()
    ctx = Context(
        path=str(note),
        config=_config(["--alias", "run=--cmd {args}"]),
        arg_lines={},
    )
    system = _system(
        module,
        note,
        global_template=Alias.template + [("--cmd", str, [], "cmd", False)],
    )

    changed = module.modified(ctx, system)

    assert changed is None
    assert note.read_text(encoding="utf-8") == "--run echo hello\n"
    assert notifications
    assert "unsafe_target_forbidden" in notifications[0][1]


class _Recorder(AbstractModule):
    name = "recorder"
    priority = 10
    template = [("--banner", str, None, "banner", False)]

    def __init__(self):
        self.banner_value = None

    def modified(self, ctx: Context, system: System):
        self.banner_value = ctx.config["banner"]
        return None


def test_alias_rewrite_is_reparsed_before_next_module(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("--b Hello\n", encoding="utf-8")
    recorder = _Recorder()
    manager = ModuleManager(
        modules=[recorder, Alias()],
        args=["--alias", "b=--banner {args}"],
        system_config={
            "sys_ignore_paths": [],
            **_NOTIFICATION_CONFIG,
        },
    )

    changed = manager.run(str(note), FileModifiedEvent(str(note)), event_id="evt-test")

    assert changed == {str(note.resolve()): 1}
    assert recorder.banner_value == "Hello"
    assert note.read_text(encoding="utf-8") == "--banner Hello\n"
