from __future__ import annotations

from pathlib import Path

import pytest

import main_oneshot as oneshot_mod


def _base_config(tmp_path: Path) -> dict:
    return {
        "oneshot_event": "modified",
        "oneshot_paths": [str(tmp_path / "note.md")],
        "oneshot_move_src_path": "",
        "oneshot_move_dest_path": "",
        "sys_modules": [],
        "sys_modules_exclude": [],
    }


def test_build_event_plan_supports_single_event(tmp_path: Path):
    config = _base_config(tmp_path)
    plan = oneshot_mod._build_event_plan(config)

    assert len(plan) == 1
    path_value, event = plan[0]
    assert path_value.endswith("note.md")
    assert event.event_type == "modified"


def test_build_event_plan_rejects_multiple_event_values(tmp_path: Path):
    config = _base_config(tmp_path)
    config["oneshot_event"] = "modified,created"

    with pytest.raises(ValueError, match="Only one --oneshot-event is supported"):
        oneshot_mod._build_event_plan(config)


def test_build_event_plan_requires_paths_for_non_moved_event(tmp_path: Path):
    config = _base_config(tmp_path)
    config["oneshot_event"] = "deleted"
    config["oneshot_paths"] = []

    with pytest.raises(ValueError, match="requires --oneshot-paths"):
        oneshot_mod._build_event_plan(config)


def test_run_oneshot_passes_oneshot_run_mode(tmp_path: Path, monkeypatch):
    captured: dict[str, str] = {}

    class _FakeManager:
        def __init__(
            self,
            modules,
            args,
            system_config,
            run_mode="daemon",
        ):
            _ = (modules, args, system_config)
            captured["run_mode"] = run_mode

        def run(self, path, event):
            _ = (path, event)
            return None

    monkeypatch.setattr(oneshot_mod, "configure_logging", lambda _config: None)
    monkeypatch.setattr(
        oneshot_mod,
        "select_demon_lucy_modules",
        lambda include_names, exclude_names: [],
    )
    monkeypatch.setattr(oneshot_mod, "ModuleManager", _FakeManager)

    config = _base_config(tmp_path)
    exit_code = oneshot_mod.run_oneshot(config=config, unknown_args=[])

    assert exit_code == 0
    assert captured["run_mode"] == "oneshot"


def test_main_returns_2_when_startup_args_are_invalid(monkeypatch):
    monkeypatch.setattr(
        oneshot_mod,
        "setup_config_and_cli_args",
        lambda template: ({}, []),
    )
    assert oneshot_mod.main() == 2
