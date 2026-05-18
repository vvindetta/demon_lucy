from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytest
from watchdog.events import FileModifiedEvent

import lucy_notes_manager.modules.today as today_mod
from lucy_notes_manager.modules.abstract_module import Context, System
from lucy_notes_manager.modules.today import Today


def _ctx_for(
    path: Path,
    *,
    force_fs: bool = False,
    now_path: str = "now.md",
    past_path: str = "past.md",
) -> Context:
    config: dict[str, object] = {
        "today_now_path": now_path,
        "today_past_path": past_path,
        "today_idle_hours": 12.0,
        "today_force_filesystem_mtime": False,
    }
    if force_fs:
        config["today_force_filesystem_mtime"] = True

    return Context(
        path=str(path),
        config=config,
        arg_lines={},
    )


def test_supports_custom_today_now_file(tmp_path: Path, monkeypatch) -> None:
    _freeze_now(monkeypatch, 2026, 5, 2)

    now_path = tmp_path / "active.md"
    now_path.write_text("custom active\n", encoding="utf-8")
    _make_stale(now_path, 13.0)

    trigger_path = tmp_path / "other.md"
    trigger_path.write_text("x\n", encoding="utf-8")

    module = Today()
    ctx = _ctx_for(trigger_path, now_path="active.md")
    system = System(event=FileModifiedEvent(str(trigger_path)), global_template=[], modules=[module])
    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert ignore == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert past_path.read_text(encoding="utf-8") == "-- 02.05\ncustom active\n"


def _make_stale(path: Path, hours: float) -> None:
    old = time.time() - (hours * 3600.0)
    os.utime(path, (old, old))


def _freeze_now(monkeypatch, year: int, month: int, day: int) -> None:
    class _FakeDatetime:
        @classmethod
        def now(cls):
            return datetime(year, month, day, 9, 0, 0)

    monkeypatch.setattr(today_mod, "datetime", _FakeDatetime)


@pytest.mark.parametrize(
    ("event_target_name", "expected_past_text"),
    [
        ("now.md", "-- 01.05\nsomething\nmore coffee\n"),
        ("other.md", "-- 01.05\narchive me\n"),
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

    module = Today()
    ctx = _ctx_for(trigger_path)
    system = System(event=FileModifiedEvent(str(trigger_path)), global_template=[], modules=[module])

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert ignore == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert past_path.read_text(encoding="utf-8") == expected_past_text


def test_does_not_archive_when_file_is_not_stale(tmp_path: Path) -> None:
    now_path = tmp_path / "now.md"
    now_path.write_text("keep\n", encoding="utf-8")
    _make_stale(now_path, 1.0)

    module = Today()
    ctx = _ctx_for(now_path)
    system = System(event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module])

    ignore = module.modified(ctx, system)

    assert ignore is None
    assert now_path.read_text(encoding="utf-8") == "keep\n"
    assert not (tmp_path / "past.md").exists()


def test_appends_to_end_of_past_without_overwrite(tmp_path: Path, monkeypatch) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    past_path = tmp_path / "past.md"
    past_path.write_text("-- 12.04\nsomethiung\n", encoding="utf-8")

    now_path = tmp_path / "now.md"
    now_path.write_text("more coffe\n", encoding="utf-8")
    _make_stale(now_path, 14.0)

    module = Today()
    ctx = _ctx_for(now_path)
    system = System(event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module])
    module.modified(ctx, system)

    expected = "-- 12.04\nsomethiung\n\n-- 01.05\nmore coffe\n"
    assert past_path.read_text(encoding="utf-8") == expected
    assert now_path.read_text(encoding="utf-8") == ""


def test_normalizes_blank_lines_before_archiving(tmp_path: Path, monkeypatch) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    now_path = tmp_path / "now.md"
    now_path.write_text("\n\nalpha\n\n\n\n\nbeta\n\n\n", encoding="utf-8")
    _make_stale(now_path, 14.0)

    module = Today()
    ctx = _ctx_for(now_path)
    system = System(event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module])

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert ignore == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert past_path.read_text(encoding="utf-8") == "-- 01.05\nalpha\n\n\n\nbeta\n"


def test_keeps_first_line_with_lucy_flags_when_archiving(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    now_path = tmp_path / "now.md"
    now_path.write_text(
        "--fmt-blank up --fmt-todo\nalpha\nbeta\n",
        encoding="utf-8",
    )
    _make_stale(now_path, 14.0)

    module = Today()
    ctx = _ctx_for(now_path)
    system = System(event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module])

    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert ignore == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert (
        past_path.read_text(encoding="utf-8")
        == "-- 01.05\n--fmt-blank up --fmt-todo\nalpha\nbeta\n"
    )


def test_uses_git_timestamp_when_repo_file_is_clean(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 6, 2)

    now_path = tmp_path / "now.md"
    now_path.write_text("from git clock\n", encoding="utf-8")
    _make_stale(now_path, 1.0)  # fresh by filesystem mtime

    now_ts = time.time()
    monkeypatch.setattr(today_mod.time, "time", lambda: now_ts)

    module = Today()
    monkeypatch.setattr(today_mod, "find_parent_with", lambda _p, _m: "/repo")

    git_commit_ts = now_ts - (13.0 * 3600.0)

    def _fake_run(cmd: list[str], **_kwargs):
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if cmd[:4] == ["git", "log", "-1", "--format=%ct"]:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=f"{int(git_commit_ts)}\n",
                stderr="",
            )
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(today_mod.subprocess, "run", _fake_run)

    ctx = _ctx_for(now_path)
    system = System(event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module])
    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert ignore == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert past_path.read_text(encoding="utf-8") == "-- 02.06\nfrom git clock\n"


def test_force_fs_flag_skips_git_even_in_repo(tmp_path: Path, monkeypatch) -> None:
    now_path = tmp_path / "now.md"
    now_path.write_text("keep\n", encoding="utf-8")
    _make_stale(now_path, 1.0)

    module = Today()
    monkeypatch.setattr(today_mod, "find_parent_with", lambda _p, _m: "/repo")
    monkeypatch.setattr(
        today_mod.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("git must not be called when --today-force-filesystem-mtime is enabled")
        ),
    )

    ctx = _ctx_for(now_path, force_fs=True)
    system = System(event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module])
    ignore = module.modified(ctx, system)

    assert ignore is None
    assert now_path.read_text(encoding="utf-8") == "keep\n"
    assert not (tmp_path / "past.md").exists()
