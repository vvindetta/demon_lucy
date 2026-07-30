from __future__ import annotations

from datetime import datetime
from pathlib import Path

from watchdog.events import FileMovedEvent

import demon_lucy.modules.dropdir.module as dropdir_module
from demon_lucy.lib.args.models import ArgSource, ParsedArgs, Template
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.lib.notifications import NotificationProvider
from demon_lucy.modules.abstract_module import Context, System
from demon_lucy.modules.archive import clock as archive_clock
from demon_lucy.modules.archive import Archive
from demon_lucy.modules.dropdir import DropDir
from demon_lucy.modules.formatter import Formatter
from demon_lucy.runtime import DEMON_LUCY_STARTUP_TEMPLATE
from tests.args_support import result_changes


def _freeze_archive_day(monkeypatch, year: int, month: int, day: int) -> None:
    class _FakeDatetime:
        @classmethod
        def now(cls):
            return datetime(year, month, day, 12, 0, 0)

    monkeypatch.setattr(archive_clock, "datetime", _FakeDatetime)


def _global_template() -> Template:
    return [
        *DEMON_LUCY_STARTUP_TEMPLATE,
        *DropDir.template,
        *Archive.template,
        *Formatter.template,
    ]


def _args(
    action: str,
    *,
    delay_ms: int = 0,
) -> ParsedArgs:
    return parse_args(
        args=[
            "--dropdir-action",
            action,
            "--dropdir-action-delay-milliseconds",
            str(delay_ms),
            "--archive-auto-pair",
            "now.md",
            "past.md",
            "--sys-notification-provider",
            NotificationProvider.DISABLE,
        ],
        template=_global_template(),
        source=ArgSource.CONFIG,
    )


def _ctx(
    path: Path,
    action: str,
    event: FileMovedEvent,
    *,
    delay_ms: int = 0,
) -> Context:
    return Context(
        path=str(path),
        args=_args(action, delay_ms=delay_ms),
        run_mode="oneshot",
        event_id="test",
        event=event,
    )


def _system(
    dropdir: DropDir,
    *modules: Archive | Formatter,
) -> System:
    return System(
        global_template=_global_template(),
        modules=[dropdir, *modules],
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
    system = _system(dropdir, archive)

    changed = dropdir.moved(
        _ctx(now_path, "cleanup=--archive-pair", event),
        system,
    )

    past_path = src_path.parent / "past.md"
    assert result_changes(changed) == {
        str(now_path.resolve()): 1,
        str(src_path.resolve()): 2,
        str(past_path.resolve()): 1,
    }
    assert changed is not None
    assert changed.context.path == str(src_path.resolve())
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
    system = _system(dropdir, archive)

    changed = dropdir.moved(
        _ctx(file_path, "cleanup=--archive-pair", event),
        system,
    )

    assert result_changes(changed) == {str(file_path.resolve()): 1, str(src_path.resolve()): 1}
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
    system = _system(dropdir, archive)

    _ = dropdir.moved(
        _ctx(
            now_path,
            "cleanup=--archive-pair",
            event,
            delay_ms=1500,
        ),
        system,
    )

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
    system = _system(dropdir, formatter)
    ctx = _ctx(dropped_path, "drop=--formatter-todo", event)

    changed = dropdir.moved(ctx, system)

    assert result_changes(changed) == {str(dropped_path.resolve()): 1, str(src_path.resolve()): 2}
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
    system = _system(dropdir, formatter)
    ctx = _ctx(
        dropped_path,
        "drop=--sys-log-level debug --formatter-todo",
        event,
    )

    changed = dropdir.moved(ctx, system)

    assert result_changes(changed) == {str(dropped_path.resolve()): 1, str(src_path.resolve()): 1}
    assert not dropped_path.exists()
    assert src_path.read_text(encoding="utf-8") == "body\n"
