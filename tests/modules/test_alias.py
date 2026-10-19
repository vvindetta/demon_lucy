from __future__ import annotations

from pathlib import Path
import sys

import pytest
from watchdog.events import FileModifiedEvent

import demon_lucy.modules.alias as alias_mod
from demon_lucy.lib.args.models import KnownArg
from demon_lucy.lib.args.parser import parse_args, split_arg_line
from demon_lucy.lib.args.sources import load_args
from demon_lucy.module_manager import ModuleManager
from demon_lucy.modules.alias import Alias
from demon_lucy.modules.alias.rules import CommandAliasRule, parse_command_rule
from demon_lucy.modules.abstract_module import AbstractModule, Context, System
from demon_lucy.runtime import DEMON_LUCY_DEFERRED_TEMPLATE, DEMON_LUCY_STARTUP_TEMPLATE
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
            "alias-command": [
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
        {"alias-command": ["rn=--rename {args}"]},
    )

    changed = module.modified(ctx, _system(module))

    assert result_changes(changed) == {str(note): 1}
    assert note.read_text(encoding="utf-8") == "--rename done.md\n"


def test_alias_rule_preserves_windows_path() -> None:
    parsed = parse_command_rule(
        r"inc=--include C:\Users\name\Notes\file.md",
        known_flag_values={"--include"},
    )

    assert isinstance(parsed, CommandAliasRule)
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
            "alias-command": ["b=--banner {args}"],
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
        {"alias-command": ["x=--sys-log-level {args}"]},
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
        {"alias-command": ["run=--cmd {args}"]},
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
            args=["--alias-command", "b=--banner {args}"],
            template=DEMON_LUCY_STARTUP_TEMPLATE,
        ),
    )

    changed = manager.run(str(note), FileModifiedEvent(str(note)), event_id="evt-test")

    assert changed == {str(note.resolve()): 1}
    assert recorder.banner_value == "Hello"
    assert note.read_text(encoding="utf-8") == "--banner Hello\n"


@pytest.mark.parametrize(
    "rule,before,after",
    [
        (" -- = — ", "  Раз -- два\t-- три\r\n", "  Раз — два\t-- три\r\n"),
        (" -- =—", "Раз -- два\n", "Раз—два\n"),
        (
            "--thanks=Спасибо за помощь!",
            "В конце: --thanks\n",
            "В конце: Спасибо за помощь!\n",
        ),
        (';bye=Best regards, "Alex"', ";bye", 'Best regards, "Alex"'),
        (" -- = — ", "It's fine -- really.\n", "It's fine — really.\n"),
        (r";home=C:\Users\Alex", ";home\n", "C:\\Users\\Alex\n"),
        (";remove=", "Keep ;remove this.\n", "Keep  this.\n"),
        (";greet=Hello {args}", ";greet\n", "Hello {args}\n"),
    ],
)
def test_text_alias_preserves_literal_text(tmp_path, rule, before, after):
    note = tmp_path / "note.md"
    note.write_bytes(before.encode())
    module = Alias()
    ctx = make_context(str(note), Alias.template, {"alias-text": [rule]})

    result = module.modified(ctx, _system(module))

    assert result_changes(result) == {str(note): 1}
    assert note.read_bytes() == after.encode()


def test_text_alias_rules_are_longest_first_and_do_not_cascade(tmp_path):
    note = tmp_path / "note.md"
    note.write_text(";hello ;hi HI ;noop\n")
    module = Alias()
    ctx = make_context(
        str(note),
        Alias.template,
        {
            "alias-text": [
                ";h=short",
                ";hello=old",
                ";hello=;hi",
                ";hi=Hello",
                ";noop=;noop",
            ]
        },
    )
    module.modified(ctx, _system(module))
    assert note.read_text() == ";hi Hello HI ;noop\n"


