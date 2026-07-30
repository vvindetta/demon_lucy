from __future__ import annotations

import os
import subprocess
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
from watchdog.events import FileModifiedEvent

from demon_lucy.lib import file_time
from demon_lucy.lib.args.models import ArgSource, KnownArg, ParsedArgs, Template
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.lib.dynamic_blocks.parser import format_dynamic_block
from demon_lucy.lib.notifications import NotificationProvider
from demon_lucy.lib.operating_system import OperatingSystem
from demon_lucy.modules.abstract_module import Context, System
from demon_lucy.modules.archive import Archive
from demon_lucy.modules.archive import clock as archive_clock
from demon_lucy.modules.archive import notify as archive_notify
from demon_lucy.modules.archive import storage as archive_storage
from demon_lucy.modules.archive.constants import ARCHIVE_TEMPLATE
from demon_lucy.modules.archive.types import ArchiveOutputMode
from tests.args_support import result_changes

_TEST_SYSTEM_TEMPLATE: Template = [
    KnownArg(name="sys-config-path", value_type=str, default=""),
    KnownArg(name="sys-watch-paths", value_type=str, default=[]),
    KnownArg(
        name="sys-notification-provider",
        value_type=NotificationProvider,
        default=NotificationProvider.DISABLE,
    ),
    KnownArg(
        name="sys-notification-min-interval-seconds",
        value_type=float,
        default=0.0,
    ),
    KnownArg(
        name="sys-notification-error-backoff-base-seconds",
        value_type=float,
        default=0.0,
    ),
    KnownArg(
        name="sys-notification-error-backoff-max-seconds",
        value_type=float,
        default=0.0,
    ),
    KnownArg(
        name="sys-notification-error-burst-limit",
        value_type=int,
        default=0,
    ),
    KnownArg(
        name="sys-notification-error-burst-window-seconds",
        value_type=float,
        default=0.0,
    ),
]
_TEST_TEMPLATE = [*ARCHIVE_TEMPLATE, *_TEST_SYSTEM_TEMPLATE]


def test_archive_default_mode_is_typed_enum() -> None:
    parsed = parse_args(args=[], template=ARCHIVE_TEMPLATE)

    assert parsed.unknown == ()
    assert parsed.require("archive-default-mode").value is ArchiveOutputMode.TEXT


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
    date_prefix: str = "--- ",
    date_suffix: str = "",
) -> Context:
    resolved_pair = (
        list(pair_values) if pair_values is not None else ["now.md", "past.md"]
    )
    values: dict[str, object] = {
        "archive-auto-pair": resolved_pair,
        "archive-auto-local": list(auto_local_values or []),
        "archive-auto-global": list(auto_global_values or []),
        "archive-global-dest-path": global_dest_path,
        "archive-date-prefix": date_prefix,
        "archive-date-suffix": date_suffix,
        "archive-force-filesystem-mtime": force_fs,
        "sys-config-path": config_path or "",
        "sys-watch-paths": list(watch_paths or []),
    }
    file_args: set[str] = set()
    if archive_command:
        values["archive"] = True
        file_args.add("archive")
    selected_manual_route = manual_route
    if force_archive and selected_manual_route is None:
        selected_manual_route = "pair"
    if selected_manual_route is not None:
        route_name = f"archive-{selected_manual_route}"
        values[route_name] = [manual_mode] if manual_mode else []
        file_args.add(route_name)

    defaults = parse_args(args=[], template=_TEST_TEMPLATE)
    args = ParsedArgs(
        known=tuple(
            replace(
                argument,
                value=values[argument.name],
                source=(
                    ArgSource.FILE if argument.name in file_args else ArgSource.CONFIG
                ),
                lines=(1,) if argument.name in file_args else (),
            )
            if argument.name in values
            else argument
            for argument in defaults.known
        ),
    )

    return Context(
        path=str(path),
        args=args,
        run_mode="oneshot",
        event_id="test",
        event=FileModifiedEvent(str(path)),
    )


