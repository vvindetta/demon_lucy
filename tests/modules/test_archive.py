from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytest
from watchdog.events import FileModifiedEvent

from demon_lucy.lib import file_time
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.modules.abstract_module import Context, System
from demon_lucy.modules.archive import Archive
from demon_lucy.modules.archive import clock as archive_clock
from demon_lucy.modules.archive import notify as archive_notify
from demon_lucy.modules.archive import storage as archive_storage
from demon_lucy.modules.archive.constants import ARCHIVE_TEMPLATE
from demon_lucy.modules.archive.types import ArchiveOutputMode


def test_archive_default_mode_is_typed_enum() -> None:
    config, unknown = parse_args(args=[], template=ARCHIVE_TEMPLATE)

    assert unknown == []
    assert config["archive_default_mode"] is ArchiveOutputMode.TEXT


def _ctx_for(
    path: Path,
    *,
    force_fs: bool = False,
    force_archive: bool = False,
    pair_values: list[str] | None = None,
    global_dest_path: str = "",
    manual_route: str | None = None,
    manual_mode: str | None = None,
    auto_local_values: list[str] | None = None,
    auto_global_values: list[str] | None = None,
    config_path: str | None = None,
    watch_paths: list[str] | None = None,
    archive_command: bool = False,
) -> Context:
    resolved_pair = (
        list(pair_values) if pair_values is not None else ["now.md", "past.md"]
    )
    config: dict[str, object] = {
        "archive": False,
        "archive_pair": [],
        "archive_local": [],
        "archive_global": [],
        "archive_auto_pair": resolved_pair,
        "archive_auto_local": list(auto_local_values or []),
        "archive_auto_global": list(auto_global_values or []),
        "archive_default_mode": "text",
        "archive_global_dest_path": global_dest_path,
        "archive_idle_hours": 12.0,
        "archive_date_prefix": "--- ",
        "archive_date_suffix": "",
        "archive_force_filesystem_mtime": False,
        "sys_notification_provider": "disable",
        "sys_notification_min_interval_seconds": 0.0,
        "sys_notification_error_backoff_base_seconds": 0.0,
        "sys_notification_error_backoff_max_seconds": 0.0,
        "sys_notification_error_burst_limit": 0,
        "sys_notification_error_burst_window_seconds": 0.0,
    }
    if force_fs:
        config["archive_force_filesystem_mtime"] = True
    arg_lines: dict[str, list[int]] = {}
    if archive_command:
        config["archive"] = True
        arg_lines["archive"] = [1]
    selected_manual_route = manual_route
    if force_archive and selected_manual_route is None:
        selected_manual_route = "pair"
    if selected_manual_route is not None:
        route_key = f"archive_{selected_manual_route}"
        config[route_key] = [manual_mode] if manual_mode else []
        arg_lines[route_key] = [1]
    if config_path is not None:
        config["sys_config_path"] = config_path
    if watch_paths is not None:
        config["sys_watch_paths"] = watch_paths

    return Context(
        path=str(path),
        config=config,
        arg_lines=arg_lines,
    )


