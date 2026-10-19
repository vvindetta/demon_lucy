from __future__ import annotations

import sys

from demon_lucy.lib.args.parser import split_arg_line
from demon_lucy.lib.args.sources import load_args
from demon_lucy.module_manager import ModuleManager
from demon_lucy.modules.alias import Alias
from demon_lucy.modules.banner import Banner
from demon_lucy.runtime import (
    DEMON_LUCY_DEFERRED_TEMPLATE,
    DEMON_LUCY_STARTUP_TEMPLATE,
    run_config_migrations,
)
from watchdog.events import FileModifiedEvent


def test_alias_migration_splits_command_and_text_rules(tmp_path, monkeypatch):
    config = tmp_path / "config.txt"
    original = (
        '# --alias "comment=leave alone"\n'
        '--alias "date=--banner-date" "thanks=Спасибо за помощь!" " -- = — "\n'
        "--sys-notification-provider disable\n"
    )
    config.write_text(original)

    migrations = run_config_migrations(str(config))

    assert [item.get_migration_name() for item in migrations] == [
        "alias_kinds_20261018"
    ]
    assert (tmp_path / "config.txt.bak-alias_kinds_20261018").read_text() == original
    assert config.read_text().splitlines()[0] == original.splitlines()[0]
    assert split_arg_line(config.read_text().splitlines()[1]) == [
        "--alias-command",
        "date=--banner-date",
        "--alias-text",
        "--thanks=Спасибо за помощь!",
        "--alias-text",
        " -- = — ",
    ]
    assert run_config_migrations(str(config)) == []

    monkeypatch.setattr(sys, "argv", ["lucy", "--sys-config-path", str(config)])
    startup = load_args(
        DEMON_LUCY_STARTUP_TEMPLATE, deferred_template=DEMON_LUCY_DEFERRED_TEMPLATE
    )
    note = tmp_path / "note.md"
    note.write_text("word -- word\n--thanks\n")
    manager = ModuleManager(modules=[Alias(), Banner()], startup_args=startup)
    manager.run(str(note), FileModifiedEvent(str(note)))
    assert note.read_text() == "word — word\nСпасибо за помощь!\n"


def test_alias_migration_handles_inline_flag_and_preserves_other_flags(tmp_path):
    config = tmp_path / "config.txt"
    config.write_text("--alias=date=--banner-date --sys-log-level info\n")
    run_config_migrations(str(config))
    assert split_arg_line(config.read_text()) == [
        "--alias-command",
        "date=--banner-date",
        "--sys-log-level",
        "info",
    ]


def test_alias_migration_leaves_current_rules_untouched(tmp_path):
    config = tmp_path / "config.txt"
    original = '--alias-command "date=--banner-date"\n--alias-text " -- = — "\n'
    config.write_text(original)
    assert run_config_migrations(str(config)) == []
    assert config.read_text() == original


def test_alias_migration_removes_empty_legacy_rule_list(tmp_path):
    config = tmp_path / "config.txt"
    config.write_text("--alias --sys-log-level info\n")
    run_config_migrations(str(config))
    assert config.read_text() == "--sys-log-level info\n"
