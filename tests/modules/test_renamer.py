from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

import lucy_notes_manager.modules.renamer as renamer_mod
from lucy_notes_manager.modules.renamer import Renamer


@pytest.mark.parametrize(
    ("target_exists", "expected_changed"),
    [
        (False, True),
        (True, False),
    ],
)
def test_apply_manual_rename_behaviour(
    tmp_path: Path, target_exists: bool, expected_changed: bool
):
    old_path = tmp_path / "old.md"
    new_path = tmp_path / "new.md"
    old_path.write_text("x\n", encoding="utf-8")
    if target_exists:
        new_path.write_text("y\n", encoding="utf-8")

    module = Renamer()
    changed = module._apply_manual(path=str(old_path), config={"rename": "new.md"})

    assert (changed is not None) is expected_changed
    assert new_path.exists()
    if expected_changed:
        assert not old_path.exists()
    else:
        assert old_path.exists()


def test_apply_auto_on_create_uses_date_name(tmp_path: Path, monkeypatch):
    class _FakeDatetime:
        @classmethod
        def now(cls):
            return datetime(2026, 4, 21, 10, 30, 0)

    monkeypatch.setattr(renamer_mod, "datetime", _FakeDatetime)

    old_path = tmp_path / "t"
    old_path.write_text("x\n", encoding="utf-8")

    module = Renamer()
    changed = module._apply_auto_on_create(
        path=str(old_path),
        config={"rename_auto": True},
    )

    assert changed is not None
    assert (tmp_path / "21-04.txt").exists()
    assert not old_path.exists()
