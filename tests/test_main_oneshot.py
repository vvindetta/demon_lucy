from __future__ import annotations

from pathlib import Path

import pytest

import main_oneshot as oneshot_mod
from demon_lucy.lib.args.models import (
    ArgSource,
    KnownArg,
    ParsedArgs,
    UnknownArg,
)
from demon_lucy.lib.args.parser import parse_args


def test_oneshot_event_default_is_typed_enum() -> None:
    parsed = parse_args(args=[], template=oneshot_mod.ONESHOT_STARTUP_TEMPLATE)

    assert parsed.unknown == ()
    assert (
        parsed.require("oneshot-event").value
        is oneshot_mod.OneShotEvent.MODIFIED
    )


def _base_config(tmp_path: Path) -> dict:
    return {
        "oneshot_event": oneshot_mod.OneShotEvent.MODIFIED,
        "oneshot_paths": [str(tmp_path / "note.md")],
        "oneshot_move_src_path": "",
        "oneshot_move_dest_path": "",
        "sys_modules": [],
        "sys_modules_exclude": [],
        "sys_watch_paths": [],
        "sys_disable_opened_events": False,
        "sys_log_level": "warning",
    }


def _startup_args(
    config: dict,
    *,
    unknown_args: list[str] | None = None,
) -> ParsedArgs:
    return ParsedArgs(
        known=tuple(
            KnownArg(
                name=f"{key.replace('_', '-')}",
                value=value,
                source=ArgSource.CLI,
            )
            for key, value in config.items()
        ),
        unknown=tuple(
            UnknownArg(token=token, source=ArgSource.CLI)
            for token in unknown_args or []
        ),
    )


def test_build_event_plan_supports_single_event(tmp_path: Path):
    config = _base_config(tmp_path)
    plan = oneshot_mod._build_event_plan(_startup_args(config))

    assert len(plan) == 1
    path_value, event = plan[0]
    assert path_value.endswith("note.md")
    assert event.event_type == "modified"


def test_build_event_plan_requires_paths_for_non_moved_event(tmp_path: Path):
    config = _base_config(tmp_path)
    config["oneshot_event"] = oneshot_mod.OneShotEvent.DELETED
    config["oneshot_paths"] = []

    with pytest.raises(ValueError, match="requires --oneshot-paths"):
        oneshot_mod._build_event_plan(_startup_args(config))


def test_run_oneshot_passes_oneshot_run_mode(tmp_path: Path, monkeypatch):
    captured: dict[str, str] = {}

    class _FakeManager:
        def __init__(
            self,
            modules,
            startup_args,
            run_mode="daemon",
        ):
            _ = (modules, startup_args)
            captured["run_mode"] = run_mode

        def run(self, path, event, event_id=None):
            _ = (path, event, event_id)
            return None

    monkeypatch.setattr(oneshot_mod, "configure_logging", lambda _config: None)
    monkeypatch.setattr(
        oneshot_mod,
        "select_demon_lucy_modules",
        lambda include_names, exclude_names: [],
    )
    monkeypatch.setattr(oneshot_mod, "ModuleManager", _FakeManager)

    config = _base_config(tmp_path)
    exit_code = oneshot_mod.run_oneshot(startup_args=_startup_args(config))

    assert exit_code == 0
    assert captured["run_mode"] == "oneshot"


def test_run_oneshot_without_paths_runs_cli_flow(
    tmp_path: Path,
    monkeypatch,
):
    captured: dict[str, object] = {}

    class _FakeManager:
        def __init__(
            self,
            modules,
            startup_args,
            run_mode="daemon",
        ):
            _ = modules
            captured["args"] = startup_args.unknown
            captured["run_mode"] = run_mode

        def run(self, path, event, event_id=None):
            _ = (path, event, event_id)
            raise AssertionError("event flow should not run")

        def run_cli(self, event_id=None):
            captured["event_id"] = event_id
            return None, 1

    monkeypatch.setattr(oneshot_mod, "configure_logging", lambda _args: None)
    monkeypatch.setattr(
        oneshot_mod,
        "select_demon_lucy_modules",
        lambda include_names, exclude_names: [],
    )
    monkeypatch.setattr(oneshot_mod, "ModuleManager", _FakeManager)

    config = _base_config(tmp_path)
    config["oneshot_paths"] = []
    exit_code = oneshot_mod.run_oneshot(
        startup_args=_startup_args(
            config,
            unknown_args=["--workspace-init", str(tmp_path / "Notes")],
        ),
    )

    assert exit_code == 0
    assert captured["run_mode"] == "cli"
    assert captured["args"] == (
        UnknownArg(token="--workspace-init", source=ArgSource.CLI),
        UnknownArg(token=str(tmp_path / "Notes"), source=ArgSource.CLI),
    )
    assert captured["event_id"]


def test_run_oneshot_without_paths_errors_when_no_module_arg_runs(
    tmp_path: Path,
    monkeypatch,
):
    class _FakeManager:
        def __init__(
            self,
            modules,
            startup_args,
            run_mode="daemon",
        ):
            _ = (modules, startup_args, run_mode)

        def run_cli(self, event_id=None):
            _ = event_id
            return None, 0

    monkeypatch.setattr(oneshot_mod, "configure_logging", lambda _args: None)
    monkeypatch.setattr(
        oneshot_mod,
        "select_demon_lucy_modules",
        lambda include_names, exclude_names: [],
    )
    monkeypatch.setattr(oneshot_mod, "ModuleManager", _FakeManager)

    config = _base_config(tmp_path)
    config["oneshot_paths"] = []

    with pytest.raises(ValueError, match="requires at least one module argument"):
        oneshot_mod.run_oneshot(startup_args=_startup_args(config))


def test_main_returns_2_when_startup_args_are_invalid(monkeypatch):
    monkeypatch.setattr(oneshot_mod, "run_config_migrations", lambda _path: [])
    monkeypatch.setattr(
        oneshot_mod,
        "load_args",
        lambda template: ParsedArgs(),
    )
    assert oneshot_mod.main() == 2
