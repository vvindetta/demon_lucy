from __future__ import annotations

from datetime import datetime
from pathlib import Path

from watchdog.events import FileMovedEvent

import demon_lucy.modules.dropdir.module as dropdir_module
from demon_lucy.modules.abstract_module import Context, System
from demon_lucy.modules.archive import clock as archive_clock
from demon_lucy.modules.dropdir import DropDir
from demon_lucy.modules.archive import Archive
from demon_lucy.modules.formatter import Formatter

_NOTIFICATION_CONFIG = {
    "sys_notification_provider": "disable",
    "sys_notification_min_interval_seconds": 0.0,
    "sys_notification_error_backoff_base_seconds": 0.0,
    "sys_notification_error_backoff_max_seconds": 0.0,
    "sys_notification_error_burst_limit": 0,
    "sys_notification_error_burst_window_seconds": 0.0,
}


def _freeze_archive_day(monkeypatch, year: int, month: int, day: int) -> None:
    class _FakeDatetime:
        @classmethod
        def now(cls):
            return datetime(year, month, day, 12, 0, 0)

    monkeypatch.setattr(archive_clock, "datetime", _FakeDatetime)


def _ctx(path: Path, cleanup_selector: str, *, delay_ms: int = 0) -> Context:
    return Context(
        path=str(path),
        config={
            "dropdir_action": [f"{cleanup_selector}=--archive-pair"],
            "dropdir_action_delay_milliseconds": delay_ms,
            "archive_pair": [],
            "archive_local": [],
            "archive_global": [],
            "archive_auto_pair": ["now.md", "past.md"],
            "archive_auto_local": [],
            "archive_auto_global": [],
            "archive_default_mode": "text",
            "archive_global_dest_path": "",
            "archive_idle_hours": 12.0,
            "archive_date_prefix": "--- ",
            "archive_date_suffix": "",
            "archive_force_filesystem_mtime": False,
            **_NOTIFICATION_CONFIG,
        },
        arg_lines={},
    )


def _global_template():
    return DropDir.template + Archive.template + Formatter.template


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
    system = System(
        event=event,
        global_template=_global_template(),
        modules=[dropdir, archive],
    )

    changed = dropdir.moved(_ctx(now_path, "cleanup"), system)

    past_path = src_path.parent / "past.md"
    assert changed == {
        str(now_path.resolve()): 1,
        str(src_path.resolve()): 2,
        str(past_path.resolve()): 1,
    }
    assert not now_path.exists()
    assert src_path.read_text(encoding="utf-8") == ""
    assert past_path.read_text(encoding="utf-8") == "--- 03.05.2026\nclean this now\n"


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
    system = System(
        event=event,
        global_template=_global_template(),
        modules=[dropdir, archive],
    )

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
    monkeypatch.setattr(dropdir_module.time, "sleep", lambda value: slept.append(value))

    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir(parents=True, exist_ok=True)
    now_path = cleanup_dir / "now.md"
    now_path.write_text("clean this now\n", encoding="utf-8")

    src_path = tmp_path / "inbox" / "now.md"
    src_path.parent.mkdir(parents=True, exist_ok=True)

    dropdir = DropDir()
    archive = Archive()
    event = FileMovedEvent(str(src_path), str(now_path))
    system = System(
        event=event,
        global_template=_global_template(),
        modules=[dropdir, archive],
    )

    _ = dropdir.moved(_ctx(now_path, "cleanup", delay_ms=1500), system)

    assert slept == [1.5]


def test_dropdir_runs_arbitrary_configured_action(tmp_path: Path) -> None:
    drop_dir = tmp_path / "drop"
    drop_dir.mkdir(parents=True, exist_ok=True)
    dropped_path = drop_dir / "todo.md"
    dropped_path.write_text("- task\n", encoding="utf-8")

    src_path = tmp_path / "inbox" / "todo.md"
    src_path.parent.mkdir(parents=True, exist_ok=True)

    dropdir = DropDir()
    formatter = Formatter()
    event = FileMovedEvent(str(src_path), str(dropped_path))
    system = System(
        event=event,
        global_template=_global_template(),
        modules=[dropdir, formatter],
    )
    ctx = Context(
        path=str(dropped_path),
        config={
            "dropdir_action": ["drop=--formatter-todo"],
            "dropdir_action_delay_milliseconds": 0,
            "formatter_todo": False,
            "formatter_blank": [],
            **_NOTIFICATION_CONFIG,
        },
        arg_lines={},
    )

    changed = dropdir.moved(ctx, system)

    assert changed == {str(dropped_path.resolve()): 1, str(src_path.resolve()): 2}
    assert not dropped_path.exists()
    assert src_path.read_text(encoding="utf-8") == "- [ ] task\n"


def test_dropdir_rejects_system_flags_in_action(tmp_path: Path) -> None:
    drop_dir = tmp_path / "drop"
    drop_dir.mkdir(parents=True, exist_ok=True)
    dropped_path = drop_dir / "note.md"
    dropped_path.write_text("body\n", encoding="utf-8")

    src_path = tmp_path / "inbox" / "note.md"
    src_path.parent.mkdir(parents=True, exist_ok=True)

    dropdir = DropDir()
    formatter = Formatter()
    event = FileMovedEvent(str(src_path), str(dropped_path))
    system = System(
        event=event,
        global_template=_global_template(),
        modules=[dropdir, formatter],
    )
    ctx = Context(
        path=str(dropped_path),
        config={
            "dropdir_action": ["drop=--sys-log-level debug --formatter-todo"],
            "dropdir_action_delay_milliseconds": 0,
            "formatter_todo": False,
            "formatter_blank": [],
            **_NOTIFICATION_CONFIG,
        },
        arg_lines={},
    )

    changed = dropdir.moved(ctx, system)

    assert changed == {str(dropped_path.resolve()): 1, str(src_path.resolve()): 1}
    assert not dropped_path.exists()
    assert src_path.read_text(encoding="utf-8") == "body\n"
