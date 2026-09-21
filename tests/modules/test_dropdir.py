from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from watchdog.events import FileMovedEvent

import demon_lucy.modules.dropdir.module as dropdir_module
import demon_lucy.modules.dropdir.actions as dropdir_actions
from demon_lucy.lib.args.models import ArgSource, ParsedArgs, Template
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.lib.notifications import NotificationProvider
from demon_lucy.modules.abstract_module import Context, System
from demon_lucy.modules.archive import clock as archive_clock
from demon_lucy.modules.archive import Archive
from demon_lucy.modules.dropdir import DropDir
from demon_lucy.modules.formatter import Formatter
from demon_lucy.modules.linker import Linker
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
        *Linker.template,
    ]


def _args(
    action: tuple[str, str] | None = None,
    *,
    delay_ms: int = 0,
) -> ParsedArgs:
    return parse_args(
        args=[
            *(["--dropdir-action", *action] if action is not None else []),
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
    action: tuple[str, str] | None,
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
    *modules: Archive | Formatter | Linker,
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
        _ctx(now_path, ("--archive-pair", "cleanup"), event),
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
        _ctx(file_path, ("--archive-pair", "cleanup"), event),
        system,
    )

    assert result_changes(changed) == {
        str(file_path.resolve()): 1,
        str(src_path.resolve()): 1,
    }
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
            ("--archive-pair", "cleanup"),
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
    ctx = _ctx(dropped_path, ("--formatter-todo", "drop"), event)

    changed = dropdir.moved(ctx, system)

    assert result_changes(changed) == {
        str(dropped_path.resolve()): 1,
        str(src_path.resolve()): 2,
    }
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
        ("--sys-log-level debug --formatter-todo", "drop"),
        event,
    )

    changed = dropdir.moved(ctx, system)

    assert result_changes(changed) == {
        str(dropped_path.resolve()): 1,
        str(src_path.resolve()): 1,
    }
    assert not dropped_path.exists()
    assert src_path.read_text(encoding="utf-8") == "body\n"


@pytest.mark.parametrize("subdirectory", ["", "nested"])
def test_dropdir_runs_linker_for_configured_absolute_directory(
    tmp_path: Path, subdirectory: str
) -> None:
    (tmp_path / ".git").mkdir()
    drop_dir = tmp_path / "my drop directory"
    destination_dir = drop_dir / subdirectory
    destination_dir.mkdir(parents=True)
    dropped = destination_dir / "note.md"
    dropped.write_text("note body\n", encoding="utf-8")
    source = tmp_path / "inbox" / "note.md"
    source.parent.mkdir()
    dropdir = DropDir()
    ctx = _ctx(
        dropped,
        ("--linker-root", str(drop_dir)),
        FileMovedEvent(str(source), str(dropped)),
    )

    result = dropdir.moved(ctx, _system(dropdir, Linker()))

    assert result is not None
    assert result.context.path == str(source)
    assert result.context.args.require("linker-root").value is False
    assert not dropped.exists()
    assert source.read_text(encoding="utf-8") == "note body\n"
    root_link = tmp_path / "note.md"
    assert root_link.is_symlink()
    assert root_link.resolve() == source
    assert result.changed == {str(dropped): 1, str(source): 1, str(root_link): 1}


def test_dropdir_does_not_match_directory_with_same_prefix(tmp_path: Path) -> None:
    drop_dir = tmp_path / "drop"
    actual_dir = tmp_path / "drop-other"
    actual_dir.mkdir()
    dropped = actual_dir / "note.md"
    dropped.write_text("- task\n", encoding="utf-8")
    source = tmp_path / "inbox" / "note.md"
    source.parent.mkdir()
    dropdir = DropDir()

    result = dropdir.moved(
        _ctx(
            dropped,
            ("--formatter-todo", str(drop_dir)),
            FileMovedEvent(str(source), str(dropped)),
        ),
        _system(dropdir, Formatter()),
    )

    assert result is None
    assert dropped.read_text(encoding="utf-8") == "- task\n"
    assert not source.exists()


@pytest.mark.parametrize("action", ["", "--formatter-todo '", "--unknown-action"])
def test_dropdir_reports_invalid_actions(
    tmp_path: Path, monkeypatch, caplog, action: str
) -> None:
    dropped = tmp_path / "drop" / "note.md"
    dropped.parent.mkdir()
    dropped.write_text("- task\n", encoding="utf-8")
    source = tmp_path / "inbox" / "note.md"
    source.parent.mkdir()
    notifications = []
    monkeypatch.setattr(
        dropdir_actions,
        "safe_notify",
        lambda *args, **kwargs: notifications.append(args),
    )
    dropdir = DropDir()

    dropdir.moved(
        _ctx(dropped, (action, "drop"), FileMovedEvent(str(source), str(dropped))),
        _system(dropdir, Formatter()),
    )

    assert "dropdir.action_invalid" in caplog.text
    assert len(notifications) == 1
    remaining = source if source.exists() else dropped
    assert remaining.read_text(encoding="utf-8") == "- task\n"


