from __future__ import annotations

from datetime import datetime
from pathlib import Path

from watchdog.events import FileMovedEvent

import demon_lucy.modules.dropdir as dropdir_mod
import demon_lucy.modules.archive as archive_mod
from demon_lucy.modules.abstract_module import Context, System
from demon_lucy.modules.dropdir import DropDir
from demon_lucy.modules.archive import Archive


def _freeze_archive_day(monkeypatch, year: int, month: int, day: int) -> None:
    class _FakeDatetime:
        @classmethod
        def now(cls):
            return datetime(year, month, day, 12, 0, 0)

    monkeypatch.setattr(archive_mod, "datetime", _FakeDatetime)


def _ctx(path: Path, cleanup_selector: str, *, delay_ms: int = 0) -> Context:
    return Context(
        path=str(path),
        config={
            "dropdir_archive_clean_paths": [cleanup_selector],
            "dropdir_archive_clean_delay_milliseconds": delay_ms,
            "archive": False,
            "archive_pair": ["now.md", "past.md"],
            "archive_default_dest_path": "past.md",
            "archive_idle_hours": 12.0,
            "archive_date_prefix": "-- ",
            "archive_date_suffix": "",
            "archive_force_filesystem_mtime": False,
        },
        arg_lines={},
    )


def test_dropdir_forces_archive_when_now_moved_into_cleanup(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_archive_day(monkeypatch, 2026, 5, 3)

    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir(parents=True, exist_ok=True)
    now_path = cleanup_dir / "now.md"
    now_path.write_text("clean this now\n", encoding="utf-8")

    src_path = tmp_path / "inbox" / "now.md"
    src_path.parent.mkdir(parents=True, exist_ok=True)

    dropdir = DropDir()
    archive = Archive()
    event = FileMovedEvent(str(src_path), str(now_path))
    system = System(event=event, global_template=[], modules=[dropdir, archive])

    changed = dropdir.moved(_ctx(now_path, "cleanup"), system)

    past_path = src_path.parent / "past.md"
    assert changed == {
        str(now_path.resolve()): 1,
        str(src_path.resolve()): 2,
        str(past_path.resolve()): 1,
    }
    assert not now_path.exists()
    assert src_path.read_text(encoding="utf-8") == ""
    assert past_path.read_text(encoding="utf-8") == "-- 03.05.2026\nclean this now\n"


def test_dropdir_ignores_non_archive_filename(tmp_path: Path, monkeypatch) -> None:
    _freeze_archive_day(monkeypatch, 2026, 5, 3)

    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir(parents=True, exist_ok=True)
    file_path = cleanup_dir / "other.md"
    file_path.write_text("keep\n", encoding="utf-8")

    src_path = tmp_path / "inbox" / "other.md"
    src_path.parent.mkdir(parents=True, exist_ok=True)

    dropdir = DropDir()
    archive = Archive()
    event = FileMovedEvent(str(src_path), str(file_path))
    system = System(event=event, global_template=[], modules=[dropdir, archive])

    changed = dropdir.moved(_ctx(file_path, "cleanup"), system)

    assert changed == {str(file_path.resolve()): 1, str(src_path.resolve()): 1}
    assert not file_path.exists()
    assert src_path.read_text(encoding="utf-8") == "keep\n"
    assert not (src_path.parent / "past.md").exists()


def test_dropdir_applies_custom_delay_before_archive_clean(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_archive_day(monkeypatch, 2026, 5, 3)

    slept: list[float] = []
    monkeypatch.setattr(dropdir_mod.time, "sleep", lambda value: slept.append(value))

    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir(parents=True, exist_ok=True)
    now_path = cleanup_dir / "now.md"
    now_path.write_text("clean this now\n", encoding="utf-8")

    src_path = tmp_path / "inbox" / "now.md"
    src_path.parent.mkdir(parents=True, exist_ok=True)

    dropdir = DropDir()
    archive = Archive()
    event = FileMovedEvent(str(src_path), str(now_path))
    system = System(event=event, global_template=[], modules=[dropdir, archive])

    _ = dropdir.moved(_ctx(now_path, "cleanup", delay_ms=1500), system)

    assert slept == [1.5]