def _system(
    module: Archive,
    operating_system: OperatingSystem = OperatingSystem.LINUX,
) -> System:
    return System(
        global_template=_TEST_TEMPLATE,
        modules=[module],
        operating_system=operating_system,
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
    system = _system(module)
    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert result_changes(ignore) == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
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
    system = _system(module)

    ignore = module.modified(ctx, system)

    assert result_changes(ignore) == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert past_path.read_text(encoding="utf-8") == "--- 02.05.2026\nunicode active\n"


def test_archive_pair_uses_source_root_for_event_from_another_watch_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 2)
    notifications: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        archive_notify,
        "safe_notify",
        lambda *args, **kwargs: notifications.append((args, kwargs)),
    )

    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    plasma_dir = tmp_path / "plasma"
    plasma_dir.mkdir()

    now_path = notes_dir / "now.md"
    now_path.write_text("archive from notes\n", encoding="utf-8")
    _make_stale(now_path, 3.0)
    past_path = notes_dir / "past.md"
    trigger_path = plasma_dir / "widget"
    trigger_path.write_text("widget event\n", encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(
        trigger_path,
        pair_values=[str(now_path), str(past_path), "2"],
        watch_paths=[str(plasma_dir), str(notes_dir)],
    )
    system = _system(module)

    ignore = module.modified(ctx, system)

    assert result_changes(ignore) == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert past_path.read_text(encoding="utf-8") == (
        "--- 02.05.2026\narchive from notes\n"
    )
    assert notifications == []


def test_archive_pair_still_rejects_source_outside_all_watch_roots(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 2)
    notifications: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        archive_notify,
        "safe_notify",
        lambda *args, **kwargs: notifications.append((args, kwargs)),
    )

    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    plasma_dir = tmp_path / "plasma"
    plasma_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()

    outside_path = outside_dir / "now.md"
    outside_path.write_text("must stay\n", encoding="utf-8")
    _make_stale(outside_path, 3.0)
    trigger_path = plasma_dir / "widget"
    trigger_path.write_text("widget event\n", encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(
        trigger_path,
        pair_values=[str(outside_path), str(notes_dir / "past.md"), "2"],
        watch_paths=[str(plasma_dir), str(notes_dir)],
    )
    system = _system(module)

    ignore = module.modified(ctx, system)

    assert ignore is None
    assert outside_path.read_text(encoding="utf-8") == "must stay\n"
    assert not (notes_dir / "past.md").exists()
    assert any(
        str(args[0]).startswith("archive-security:outside_allowed_root:")
        for args, _kwargs in notifications
    )


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
    system = _system(module)

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
    system = _system(module)

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
    system = _system(module)

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
    system = _system(module)

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
    system = _system(module)

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
    system = _system(module)

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
    system = _system(module)

    ignore = module.modified(ctx, system)

    past_path = note_dir / "past.md"
    assert result_changes(ignore) == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
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
    system = _system(module)

    ignore = module.modified(ctx, system)

    past_path = repo_root / "past.md"
    assert result_changes(ignore) == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
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
    system = _system(module)

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
    system = _system(module)

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
    system = _system(module)

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
    system = _system(module)

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
    system = _system(module)

    ignore = module.modified(ctx, system)

    assert ignore is None
    assert target_path.read_text(encoding="utf-8") == "target must stay\n"
    assert not (tmp_path / "past.md").exists()


def test_source_rewrite_validates_opened_file_before_truncating(
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
        operating_system: OperatingSystem,
        mode: int = 0o666,
    ) -> int:
        open_flags.append(flags)
        return os.open(hardlink_path, os.O_WRONLY)

    monkeypatch.setattr(archive_storage, "open_file_no_follow", open_replaced_path)

    assert (
        archive_storage.write_text_no_follow(
            str(source_path),
            "",
            operating_system=OperatingSystem.LINUX,
        )
        is False
    )
    assert open_flags == [os.O_WRONLY | os.O_CREAT]
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
    system = _system(module)

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert result_changes(ignore) == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert past_path.read_text(encoding="utf-8") == expected_past_text


def test_does_not_archive_when_file_is_not_stale(tmp_path: Path) -> None:
    now_path = tmp_path / "now.md"
    now_path.write_text("keep\n", encoding="utf-8")
    _make_stale(now_path, 1.0)

    module = Archive()
    ctx = _ctx_for(now_path)
    system = _system(module)

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
    system = _system(module)

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "history.md"
    assert result_changes(ignore) == {str(active_path.resolve()): 1, str(past_path.resolve()): 1}
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
    system = _system(module)

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "history.md"
    assert result_changes(ignore) == {str(active_path.resolve()): 1, str(past_path.resolve()): 1}
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
        pair_values=["active.md", "history.md", "zip"],
    )
    system = _system(module)

    ignore = module.modified(ctx, system)

    assert ignore is None
    assert notifications
    assert str(notifications[0][0][0]).startswith(
        "archive-rule:--archive-auto-pair:invalid_trailing_token:"
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
    active_path = tmp_path / "active.md"
    active_path.write_text("cannot truncate\n", encoding="utf-8")
    _make_stale(active_path, 13.0)

    trigger_path = tmp_path / "other.md"
    trigger_path.write_text("x\n", encoding="utf-8")

    original_write = archive_storage.write_text_no_follow

    def fail_source_write(path_value, content, *, operating_system):
        if path_value == str(active_path.resolve()):
            return False
        return original_write(
            path_value,
            content,
            operating_system=operating_system,
        )

    monkeypatch.setattr(
        archive_storage,
        "write_text_no_follow",
        fail_source_write,
    )

    module = Archive()
    ctx = _ctx_for(
        trigger_path,
        pair_values=["active.md", "history.md"],
    )
    system = _system(module)

    ignore = module.modified(ctx, system)

    assert ignore is None
    assert notifications
    assert str(notifications[0][0][0]).startswith(
        "archive-operation:rewrite_source_failed:"
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
    system = _system(module)

    ignore = module.modified(ctx, system)

    dest_path = tmp_path / "journal.md"
    assert result_changes(ignore) == {str(src_path.resolve()): 1, str(dest_path.resolve()): 1}
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
    system = _system(module)

    ignore = module.modified(ctx, system)

    dest_path = tmp_path / ".archive" / "archive.md"
    assert result_changes(ignore) == {str(src_path.resolve()): 1, str(dest_path.resolve()): 1}
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
    system = _system(module)

    ignore = module.modified(ctx, system)

    dest_path = tmp_path / ".archive" / "2026-05-01---note.md"
    assert result_changes(ignore) == {str(src_path.resolve()): 1, str(dest_path.resolve()): 1}
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
    system = _system(module)

    ignore = module.modified(ctx, system)

    dest_path = archive_dir / "2026-05-01---note-2.md"
    assert result_changes(ignore) == {str(src_path.resolve()): 1, str(dest_path.resolve()): 1}
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
    system = _system(module)

    ignore = module.modified(ctx, system)

    dest_path = repo_root / "archive.md"
    assert result_changes(ignore) == {str(src_path.resolve()): 1, str(dest_path.resolve()): 1}
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
    system = _system(module)

    ignore = module.modified(ctx, system)

    dest_path = tmp_path / ".archive" / "2026-05-01---now.md"
    assert result_changes(ignore) == {str(src_path.resolve()): 1, str(dest_path.resolve()): 1}
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
    system = _system(module)

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
    system = _system(module)

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
    ctx = _ctx_for(
        now_path,
        date_prefix="### ",
        date_suffix=" // archived",
    )
    system = _system(module)

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert result_changes(ignore) == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
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
    system = _system(module)

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert result_changes(ignore) == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert past_path.read_text(encoding="utf-8") == "--- 01.05.2026\nmove now\n"


def test_archive_text_keeps_dynamic_blocks_in_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)
    block = format_dynamic_block(
        arg="graph",
        params={"source": "past.md", "pattern": "sleep"},
        body="generated graph",
        updated_timestamp=1_800_000_000.0,
    )
    now_path = tmp_path / "now.md"
    now_path.write_text(
        "archive before\n\n" + block + "archive after\n",
        encoding="utf-8",
    )

    module = Archive()
    ctx = _ctx_for(now_path, force_archive=True)
    system = _system(module)

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert result_changes(ignore) == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == block
    assert past_path.read_text(encoding="utf-8") == (
        "--- 01.05.2026\narchive before\n\narchive after\n"
    )


def test_archive_file_keeps_dynamic_blocks_in_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)
    block = format_dynamic_block(
        arg="include",
        params={"source": "todo.md"},
        body="generated include",
        updated_timestamp=1_800_000_000.0,
    )
    source_path = tmp_path / "note.md"
    source_path.write_text(
        "--archive-local file\narchive me\n\n" + block,
        encoding="utf-8",
    )

    module = Archive()
    ctx = _ctx_for(
        source_path,
        pair_values=[],
        manual_route="local",
        manual_mode="file",
    )
    system = _system(module)

    ignore = module.modified(ctx, system)

    dest_path = tmp_path / ".archive" / "2026-05-01---note.md"
    assert result_changes(ignore) == {str(source_path.resolve()): 1, str(dest_path.resolve()): 1}
    assert source_path.read_text(encoding="utf-8") == block
    assert dest_path.read_text(encoding="utf-8") == "archive me\n"


