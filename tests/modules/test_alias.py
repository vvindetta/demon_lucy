from __future__ import annotations

from pathlib import Path

from watchdog.events import FileModifiedEvent

import demon_lucy.modules.alias as alias_mod
from demon_lucy.lib.args.models import KnownArg
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.module_manager import ModuleManager
from demon_lucy.modules.alias import Alias
from demon_lucy.modules.alias.rules import AliasRule, parse_rule
from demon_lucy.modules.abstract_module import AbstractModule, Context, System
from demon_lucy.runtime import DEMON_LUCY_STARTUP_TEMPLATE
from tests.args_support import make_context, result_changes


def _system(module: Alias, global_template=None) -> System:
    return System(
        global_template=global_template
        or Alias.template
        + [
            KnownArg(name="banner", description="banner"),
            KnownArg(
                name="formatter-todo",
                value_type=bool,
                default=False,
                description="todo",
            ),
            KnownArg(name="rename", description="rename"),
        ],
        modules=[module],
    )


def test_alias_rewrites_note_flags_to_canonical_flags(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text('--b "Daily notes" --todo\nbody\n', encoding="utf-8")

    module = Alias()
    ctx = make_context(
        str(note),
        Alias.template,
        {
            "alias": [
                "b=--banner {args}",
                "todo=--formatter-todo",
            ],
        },
    )

    changed = module.modified(ctx, _system(module))

    assert result_changes(changed) == {str(note): 1}
    assert note.read_text(encoding="utf-8") == (
        "--banner 'Daily notes' --formatter-todo\nbody\n"
    )


def test_alias_passes_inline_value_to_args_placeholder(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("--rn=done.md\n", encoding="utf-8")

    module = Alias()
    ctx = make_context(
        str(note),
        Alias.template,
        {"alias": ["rn=--rename {args}"]},
    )

    changed = module.modified(ctx, _system(module))

    assert result_changes(changed) == {str(note): 1}
    assert note.read_text(encoding="utf-8") == "--rename done.md\n"


def test_alias_rule_preserves_windows_path() -> None:
    parsed = parse_rule(
        r"inc=--include C:\Users\name\Notes\file.md",
        known_flag_values={"--include"},
    )

    assert isinstance(parsed, AliasRule)
    assert parsed.expansion_tokens == [
        "--include",
        r"C:\Users\name\Notes\file.md",
    ]


def test_alias_dry_run_does_not_write(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("--b Hello\n", encoding="utf-8")

    module = Alias()
    ctx = make_context(
        str(note),
        Alias.template,
        {
            "alias": ["b=--banner {args}"],
            "alias-dry-run": True,
        },
    )

    changed = module.modified(ctx, _system(module))

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
        lambda name, message, args, **_kwargs: notifications.append((name, message)),
    )

    module = Alias()
    ctx = make_context(
        str(note),
        Alias.template,
        {"alias": ["x=--sys-log-level {args}"]},
    )
    system = _system(
        module,
        global_template=Alias.template
        + [
            KnownArg(
                name="sys-log-level",
                value_type=str,
                default="warning",
                description="log level",
            )
        ],
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
        lambda name, message, args, **_kwargs: notifications.append((name, message)),
    )

    module = Alias()
    ctx = make_context(
        str(note),
        Alias.template,
        {"alias": ["run=--cmd {args}"]},
    )
    system = _system(
        module,
        global_template=Alias.template
        + [KnownArg(name="cmd", value_type=str, default=[], description="cmd")],
    )

    changed = module.modified(ctx, system)

    assert changed is None
    assert note.read_text(encoding="utf-8") == "--run echo hello\n"
    assert notifications
    assert "unsafe_target_forbidden" in notifications[0][1]


class _Recorder(AbstractModule):
    name = "recorder"
    priority = 10
    template = [KnownArg(name="banner", description="banner")]

    def __init__(self):
        self.banner_value = None

    def modified(self, ctx: Context, system: System):
        self.banner_value = ctx.args.require("banner").value
        return None


def test_alias_rewrite_is_reparsed_before_next_module(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("--b Hello\n", encoding="utf-8")
    recorder = _Recorder()
    manager = ModuleManager(
        modules=[recorder, Alias()],
        startup_args=parse_args(
            args=["--alias", "b=--banner {args}"],
            template=DEMON_LUCY_STARTUP_TEMPLATE,
        ),
    )

    changed = manager.run(str(note), FileModifiedEvent(str(note)), event_id="evt-test")

    assert changed == {str(note.resolve()): 1}
    assert recorder.banner_value == "Hello"
    assert note.read_text(encoding="utf-8") == "--banner Hello\n"