def test_dropdir_init_runs_actions_and_keeps_marker(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    drop_dir = tmp_path / "any folder name"
    drop_dir.mkdir()
    init_path = drop_dir / "init.md"
    init_text = (
        '# Drop actions\n--dropdir-init "--linker-root"\n'
        '--dropdir-init "--formatter-todo --formatter-blank down 2"\n'
    )
    init_path.write_text(init_text, encoding="utf-8")
    dropped = drop_dir / "todo.md"
    dropped.write_text("- task\n", encoding="utf-8")
    source = tmp_path / "inbox" / dropped.name
    source.parent.mkdir()
    dropdir = DropDir()

    result = dropdir.moved(
        _ctx(dropped, None, FileMovedEvent(str(source), str(dropped))),
        _system(dropdir, Linker(), Formatter()),
    )

    assert result is not None
    assert result.context.path == str(source)
    assert not dropped.exists()
    assert source.read_text(encoding="utf-8") == "- [ ] task\n\n\n"
    root_link = tmp_path / source.name
    assert root_link.is_symlink()
    assert root_link.resolve() == source
    assert init_path.read_text(encoding="utf-8") == init_text
    assert result.context.args.require("linker-root").value is False
    assert result.context.args.require("formatter-todo").value is False
    assert str(init_path) not in result.changed


@pytest.mark.parametrize("scenario", ["marker", "nested", "rename", "ordinary_note"])
def test_dropdir_init_only_handles_drops_into_its_own_folder(
    tmp_path: Path, scenario: str
) -> None:
    drop_dir = tmp_path / "drop"
    drop_dir.mkdir()
    rule = '--dropdir-init "--formatter-todo"\n'
    init_path = drop_dir / "init.md"
    if scenario != "ordinary_note":
        init_path.write_text(rule, encoding="utf-8")
    destination_dir = drop_dir / "nested" if scenario == "nested" else drop_dir
    destination_dir.mkdir(exist_ok=True)
    dropped = destination_dir / ("init.md" if scenario == "marker" else "todo.md")
    body = rule + "- task\n" if scenario in {"marker", "ordinary_note"} else "- task\n"
    dropped.write_text(body, encoding="utf-8")
    source_dir = drop_dir if scenario == "rename" else tmp_path / "inbox"
    source_dir.mkdir(exist_ok=True)
    source = source_dir / "old.md"
    dropdir = DropDir()

    result = dropdir.moved(
        _ctx(dropped, None, FileMovedEvent(str(source), str(dropped))),
        _system(dropdir, Formatter()),
    )

    assert result is None
    assert dropped.read_text(encoding="utf-8") == body
    assert not source.exists()


def test_dropdir_init_reloads_rules_for_each_drop(tmp_path: Path) -> None:
    drop_dir = tmp_path / "drop"
    drop_dir.mkdir()
    init_path = drop_dir / "init.md"
    source_dir = tmp_path / "inbox"
    source_dir.mkdir()
    dropdir = DropDir()
    system = _system(dropdir, Formatter())

    for index, (rule, expected) in enumerate(
        [
            ('--dropdir-init "--formatter-todo"\n', "- [ ] task\n"),
            ('--dropdir-init "--formatter-blank down 1"\n', "- task\n\n"),
        ]
    ):
        init_path.write_text(rule, encoding="utf-8")
        dropped = drop_dir / f"note{index}.md"
        dropped.write_text("- task\n", encoding="utf-8")
        source = source_dir / dropped.name

        dropdir.moved(
            _ctx(dropped, None, FileMovedEvent(str(source), str(dropped))), system
        )

        assert not dropped.exists()
        assert source.read_text(encoding="utf-8") == expected
        assert init_path.read_text(encoding="utf-8") == rule


@pytest.mark.parametrize(
    "action", ["", "--unknown-action", "--sys-disable-opened-events"]
)
def test_dropdir_init_reports_invalid_actions(
    tmp_path: Path, monkeypatch, caplog, action: str
) -> None:
    drop_dir = tmp_path / "drop"
    drop_dir.mkdir()
    init_path = drop_dir / "init.md"
    init_text = f'--dropdir-init "{action}"\n'
    init_path.write_text(init_text, encoding="utf-8")
    dropped = drop_dir / "note.md"
    dropped.write_text("- task\n", encoding="utf-8")
    source = tmp_path / "inbox" / dropped.name
    source.parent.mkdir()
    notifications = []
    monkeypatch.setattr(
        dropdir_actions,
        "safe_notify",
        lambda *args, **kwargs: notifications.append(args),
    )
    dropdir = DropDir()

    dropdir.moved(
        _ctx(dropped, None, FileMovedEvent(str(source), str(dropped))),
        _system(dropdir, Formatter()),
    )

    assert "dropdir.action_invalid" in caplog.text
    assert len(notifications) == 1
    remaining = source if source.exists() else dropped
    assert remaining.read_text(encoding="utf-8") == "- task\n"
    assert init_path.read_text(encoding="utf-8") == init_text