def test_archive_skips_source_containing_only_dynamic_blocks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)
    block = format_dynamic_block(
        arg="graph",
        params={"pattern": "sleep"},
        body="generated graph",
        updated_timestamp=1_800_000_000.0,
    )
    source_path = tmp_path / "now.md"
    source_path.write_text(block, encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(source_path, force_archive=True)
    system = _system(module)

    ignore = module.modified(ctx, system)

    assert ignore is None
    assert source_path.read_text(encoding="utf-8") == block
    assert not (tmp_path / "past.md").exists()


def test_archive_rejects_malformed_dynamic_block(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)
    notifications: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        archive_notify,
        "safe_notify",
        lambda *args, **kwargs: notifications.append((args, kwargs)),
    )
    source_text = "archive me\n--- graph begin ---\n- pattern: sleep\n"
    source_path = tmp_path / "now.md"
    source_path.write_text(source_text, encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(source_path, force_archive=True)
    system = _system(module)

    ignore = module.modified(ctx, system)

    assert ignore is None
    assert source_path.read_text(encoding="utf-8") == source_text
    assert not (tmp_path / "past.md").exists()
    assert notifications
    assert str(notifications[0][0][0]).startswith(
        "archive-operation:invalid_dynamic_blocks:"
    )


def test_archive_command_prefers_configured_pair(tmp_path: Path, monkeypatch) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    now_path = tmp_path / "now.md"
    now_path.write_text("--archive\nmove by default\n", encoding="utf-8")
    _make_stale(now_path, 1.0)

    module = Archive()
    ctx = _ctx_for(now_path, archive_command=True)
    system = _system(module)

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert result_changes(ignore) == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
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
    system = _system(module)

    ignore = module.modified(ctx, system)

    dest_path = tmp_path / ".archive" / "archive.md"
    assert result_changes(ignore) == {str(src_path.resolve()): 1, str(dest_path.resolve()): 1}
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
    system = _system(module)

    ignore = module.modified(ctx, system)

    dest_path = repo_root / "archive.md"
    assert result_changes(ignore) == {str(src_path.resolve()): 1, str(dest_path.resolve()): 1}
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
    system = _system(module)

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert result_changes(ignore) == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
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
    system = _system(module)

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert result_changes(ignore) == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
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
    system = _system(module)

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert result_changes(ignore) == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
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
    system = _system(module)
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
    system = _system(module)

    ignore = module.modified(ctx, system)

    assert result_changes(ignore) == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
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
    system = _system(module)

    ignore = module.modified(ctx, system)

    assert result_changes(ignore) == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
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
    system = _system(module)

    ignore = module.modified(ctx, system)

    assert result_changes(ignore) == {str(now_path.resolve()): 1}
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
    system = _system(module)

    ignore = module.modified(ctx, system)

    assert result_changes(ignore) == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
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
    system = _system(module)

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert result_changes(ignore) == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
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
    system = _system(module)

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert result_changes(ignore) == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
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
    system = _system(module)
    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert result_changes(ignore) == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
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
    system = _system(module)

    ignore = module.modified(ctx, system)

    dest_path = tmp_path / "archive.md"
    assert result_changes(ignore) == {str(now_path.resolve()): 1, str(dest_path.resolve()): 1}
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
    system = _system(module)
    ignore = module.modified(ctx, system)

    assert ignore is None
    assert now_path.read_text(encoding="utf-8") == "keep\n"
    assert not (tmp_path / "past.md").exists()