def test_text_alias_skips_commands_code_and_generated_blocks(tmp_path):
    note = tmp_path / "note.md"
    protected = (
        '--alias-text " -- = — "\n'
        '--banner "word -- word"\n'
        'prose --banner "word -- word"\n'
        "```text\nword -- word\n```\n"
        "~~~text\nword -- word\n~~~\n"
        "--- include begin ---\n- source: file.md\n\nword -- word\n--- include end ---\n"
    )
    note.write_text(protected + "# Heading -- text\nword -- word\n")
    module = Alias()
    ctx = make_context(str(note), Alias.template, {"alias-text": [" -- = — "]})

    module.modified(ctx, _system(module))

    assert note.read_text() == protected + "# Heading — text\nword — word\n"


def test_text_alias_dry_run_does_not_write(tmp_path):
    note = tmp_path / "note.md"
    note.write_text("word -- word\n")
    module = Alias()
    ctx = make_context(
        str(note), Alias.template, {"alias-text": [" -- = — "], "alias-dry-run": True}
    )
    assert module.modified(ctx, _system(module)) is None
    assert note.read_text() == "word -- word\n"


@pytest.mark.parametrize(
    "rule", ["no-separator", "=empty source", "x=--banner-date", "x=word --cmd whoami"]
)
def test_text_alias_rejects_invalid_rules(tmp_path, monkeypatch, rule):
    note = tmp_path / "note.md"
    note.write_text("x\n")
    notifications = []
    monkeypatch.setattr(
        alias_mod, "safe_notify", lambda *args, **kwargs: notifications.append(args)
    )
    module = Alias()
    ctx = make_context(str(note), Alias.template, {"alias-text": [rule]})
    assert module.modified(ctx, _system(module)) is None
    assert note.read_text() == "x\n"
    assert len(notifications) == 1


def test_text_alias_does_not_form_commands_by_concatenation(tmp_path, caplog):
    note = tmp_path / "note.md"
    note.write_text("prefixbanner-date\n")
    module = Alias()
    ctx = make_context(str(note), Alias.template, {"alias-text": ["prefix=--"]})
    assert module.modified(ctx, _system(module)) is None
    assert note.read_text() == "prefixbanner-date\n"
    assert "text_would_create_command" in caplog.text


def test_command_alias_rejects_plain_text_targets(tmp_path, monkeypatch):
    note = tmp_path / "note.md"
    note.write_text("--thanks\n")
    notifications = []
    monkeypatch.setattr(
        alias_mod, "safe_notify", lambda *args, **kwargs: notifications.append(args)
    )
    module = Alias()
    ctx = make_context(
        str(note), Alias.template, {"alias-command": ["thanks=Thank you!"]}
    )
    assert module.modified(ctx, _system(module)) is None
    assert note.read_text() == "--thanks\n"
    assert "command_target_required" in notifications[0][1]


@pytest.mark.parametrize("source", ["config", "cli", "note"])
def test_both_alias_kinds_work_through_module_manager(tmp_path, monkeypatch, source):
    from demon_lucy.modules.banner import Banner

    note = tmp_path / "note.md"
    config = tmp_path / "config.txt"
    rule_lines = (
        '--alias-command "b=--banner {args}"\n'
        '--alias-text " -- = — "\n'
        '--alias-text "--thanks=Спасибо за помощь!"\n'
    )
    config.write_text(rule_lines if source == "config" else "")
    body = "--b Hello\nword -- word\n--thanks\n"
    note.write_text((rule_lines if source == "note" else "") + body)
    cli = ["--sys-config-path", str(config), "--sys-notification-provider", "disable"]
    if source == "cli":
        cli.extend(split_arg_line(rule_lines))
    monkeypatch.setattr(sys, "argv", ["lucy", *cli])
    startup = load_args(
        template=DEMON_LUCY_STARTUP_TEMPLATE,
        deferred_template=DEMON_LUCY_DEFERRED_TEMPLATE,
    )
    manager = ModuleManager(modules=[Alias(), Banner()], startup_args=startup)
    manager.run(str(note), FileModifiedEvent(str(note)))
    expected = (
        Banner._render_banner("Hello", "\n") + "word — word\nСпасибо за помощь!\n"
    )
    if source == "note":
        expected = rule_lines + expected
    assert note.read_text() == expected
    assert manager.run(str(note), FileModifiedEvent(str(note))) is None
