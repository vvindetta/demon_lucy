from __future__ import annotations

from datetime import datetime
from pathlib import Path

from watchdog.events import FileMovedEvent

import lucy_notes_manager.modules.today as today_mod
from lucy_notes_manager.modules.abstract_module import Context, System
from lucy_notes_manager.modules.dropdir import DropDir
from lucy_notes_manager.modules.today import Today


def _freeze_today(monkeypatch, year: int, month: int, day: int) -> None:
    class _FakeDatetime:
        @classmethod
        def now(cls):
            return datetime(year, month, day, 12, 0, 0)

    monkeypatch.setattr(today_mod, "datetime", _FakeDatetime)


def _ctx(path: Path, cleanup_selector: str) -> Context:
    return Context(
        path=str(path),
        config={
            "dropdir_today_clean_paths": [cleanup_selector],
            "today_now_path": "now.md",
            "today_past_path": "past.md",
            "today_idle_hours": 12.0,
            "today_force_fs": False,
        },
        arg_lines={},
    )


def test_dropdir_forces_today_archive_when_now_moved_into_cleanup(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_today(monkeypatch, 2026, 5, 3)

    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir(parents=True, exist_ok=True)
    now_path = cleanup_dir / "now.md"
    now_path.write_text("clean this now\n", encoding="utf-8")

    src_path = tmp_path / "inbox" / "now.md"
    src_path.parent.mkdir(parents=True, exist_ok=True)

    dropdir = DropDir()
    today = Today()
    event = FileMovedEvent(str(src_path), str(now_path))
    system = System(event=event, global_template=[], modules=[dropdir, today])

    changed = dropdir.moved(_ctx(now_path, "cleanup"), system)

    past_path = src_path.parent / "past.md"
    assert changed == {
        str(now_path.resolve()): 1,
        str(src_path.resolve()): 2,
        str(past_path.resolve()): 1,
    }
    assert not now_path.exists()
    assert src_path.read_text(encoding="utf-8") == ""
    assert past_path.read_text(encoding="utf-8") == "-- 03.05\nclean this now\n"


def test_dropdir_ignores_non_today_filename(tmp_path: Path, monkeypatch) -> None:
    _freeze_today(monkeypatch, 2026, 5, 3)

    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir(parents=True, exist_ok=True)
    file_path = cleanup_dir / "other.md"
    file_path.write_text("keep\n", encoding="utf-8")

    src_path = tmp_path / "inbox" / "other.md"
    src_path.parent.mkdir(parents=True, exist_ok=True)

    dropdir = DropDir()
    today = Today()
    event = FileMovedEvent(str(src_path), str(file_path))
    system = System(event=event, global_template=[], modules=[dropdir, today])

    changed = dropdir.moved(_ctx(file_path, "cleanup"), system)

    assert changed == {str(file_path.resolve()): 1, str(src_path.resolve()): 1}
    assert not file_path.exists()
    assert src_path.read_text(encoding="utf-8") == "keep\n"
    assert not (src_path.parent / "past.md").exists()