def test_supports_custom_archive_now_file(tmp_path: Path, monkeypatch) -> None:
    _freeze_now(monkeypatch, 2026, 5, 2)

    now_path = tmp_path / "active.md"
    now_path.write_text("custom active\n", encoding="utf-8")
    _make_stale(now_path, 13.0)

    trigger_path = tmp_path / "other.md"
    trigger_path.write_text("x\n", encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(trigger_path, pair_values=["active.md", "past.md"])
    system = System(
        event=FileModifiedEvent(str(trigger_path)), global_template=[], modules=[module]
    )
    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert ignore == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert past_path.read_text(encoding="utf-8") == "--- 02.05.2026\ncustom active\n"


def test_allows_absolute_archive_pair_paths_inside_allowed_root(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 2)

    now_path = tmp_path / "λ note.md"
    now_path.write_text("unicode active\n", encoding="utf-8")
    _make_stale(now_path, 3.0)

    past_path = tmp_path / "past.md"
    trigger_path = tmp_path / "other.md"
    trigger_path.write_text("x\n", encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(
        trigger_path,
        pair_values=[str(now_path), str(past_path), "2"],
    )
    system = System(
        event=FileModifiedEvent(str(trigger_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    assert ignore == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert past_path.read_text(encoding="utf-8") == "--- 02.05.2026\nunicode active\n"


def test_rejects_absolute_archive_pair_paths_outside_allowed_root(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 2)

    notes_dir = tmp_path / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    outside_path = tmp_path / "outside.md"
    outside_path.write_text("outside must stay\n", encoding="utf-8")
    _make_stale(outside_path, 13.0)

    trigger_path = notes_dir / "other.md"
    trigger_path.write_text("x\n", encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(
        trigger_path,
        pair_values=[str(outside_path), "past.md", "2"],
    )
    system = System(
        event=FileModifiedEvent(str(trigger_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    assert ignore is None
    assert outside_path.read_text(encoding="utf-8") == "outside must stay\n"
    assert not (notes_dir / "past.md").exists()


def test_archive_security_block_notifies_for_outside_path(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 2)
    notifications: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        archive_notify,
        "safe_notify",
        lambda *args, **kwargs: notifications.append((args, kwargs)),
    )

    notes_dir = tmp_path / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    outside_path = tmp_path / "outside.md"
    outside_path.write_text("outside must stay\n", encoding="utf-8")
    _make_stale(outside_path, 13.0)

    trigger_path = notes_dir / "other.md"
    trigger_path.write_text("x\n", encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(
        trigger_path,
        pair_values=[str(outside_path), "past.md", "2"],
    )
    system = System(
        event=FileModifiedEvent(str(trigger_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    assert ignore is None
    assert notifications
    assert str(notifications[0][0][0]).startswith(
        "archive-security:outside_allowed_root:"
    )
    assert notifications[0][1]["use_rare_mode"] is True


def test_rejects_absolute_archive_pair_path_traversal_outside_allowed_root(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 2)

    notes_dir = tmp_path / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    outside_path = tmp_path / "outside.md"
    outside_path.write_text("outside must stay\n", encoding="utf-8")
    _make_stale(outside_path, 13.0)

    trigger_path = notes_dir / "other.md"
    trigger_path.write_text("x\n", encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(
        trigger_path,
        pair_values=[str(notes_dir / ".." / "outside.md"), "past.md", "2"],
    )
    system = System(
        event=FileModifiedEvent(str(trigger_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    assert ignore is None
    assert outside_path.read_text(encoding="utf-8") == "outside must stay\n"
    assert not (notes_dir / "past.md").exists()


def test_rejects_archive_pair_path_traversal(tmp_path: Path, monkeypatch) -> None:
    _freeze_now(monkeypatch, 2026, 5, 2)

    notes_dir = tmp_path / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    outside_path = tmp_path / "outside.md"
    outside_path.write_text("outside must stay\n", encoding="utf-8")
    _make_stale(outside_path, 13.0)

    trigger_path = notes_dir / "other.md"
    trigger_path.write_text("x\n", encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(trigger_path, pair_values=["../outside.md", "past.md"])
    system = System(
        event=FileModifiedEvent(str(trigger_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    assert ignore is None
    assert outside_path.read_text(encoding="utf-8") == "outside must stay\n"
    assert not (notes_dir / "past.md").exists()


def test_rejects_archive_pair_through_symlink_parent_outside_allowed_root(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 2)

    notes_dir = tmp_path / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_path = outside_dir / "outside.md"
    outside_path.write_text("outside must stay\n", encoding="utf-8")
    _make_stale(outside_path, 13.0)

    link_dir = notes_dir / "linked"
    link_dir.symlink_to(outside_dir, target_is_directory=True)

    trigger_path = notes_dir / "other.md"
    trigger_path.write_text("x\n", encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(trigger_path, pair_values=["linked/outside.md", "past.md"])
    system = System(
        event=FileModifiedEvent(str(trigger_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    assert ignore is None
    assert outside_path.read_text(encoding="utf-8") == "outside must stay\n"
    assert not (notes_dir / "past.md").exists()


def test_archive_rejects_canonical_event_path_outside_configured_watch_roots(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 2)

    notes_dir = tmp_path / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()

    outside_now = outside_dir / "now.md"
    outside_now.write_text("outside must stay\n", encoding="utf-8")
    _make_stale(outside_now, 13.0)

    module = Archive()
    ctx = _ctx_for(
        outside_now,
        force_archive=True,
        pair_values=["now.md", "past.md"],
        watch_paths=[str(notes_dir)],
    )
    system = System(
        event=FileModifiedEvent(str(notes_dir / "linked-now.md")),
        global_template=[],
        modules=[module],
    )

    ignore = module.modified(ctx, system)

    assert ignore is None
    assert outside_now.read_text(encoding="utf-8") == "outside must stay\n"
    assert not (outside_dir / "past.md").exists()


def test_archive_paths_must_stay_inside_git_repo_root(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 2)

    repo_root = tmp_path / "repo"
    note_dir = repo_root / "notes"
    note_dir.mkdir(parents=True, exist_ok=True)
    git_dir = repo_root / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    now_path = note_dir / "now.md"
    now_path.write_text("inside repo\n", encoding="utf-8")
    _make_stale(now_path, 1.0)

    trigger_path = note_dir / "other.md"
    trigger_path.write_text("x\n", encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(
        trigger_path,
        force_archive=True,
        pair_values=["now.md", "past.md"],
    )
    system = System(
        event=FileModifiedEvent(str(trigger_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    past_path = note_dir / "past.md"
    assert ignore == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert past_path.read_text(encoding="utf-8") == "--- 02.05.2026\ninside repo\n"


def test_absolute_archive_paths_can_target_repo_root_from_subdirectory_event(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 2)

    repo_root = tmp_path / "repo"
    note_dir = repo_root / "notes"
    note_dir.mkdir(parents=True, exist_ok=True)
    git_dir = repo_root / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    now_path = repo_root / "now.md"
    now_path.write_text("root archive source\n", encoding="utf-8")
    _make_stale(now_path, 1.0)

    trigger_path = note_dir / "other.md"
    trigger_path.write_text("x\n", encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(
        trigger_path,
        force_archive=True,
        pair_values=[str(now_path), str(repo_root / "past.md")],
    )
    system = System(
        event=FileModifiedEvent(str(trigger_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    past_path = repo_root / "past.md"
    assert ignore == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert (
        past_path.read_text(encoding="utf-8") == "--- 02.05.2026\nroot archive source\n"
    )


def test_archive_rejects_config_file_as_source(tmp_path: Path, monkeypatch) -> None:
    _freeze_now(monkeypatch, 2026, 5, 2)

    config_path = tmp_path / "config.txt"
    config_path.write_text("config must stay\n", encoding="utf-8")
    _make_stale(config_path, 13.0)

    trigger_path = tmp_path / "other.md"
    trigger_path.write_text("x\n", encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(
        trigger_path,
        force_archive=True,
        pair_values=[str(config_path), "past.md"],
        config_path=str(config_path),
    )
    system = System(
        event=FileModifiedEvent(str(trigger_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    assert ignore is None
    assert config_path.read_text(encoding="utf-8") == "config must stay\n"
    assert not (tmp_path / "past.md").exists()


def test_archive_rejects_config_file_as_destination(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 2)

    now_path = tmp_path / "now.md"
    now_path.write_text("must stay active\n", encoding="utf-8")
    _make_stale(now_path, 13.0)

    config_path = tmp_path / "config.txt"
    config_path.write_text("config must stay\n", encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(
        now_path,
        force_archive=True,
        pair_values=["now.md", str(config_path)],
        config_path=str(config_path),
    )
    system = System(
        event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    assert ignore is None
    assert now_path.read_text(encoding="utf-8") == "must stay active\n"
    assert config_path.read_text(encoding="utf-8") == "config must stay\n"


def test_archive_rejects_symlink_destination(tmp_path: Path, monkeypatch) -> None:
    _freeze_now(monkeypatch, 2026, 5, 2)

    now_path = tmp_path / "now.md"
    now_path.write_text("must not leak\n", encoding="utf-8")
    _make_stale(now_path, 1.0)

    target_path = tmp_path / "target.md"
    target_path.write_text("target\n", encoding="utf-8")
    symlink_path = tmp_path / "past.md"
    symlink_path.symlink_to(target_path)

    module = Archive()
    ctx = _ctx_for(now_path, force_archive=True)
    system = System(
        event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    assert ignore is None
    assert now_path.read_text(encoding="utf-8") == "must not leak\n"
    assert target_path.read_text(encoding="utf-8") == "target\n"


def test_archive_rejects_hardlink_destination(tmp_path: Path, monkeypatch) -> None:
    _freeze_now(monkeypatch, 2026, 5, 2)

    now_path = tmp_path / "now.md"
    now_path.write_text("must not write through hardlink\n", encoding="utf-8")
    _make_stale(now_path, 1.0)

    target_path = tmp_path / "target.md"
    target_path.write_text("target\n", encoding="utf-8")
    hardlink_path = tmp_path / "past.md"
    try:
        os.link(target_path, hardlink_path)
    except OSError:
        pytest.skip("filesystem does not support hardlinks")

    module = Archive()
    ctx = _ctx_for(now_path, force_archive=True)
    system = System(
        event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    assert ignore is None
    assert now_path.read_text(encoding="utf-8") == "must not write through hardlink\n"
    assert target_path.read_text(encoding="utf-8") == "target\n"


def test_archive_rejects_hardlink_source(tmp_path: Path, monkeypatch) -> None:
    _freeze_now(monkeypatch, 2026, 5, 2)

    target_path = tmp_path / "target.md"
    target_path.write_text("target must stay\n", encoding="utf-8")
    now_path = tmp_path / "now.md"
    try:
        os.link(target_path, now_path)
    except OSError:
        pytest.skip("filesystem does not support hardlinks")
    _make_stale(now_path, 1.0)

    module = Archive()
    ctx = _ctx_for(now_path, force_archive=True)
    system = System(
        event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    assert ignore is None
    assert target_path.read_text(encoding="utf-8") == "target must stay\n"
    assert not (tmp_path / "past.md").exists()


def test_truncate_source_validates_opened_file_before_truncating(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_path = tmp_path / "source.md"
    source_path.write_text("source must stay\n", encoding="utf-8")
    protected_path = tmp_path / "protected.md"
    protected_path.write_text("protected must stay\n", encoding="utf-8")
    hardlink_path = tmp_path / "hardlink.md"
    try:
        os.link(protected_path, hardlink_path)
    except OSError:
        pytest.skip("filesystem does not support hardlinks")

    open_flags: list[int] = []

    def open_replaced_path(
        path_value: str,
        flags: int,
        *,
        runtime_system: str,
        mode: int = 0o666,
    ) -> int:
        open_flags.append(flags)
        return os.open(hardlink_path, os.O_WRONLY)

    monkeypatch.setattr(archive_storage, "open_file_no_follow", open_replaced_path)

    assert (
        archive_storage.truncate_source_file(
            str(source_path),
            runtime_system="linux",
        )
        is False
    )
    assert open_flags == [os.O_WRONLY]
    assert source_path.read_text(encoding="utf-8") == "source must stay\n"
    assert protected_path.read_text(encoding="utf-8") == "protected must stay\n"


def _make_stale(path: Path, hours: float) -> None:
    old = time.time() - (hours * 3600.0)
    os.utime(path, (old, old))


def _freeze_now(monkeypatch, year: int, month: int, day: int) -> None:
    class _FakeDatetime:
        @classmethod
        def now(cls):
            return datetime(year, month, day, 9, 0, 0)

    monkeypatch.setattr(archive_clock, "datetime", _FakeDatetime)


@pytest.mark.parametrize(
    ("event_target_name", "expected_past_text"),
    [
        ("now.md", "--- 01.05.2026\nsomething\nmore coffee\n"),
        ("other.md", "--- 01.05.2026\narchive me\n"),
    ],
)
def test_archives_stale_now_md_when_triggered_by_now_or_sibling_event(
    tmp_path: Path,
    monkeypatch,
    event_target_name: str,
    expected_past_text: str,
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    now_path = tmp_path / "now.md"
    if event_target_name == "now.md":
        now_path.write_text("something\nmore coffee\n", encoding="utf-8")
    else:
        now_path.write_text("archive me\n", encoding="utf-8")
    _make_stale(now_path, 13.0)

    trigger_path = tmp_path / event_target_name
    if event_target_name != "now.md":
        trigger_path.write_text("x\n", encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(trigger_path)
    system = System(
        event=FileModifiedEvent(str(trigger_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert ignore == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert past_path.read_text(encoding="utf-8") == expected_past_text


def test_does_not_archive_when_file_is_not_stale(tmp_path: Path) -> None:
    now_path = tmp_path / "now.md"
    now_path.write_text("keep\n", encoding="utf-8")
    _make_stale(now_path, 1.0)

    module = Archive()
    ctx = _ctx_for(now_path)
    system = System(
        event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    assert ignore is None
    assert now_path.read_text(encoding="utf-8") == "keep\n"
    assert not (tmp_path / "past.md").exists()


def test_compact_archive_arg_overrides_paths_and_idle_hours(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    active_path = tmp_path / "active.md"
    active_path.write_text("move with compact arg\n", encoding="utf-8")
    _make_stale(active_path, 2.0)

    trigger_path = tmp_path / "other.md"
    trigger_path.write_text("x\n", encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(
        trigger_path,
        pair_values=["active.md", "history.md", "1"],
    )
    system = System(
        event=FileModifiedEvent(str(trigger_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "history.md"
    assert ignore == {str(active_path.resolve()): 1, str(past_path.resolve()): 1}
    assert active_path.read_text(encoding="utf-8") == ""
    assert (
        past_path.read_text(encoding="utf-8")
        == "--- 01.05.2026\nmove with compact arg\n"
    )


def test_compact_archive_arg_without_idle_value_uses_default_idle_hours(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    active_path = tmp_path / "active.md"
    active_path.write_text("use default idle\n", encoding="utf-8")
    _make_stale(active_path, 13.0)

    trigger_path = tmp_path / "other.md"
    trigger_path.write_text("x\n", encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(
        trigger_path,
        pair_values=["active.md", "history.md"],
    )
    system = System(
        event=FileModifiedEvent(str(trigger_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "history.md"
    assert ignore == {str(active_path.resolve()): 1, str(past_path.resolve()): 1}
    assert active_path.read_text(encoding="utf-8") == ""
    assert past_path.read_text(encoding="utf-8") == "--- 01.05.2026\nuse default idle\n"


def test_invalid_archive_rule_notifies_user(tmp_path: Path, monkeypatch) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)
    notifications: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        archive_notify,
        "safe_notify",
        lambda *args, **kwargs: notifications.append((args, kwargs)),
    )

    active_path = tmp_path / "active.md"
    active_path.write_text("invalid rule\n", encoding="utf-8")
    _make_stale(active_path, 13.0)

    trigger_path = tmp_path / "other.md"
    trigger_path.write_text("x\n", encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(
        trigger_path,
        pair_values=["active.md", "history.md"],
    )
    ctx.config["archive_default_mode"] = "zip"
    system = System(
        event=FileModifiedEvent(str(trigger_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    assert ignore is None
    assert notifications
    assert str(notifications[0][0][0]).startswith(
        "archive-rule:--archive-default-mode:unsupported_mode:"
    )
    assert "Archive rule is invalid." in str(notifications[0][0][1])
    assert notifications[0][1]["use_rare_mode"] is True


def test_archive_operation_failure_notifies_user(tmp_path: Path, monkeypatch) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)
    notifications: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        archive_notify,
        "safe_notify",
        lambda *args, **kwargs: notifications.append((args, kwargs)),
    )
    monkeypatch.setattr(
        archive_storage,
        "truncate_source_file",
        lambda _src_path, *, runtime_system: False,
    )

    active_path = tmp_path / "active.md"
    active_path.write_text("cannot truncate\n", encoding="utf-8")
    _make_stale(active_path, 13.0)

    trigger_path = tmp_path / "other.md"
    trigger_path.write_text("x\n", encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(
        trigger_path,
        pair_values=["active.md", "history.md"],
    )
    system = System(
        event=FileModifiedEvent(str(trigger_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    assert ignore is None
    assert notifications
    assert str(notifications[0][0][0]).startswith(
        "archive-operation:truncate_source_failed:"
    )
    assert "Archive operation failed." in str(notifications[0][0][1])
    assert notifications[0][1]["use_rare_mode"] is True


def test_archive_global_uses_configured_global_dest_path(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    src_path = tmp_path / "note.md"
    src_path.write_text("fallback archive\n", encoding="utf-8")
    _make_stale(src_path, 13.0)

    module = Archive()
    ctx = _ctx_for(
        src_path,
        pair_values=[],
        manual_route="global",
        global_dest_path="journal.md",
    )
    system = System(
        event=FileModifiedEvent(str(src_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    dest_path = tmp_path / "journal.md"
    assert ignore == {str(src_path.resolve()): 1, str(dest_path.resolve()): 1}
    assert src_path.read_text(encoding="utf-8") == ""
    assert dest_path.read_text(encoding="utf-8") == "--- 01.05.2026\nfallback archive\n"


def test_archive_local_text_prefers_existing_dot_archive_file(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    src_path = tmp_path / "note.md"
    src_path.write_text("fallback archive dir\n", encoding="utf-8")
    (tmp_path / ".archive").mkdir()

    module = Archive()
    ctx = _ctx_for(
        src_path,
        pair_values=[],
        manual_route="local",
    )
    system = System(
        event=FileModifiedEvent(str(src_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    dest_path = tmp_path / ".archive" / "archive.md"
    assert ignore == {str(src_path.resolve()): 1, str(dest_path.resolve()): 1}
    assert src_path.read_text(encoding="utf-8") == ""
    assert (
        dest_path.read_text(encoding="utf-8")
        == "--- 01.05.2026\nfallback archive dir\n"
    )
    assert not (tmp_path / "archive.md").exists()


def test_archive_local_file_creates_new_file_under_dot_archive(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    src_path = tmp_path / "note.md"
    src_path.write_text("--archive-local file\nlocal file archive\n", encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(
        src_path,
        pair_values=[],
        manual_route="local",
        manual_mode="file",
    )
    system = System(
        event=FileModifiedEvent(str(src_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    dest_path = tmp_path / ".archive" / "2026-05-01---note.md"
    assert ignore == {str(src_path.resolve()): 1, str(dest_path.resolve()): 1}
    assert src_path.read_text(encoding="utf-8") == ""
    assert dest_path.read_text(encoding="utf-8") == "local file archive\n"
    assert not (tmp_path / "archive.md").exists()


def test_archive_file_mode_uses_unique_name_without_overwrite(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    archive_dir = tmp_path / ".archive"
    archive_dir.mkdir()
    existing_path = archive_dir / "2026-05-01---note.md"
    existing_path.write_text("older copy\n", encoding="utf-8")

    src_path = tmp_path / "note.md"
    src_path.write_text("second copy\n", encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(
        src_path,
        pair_values=[],
        manual_route="local",
        manual_mode="file",
    )
    system = System(
        event=FileModifiedEvent(str(src_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    dest_path = archive_dir / "2026-05-01---note-2.md"
    assert ignore == {str(src_path.resolve()): 1, str(dest_path.resolve()): 1}
    assert existing_path.read_text(encoding="utf-8") == "older copy\n"
    assert dest_path.read_text(encoding="utf-8") == "second copy\n"


def test_archive_global_without_dest_uses_repo_root_archive_text(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    repo_root = tmp_path / "repo"
    note_dir = repo_root / "notes"
    note_dir.mkdir(parents=True)
    git_dir = repo_root / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    src_path = note_dir / "note.md"
    src_path.write_text("global text archive\n", encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(
        src_path,
        pair_values=[],
        manual_route="global",
    )
    system = System(
        event=FileModifiedEvent(str(src_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    dest_path = repo_root / "archive.md"
    assert ignore == {str(src_path.resolve()): 1, str(dest_path.resolve()): 1}
    assert src_path.read_text(encoding="utf-8") == ""
    assert (
        dest_path.read_text(encoding="utf-8") == "--- 01.05.2026\nglobal text archive\n"
    )


def test_archive_auto_local_file_archives_stale_configured_source(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    src_path = tmp_path / "now.md"
    src_path.write_text("auto local file\n", encoding="utf-8")
    _make_stale(src_path, 2.0)

    trigger_path = tmp_path / "other.md"
    trigger_path.write_text("trigger\n", encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(
        trigger_path,
        pair_values=[],
        auto_local_values=["now.md", "1", "file"],
    )
    system = System(
        event=FileModifiedEvent(str(trigger_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    dest_path = tmp_path / ".archive" / "2026-05-01---now.md"
    assert ignore == {str(src_path.resolve()): 1, str(dest_path.resolve()): 1}
    assert src_path.read_text(encoding="utf-8") == ""
    assert dest_path.read_text(encoding="utf-8") == "auto local file\n"


def test_archive_file_mode_rejects_global_dest_outside_allowed_root(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()

    src_path = notes_dir / "note.md"
    src_path.write_text("must stay\n", encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(
        src_path,
        pair_values=[],
        manual_route="global",
        manual_mode="file",
        global_dest_path=str(outside_dir),
    )
    system = System(
        event=FileModifiedEvent(str(src_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    assert ignore is None
    assert src_path.read_text(encoding="utf-8") == "must stay\n"
    assert not list(outside_dir.iterdir())


def test_does_not_use_default_dest_when_pair_is_missing_without_archive_flag(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    src_path = tmp_path / "note.md"
    src_path.write_text("must stay\n", encoding="utf-8")
    _make_stale(src_path, 13.0)

    module = Archive()
    ctx = _ctx_for(
        src_path,
        pair_values=[],
        global_dest_path="journal.md",
    )
    system = System(
        event=FileModifiedEvent(str(src_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    dest_path = tmp_path / "journal.md"
    assert ignore is None
    assert src_path.read_text(encoding="utf-8") == "must stay\n"
    assert not dest_path.exists()


def test_custom_archive_date_prefix_and_suffix(tmp_path: Path, monkeypatch) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    now_path = tmp_path / "now.md"
    now_path.write_text("custom header\n", encoding="utf-8")
    _make_stale(now_path, 13.0)

    module = Archive()
    ctx = _ctx_for(now_path)
    ctx.config["archive_date_prefix"] = "### "
    ctx.config["archive_date_suffix"] = " // archived"
    system = System(
        event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert ignore == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert (
        past_path.read_text(encoding="utf-8")
        == "### 01.05.2026 // archived\ncustom header\n"
    )


def test_manual_archive_pair_archives_even_when_not_stale(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    now_path = tmp_path / "now.md"
    now_path.write_text("move now\n", encoding="utf-8")
    _make_stale(now_path, 1.0)

    module = Archive()
    ctx = _ctx_for(now_path, force_archive=True)
    system = System(
        event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert ignore == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert past_path.read_text(encoding="utf-8") == "--- 01.05.2026\nmove now\n"


def test_archive_command_prefers_configured_pair(tmp_path: Path, monkeypatch) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    now_path = tmp_path / "now.md"
    now_path.write_text("--archive\nmove by default\n", encoding="utf-8")
    _make_stale(now_path, 1.0)

    module = Archive()
    ctx = _ctx_for(now_path, archive_command=True)
    system = System(
        event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert ignore == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert past_path.read_text(encoding="utf-8") == "--- 01.05.2026\nmove by default\n"


def test_archive_command_uses_local_archive_when_no_pair(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    src_path = tmp_path / "note.md"
    src_path.write_text("--archive\nlocal fallback\n", encoding="utf-8")
    (tmp_path / ".archive").mkdir()

    module = Archive()
    ctx = _ctx_for(src_path, pair_values=[], archive_command=True)
    system = System(
        event=FileModifiedEvent(str(src_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    dest_path = tmp_path / ".archive" / "archive.md"
    assert ignore == {str(src_path.resolve()): 1, str(dest_path.resolve()): 1}
    assert src_path.read_text(encoding="utf-8") == ""
    assert dest_path.read_text(encoding="utf-8") == "--- 01.05.2026\nlocal fallback\n"


def test_archive_command_uses_global_when_no_pair_or_local_archive(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    repo_root = tmp_path / "repo"
    note_dir = repo_root / "notes"
    note_dir.mkdir(parents=True)
    git_dir = repo_root / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    src_path = note_dir / "note.md"
    src_path.write_text("--archive\nglobal fallback\n", encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(src_path, pair_values=[], archive_command=True)
    system = System(
        event=FileModifiedEvent(str(src_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    dest_path = repo_root / "archive.md"
    assert ignore == {str(src_path.resolve()): 1, str(dest_path.resolve()): 1}
    assert src_path.read_text(encoding="utf-8") == ""
    assert dest_path.read_text(encoding="utf-8") == "--- 01.05.2026\nglobal fallback\n"


def test_archive_does_not_copy_old_archive_command(tmp_path: Path, monkeypatch) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    now_path = tmp_path / "now.md"
    now_path.write_text(
        "--formatter-blank up --archive --formatter-todo\nreal text\n",
        encoding="utf-8",
    )
    _make_stale(now_path, 1.0)

    module = Archive()
    ctx = _ctx_for(now_path, force_archive=True)
    system = System(
        event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert ignore == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert (
        past_path.read_text(encoding="utf-8")
        == "--- 01.05.2026\n--formatter-blank up --formatter-todo\nreal text\n"
    )


def test_archive_pair_command_is_removed_from_archive_text(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    now_path = tmp_path / "now.md"
    now_path.write_text(
        "--formatter-blank up --archive-pair text --formatter-todo\nreal text\n",
        encoding="utf-8",
    )
    _make_stale(now_path, 1.0)

    module = Archive()
    ctx = _ctx_for(now_path, force_archive=True)
    system = System(
        event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert ignore == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert (
        past_path.read_text(encoding="utf-8")
        == "--- 01.05.2026\n--formatter-blank up --formatter-todo\nreal text\n"
    )


def test_archive_keeps_non_ascii_plain_text_without_extra_quotes(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    now_path = tmp_path / "now.md"
    now_path.write_text(
        "--- SECTION\n- alpha item\n- beta item\n- gamma item\n- delta item\n",
        encoding="utf-8",
    )
    _make_stale(now_path, 1.0)

    module = Archive()
    ctx = _ctx_for(now_path, force_archive=True)
    system = System(
        event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert ignore == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert (
        past_path.read_text(encoding="utf-8")
        == "--- 01.05.2026\n--- SECTION\n- alpha item\n- beta item\n- gamma item\n- delta item\n"
    )


def test_appends_to_end_of_past_without_overwrite(tmp_path: Path, monkeypatch) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    past_path = tmp_path / "past.md"
    past_path.write_text("--- 12.04\nsomethiung\n", encoding="utf-8")

    now_path = tmp_path / "now.md"
    now_path.write_text("more coffe\n", encoding="utf-8")
    _make_stale(now_path, 14.0)

    module = Archive()
    ctx = _ctx_for(now_path)
    system = System(
        event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module]
    )
    module.modified(ctx, system)

    expected = "--- 12.04\nsomethiung\n\n--- 01.05.2026\nmore coffe\n"
    assert past_path.read_text(encoding="utf-8") == expected
    assert now_path.read_text(encoding="utf-8") == ""


def test_appends_to_existing_archive_day_without_duplicate_header(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    past_path = tmp_path / "past.md"
    past_path.write_text("--- 01.05.2026\nfirst entry\n", encoding="utf-8")

    now_path = tmp_path / "now.md"
    now_path.write_text("second entry\n", encoding="utf-8")
    _make_stale(now_path, 14.0)

    module = Archive()
    ctx = _ctx_for(now_path)
    system = System(
        event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    assert ignore == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert (
        past_path.read_text(encoding="utf-8")
        == "--- 01.05.2026\nfirst entry\n\nsecond entry\n"
    )


def test_inserts_into_existing_archive_day_before_next_day(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    past_path = tmp_path / "past.md"
    past_path.write_text(
        "--- 01.05.2026\nfirst entry\n\n--- 02.05.2026\nlater entry\n",
        encoding="utf-8",
    )

    now_path = tmp_path / "now.md"
    now_path.write_text("second entry\n", encoding="utf-8")
    _make_stale(now_path, 14.0)

    module = Archive()
    ctx = _ctx_for(now_path)
    system = System(
        event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    assert ignore == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert (
        past_path.read_text(encoding="utf-8")
        == "--- 01.05.2026\nfirst entry\n\nsecond entry\n\n--- 02.05.2026\nlater entry\n"
    )


def test_skips_append_when_exact_archive_entry_already_exists(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    past_path = tmp_path / "past.md"
    past_path.write_text("--- 01.05.2026\nsame text\n", encoding="utf-8")

    now_path = tmp_path / "now.md"
    now_path.write_text("same text\n", encoding="utf-8")
    _make_stale(now_path, 14.0)

    module = Archive()
    ctx = _ctx_for(now_path)
    system = System(
        event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    assert ignore == {str(now_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert past_path.read_text(encoding="utf-8") == "--- 01.05.2026\nsame text\n"


def test_does_not_skip_append_on_partial_archive_text_match(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    past_path = tmp_path / "past.md"
    past_path.write_text("--- 30.04.2026\nsame text\n", encoding="utf-8")

    now_path = tmp_path / "now.md"
    now_path.write_text("same text\n", encoding="utf-8")
    _make_stale(now_path, 14.0)

    module = Archive()
    ctx = _ctx_for(now_path)
    system = System(
        event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    assert ignore == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert (
        past_path.read_text(encoding="utf-8")
        == "--- 30.04.2026\nsame text\n\n--- 01.05.2026\nsame text\n"
    )


def test_normalizes_blank_lines_before_archiving(tmp_path: Path, monkeypatch) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    now_path = tmp_path / "now.md"
    now_path.write_text("\n\nalpha\n\n\n\n\nbeta\n\n\n", encoding="utf-8")
    _make_stale(now_path, 14.0)

    module = Archive()
    ctx = _ctx_for(now_path)
    system = System(
        event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert ignore == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert (
        past_path.read_text(encoding="utf-8") == "--- 01.05.2026\nalpha\n\n\n\nbeta\n"
    )


def test_keeps_first_line_with_demon_lucy_flags_when_archiving(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    now_path = tmp_path / "now.md"
    now_path.write_text(
        "--formatter-blank up --formatter-todo\nalpha\nbeta\n",
        encoding="utf-8",
    )
    _make_stale(now_path, 14.0)

    module = Archive()
    ctx = _ctx_for(now_path)
    system = System(
        event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert ignore == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert (
        past_path.read_text(encoding="utf-8")
        == "--- 01.05.2026\n--formatter-blank up --formatter-todo\nalpha\nbeta\n"
    )


def test_uses_git_timestamp_when_repo_file_is_clean(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 6, 2)

    now_path = tmp_path / "now.md"
    now_path.write_text("from git clock\n", encoding="utf-8")
    _make_stale(now_path, 1.0)  # fresh by filesystem mtime

    now_ts = datetime(2026, 6, 2, 9, 0, 0).timestamp()
    monkeypatch.setattr(archive_clock.time, "time", lambda: now_ts)

    module = Archive()
    monkeypatch.setattr(file_time, "find_parent_git_repo", lambda _p: "/repo")

    git_commit_ts = now_ts - (13.0 * 3600.0)

    def _fake_run(cmd: list[str], **_kwargs):
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )
        if cmd[:4] == ["git", "log", "-1", "--format=%ct"]:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=f"{int(git_commit_ts)}\n",
                stderr="",
            )
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(file_time.subprocess, "run", _fake_run)

    ctx = _ctx_for(now_path)
    system = System(
        event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module]
    )
    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert ignore == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert past_path.read_text(encoding="utf-8") == "--- 01.06.2026\nfrom git clock\n"


def test_archive_header_uses_git_commit_date_for_dirty_forced_file(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 6, 2)

    now_path = tmp_path / "note.md"
    now_path.write_text("--archive-global\nold note text\n", encoding="utf-8")

    module = Archive()
    monkeypatch.setattr(file_time, "find_parent_git_repo", lambda _p: "/repo")

    git_commit_ts = datetime(2026, 5, 20, 9, 0, 0).timestamp()

    def _fake_run(cmd: list[str], **_kwargs):
        if cmd[:4] == ["git", "log", "-1", "--format=%ct"]:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=f"{int(git_commit_ts)}\n",
                stderr="",
            )
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(file_time.subprocess, "run", _fake_run)

    ctx = _ctx_for(
        now_path,
        pair_values=[],
        manual_route="global",
        global_dest_path="archive.md",
    )
    system = System(
        event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    dest_path = tmp_path / "archive.md"
    assert ignore == {str(now_path.resolve()): 1, str(dest_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert dest_path.read_text(encoding="utf-8") == "--- 20.05.2026\nold note text\n"


def test_force_fs_flag_skips_git_even_in_repo(tmp_path: Path, monkeypatch) -> None:
    now_path = tmp_path / "now.md"
    now_path.write_text("keep\n", encoding="utf-8")
    _make_stale(now_path, 1.0)

    module = Archive()
    monkeypatch.setattr(file_time, "find_parent_git_repo", lambda _p: "/repo")
    monkeypatch.setattr(
        file_time.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError(
                "git must not be called when --archive-force-filesystem-mtime is enabled"
            )
        ),
    )

    ctx = _ctx_for(now_path, force_fs=True)
    system = System(
        event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module]
    )
    ignore = module.modified(ctx, system)

    assert ignore is None
    assert now_path.read_text(encoding="utf-8") == "keep\n"
    assert not (tmp_path / "past.md").exists()
