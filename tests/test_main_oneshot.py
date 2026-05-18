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
