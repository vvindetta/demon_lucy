from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytest
from watchdog.events import FileModifiedEvent

import demon_lucy.modules.archive as archive_mod
from demon_lucy.modules.abstract_module import Context, System
from demon_lucy.modules.archive import Archive


def _ctx_for(
    path: Path,
    *,
    force_fs: bool = False,
    force_archive: bool = False,
    pair_values: list[str] | None = None,
    default_dest_path: str = "past.md",
) -> Context:
    resolved_pair = list(pair_values) if pair_values is not None else ["now.md", "past.md"]
    config: dict[str, object] = {
        "archive": False,
        "archive_pair": resolved_pair,
        "archive_default_dest_path": default_dest_path,
        "archive_idle_hours": 12.0,
        "archive_date_prefix": "-- ",
        "archive_date_suffix": "",
        "archive_force_filesystem_mtime": False,
    }
    if force_fs:
        config["archive_force_filesystem_mtime"] = True
    if force_archive:
        config["archive"] = True

    return Context(
        path=str(path),
        config=config,
        arg_lines={},
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
    assert past_path.read_text(encoding="utf-8") == "-- 02.05.2026\ncustom active\n"


def test_supports_absolute_unicode_archive_pair_paths(
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
    assert past_path.read_text(encoding="utf-8") == "-- 02.05.2026\nunicode active\n"


def test_relative_past_is_anchored_to_absolute_now_path(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 2)

    active_dir = tmp_path / "active"
    active_dir.mkdir(parents=True, exist_ok=True)
    now_path = active_dir / "now.md"
    now_path.write_text("move from fixed now\n", encoding="utf-8")
    _make_stale(now_path, 13.0)

    random_dir = tmp_path / "random"
    random_dir.mkdir(parents=True, exist_ok=True)
    trigger_path = random_dir / "other.md"
    trigger_path.write_text("x\n", encoding="utf-8")

    module = Archive()
    ctx = _ctx_for(trigger_path, pair_values=[str(now_path), "past.md"])
    system = System(
        event=FileModifiedEvent(str(trigger_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    expected_past = active_dir / "past.md"
    assert ignore == {str(now_path.resolve()): 1, str(expected_past.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert expected_past.read_text(encoding="utf-8") == "-- 02.05.2026\nmove from fixed now\n"
    assert not (random_dir / "past.md").exists()


def _make_stale(path: Path, hours: float) -> None:
    old = time.time() - (hours * 3600.0)
    os.utime(path, (old, old))


def _freeze_now(monkeypatch, year: int, month: int, day: int) -> None:
    class _FakeDatetime:
        @classmethod
        def now(cls):
            return datetime(year, month, day, 9, 0, 0)

    monkeypatch.setattr(archive_mod, "datetime", _FakeDatetime)


@pytest.mark.parametrize(
    ("event_target_name", "expected_past_text"),
    [
        ("now.md", "-- 01.05.2026\nsomething\nmore coffee\n"),
        ("other.md", "-- 01.05.2026\narchive me\n"),
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
    assert past_path.read_text(encoding="utf-8") == "-- 01.05.2026\nmove with compact arg\n"


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
    assert past_path.read_text(encoding="utf-8") == "-- 01.05.2026\nuse default idle\n"


def test_uses_default_dest_when_pair_is_missing_with_archive_flag(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    src_path = tmp_path / "note.md"
    src_path.write_text("fallback archive\n", encoding="utf-8")
    _make_stale(src_path, 13.0)

    module = Archive()
    ctx = _ctx_for(
        src_path,
        force_archive=True,
        pair_values=[],
        default_dest_path="journal.md",
    )
    system = System(
        event=FileModifiedEvent(str(src_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    dest_path = tmp_path / "journal.md"
    assert ignore == {str(src_path.resolve()): 1, str(dest_path.resolve()): 1}
    assert src_path.read_text(encoding="utf-8") == ""
    assert dest_path.read_text(encoding="utf-8") == "-- 01.05.2026\nfallback archive\n"


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
        default_dest_path="journal.md",
    )
    system = System(
        event=FileModifiedEvent(str(src_path)), global_template=[], modules=[module]
    )

    ignore = module.modified(ctx, system)

    dest_path = tmp_path / "journal.md"
    assert ignore is None
    assert src_path.read_text(encoding="utf-8") == "must stay\n"
    assert not dest_path.exists()


def test_custom_archive_date_prefix_and_suffix(
    tmp_path: Path, monkeypatch
) -> None:
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


def test_archive_flag_archives_even_when_not_stale(
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
    assert past_path.read_text(encoding="utf-8") == "-- 01.05.2026\nmove now\n"


def test_archive_flag_does_not_archive_archive_command(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    now_path = tmp_path / "now.md"
    now_path.write_text(
        "--fmt-blank up --archive --fmt-todo\nreal text\n",
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
        == "-- 01.05.2026\n--fmt-blank up --fmt-todo\nreal text\n"
    )


def test_archive_pair_command_is_removed_from_archive_text(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    now_path = tmp_path / "now.md"
    now_path.write_text(
        "--fmt-blank up --archive --archive-pair now.md past.md 1 --fmt-todo\nreal text\n",
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
        == "-- 01.05.2026\n--fmt-blank up --fmt-todo\nreal text\n"
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
        == "-- 01.05.2026\n--- SECTION\n- alpha item\n- beta item\n- gamma item\n- delta item\n"
    )


def test_appends_to_end_of_past_without_overwrite(tmp_path: Path, monkeypatch) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    past_path = tmp_path / "past.md"
    past_path.write_text("-- 12.04\nsomethiung\n", encoding="utf-8")

    now_path = tmp_path / "now.md"
    now_path.write_text("more coffe\n", encoding="utf-8")
    _make_stale(now_path, 14.0)

    module = Archive()
    ctx = _ctx_for(now_path)
    system = System(
        event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module]
    )
    module.modified(ctx, system)

    expected = "-- 12.04\nsomethiung\n\n-- 01.05.2026\nmore coffe\n"
    assert past_path.read_text(encoding="utf-8") == expected
    assert now_path.read_text(encoding="utf-8") == ""


def test_skips_append_when_exact_archive_entry_already_exists(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    past_path = tmp_path / "past.md"
    past_path.write_text("-- 01.05.2026\nsame text\n", encoding="utf-8")

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
    assert past_path.read_text(encoding="utf-8") == "-- 01.05.2026\nsame text\n"


def test_does_not_skip_append_on_partial_archive_text_match(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    past_path = tmp_path / "past.md"
    past_path.write_text("-- 30.04.2026\nsame text\n", encoding="utf-8")

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
        == "-- 30.04.2026\nsame text\n\n-- 01.05.2026\nsame text\n"
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
    assert past_path.read_text(encoding="utf-8") == "-- 01.05.2026\nalpha\n\n\n\nbeta\n"


def test_keeps_first_line_with_demon_lucy_flags_when_archiving(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 5, 1)

    now_path = tmp_path / "now.md"
    now_path.write_text(
        "--fmt-blank up --fmt-todo\nalpha\nbeta\n",
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
        == "-- 01.05.2026\n--fmt-blank up --fmt-todo\nalpha\nbeta\n"
    )


def test_uses_git_timestamp_when_repo_file_is_clean(
    tmp_path: Path, monkeypatch
) -> None:
    _freeze_now(monkeypatch, 2026, 6, 2)

    now_path = tmp_path / "now.md"
    now_path.write_text("from git clock\n", encoding="utf-8")
    _make_stale(now_path, 1.0)  # fresh by filesystem mtime

    now_ts = time.time()
    monkeypatch.setattr(archive_mod.time, "time", lambda: now_ts)

    module = Archive()
    monkeypatch.setattr(archive_mod, "find_parent_with", lambda _p, _m: "/repo")

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

    monkeypatch.setattr(archive_mod.subprocess, "run", _fake_run)

    ctx = _ctx_for(now_path)
    system = System(
        event=FileModifiedEvent(str(now_path)), global_template=[], modules=[module]
    )
    ignore = module.modified(ctx, system)

    past_path = tmp_path / "past.md"
    assert ignore == {str(now_path.resolve()): 1, str(past_path.resolve()): 1}
    assert now_path.read_text(encoding="utf-8") == ""
    assert past_path.read_text(encoding="utf-8") == "-- 02.06.2026\nfrom git clock\n"


def test_force_fs_flag_skips_git_even_in_repo(tmp_path: Path, monkeypatch) -> None:
    now_path = tmp_path / "now.md"
    now_path.write_text("keep\n", encoding="utf-8")
    _make_stale(now_path, 1.0)

    module = Archive()
    monkeypatch.setattr(archive_mod, "find_parent_with", lambda _p, _m: "/repo")
    monkeypatch.setattr(
        archive_mod.subprocess,
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
