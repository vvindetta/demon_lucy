from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from watchdog.events import FileModifiedEvent

import lucy_notes_manager.modules.status as status_mod
from lucy_notes_manager.modules.abstract_module import Context, System
from lucy_notes_manager.modules.status import Status


@pytest.fixture(autouse=True)
def _disable_status_ticker(monkeypatch):
    class _DummyThread:
        def __init__(self, target=None, daemon=None):
            self.target = target
            self.daemon = daemon

        def start(self):
            return None

    monkeypatch.setattr(status_mod.threading, "Thread", _DummyThread)


class _FakeDateTime:
    _value = datetime(2026, 5, 17, 8, 9, 0)

    @classmethod
    def now(cls):
        return cls._value

    @classmethod
    def fromtimestamp(cls, value: float, tz):
        _ = tz
        return datetime.fromtimestamp(value, UTC)


def _ctx_for(
    path: Path,
    *,
    status_time: bool = False,
    status_date: bool = False,
    status_git: bool = False,
    arg_lines: dict[str, list[int]] | None = None,
) -> Context:
    return Context(
        path=str(path),
        config={
            "status_time": status_time,
            "status_date": status_date,
            "status_git": status_git,
        },
        arg_lines=arg_lines or {},
    )


def test_status_time_prefixes_filename_and_renames(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    path = tmp_path / "note.md"
    path.write_text("--status-time\n", encoding="utf-8")

    module = Status()
    ctx = _ctx_for(path, status_time=True)
    system = System(event=FileModifiedEvent(str(path)), global_template=[], modules=[module])

    changed = module.modified(ctx, system)

    new_path = tmp_path / "[t:08:09] note.md"
    assert changed == {str(path.resolve()): 1, str(new_path.resolve()): 1}
    assert new_path.exists()
    assert not path.exists()


def test_status_date_prefixes_filename(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    path = tmp_path / "today.md"
    path.write_text("--status-date\n", encoding="utf-8")

    module = Status()
    ctx = _ctx_for(path, status_date=True)
    system = System(event=FileModifiedEvent(str(path)), global_template=[], modules=[module])

    changed = module.modified(ctx, system)

    new_path = tmp_path / "[d:2026-05-17] today.md"
    assert changed == {str(path.resolve()): 1, str(new_path.resolve()): 1}
    assert new_path.exists()
    assert not path.exists()


def test_status_time_replaces_existing_time_prefix(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    old_path = tmp_path / "[t:08:08] note.md"
    old_path.write_text("--status-time\n", encoding="utf-8")

    module = Status()
    ctx = _ctx_for(old_path, status_time=True)
    system = System(
        event=FileModifiedEvent(str(old_path)),
        global_template=[],
        modules=[module],
    )

    changed = module.modified(ctx, system)

    new_path = tmp_path / "[t:08:09] note.md"
    assert changed == {str(old_path.resolve()): 1, str(new_path.resolve()): 1}
    assert new_path.exists()
    assert not old_path.exists()


def test_status_git_writes_last_sync_time_once(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "note.md"
    path.write_text("--status-git\n", encoding="utf-8")

    last_commit = 1_800_000_000.0  # 2027-01-15 08:00 UTC

    monkeypatch.setattr(status_mod, "find_parent_with", lambda _path, _marker: "/repo")

    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **_kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=f"{int(last_commit)}\n",
            stderr="",
        )

    monkeypatch.setattr(status_mod.subprocess, "run", _fake_run)

    module = Status()
    ctx = _ctx_for(path, status_git=True)
    system = System(event=FileModifiedEvent(str(path)), global_template=[], modules=[module])

    changed = module.modified(ctx, system)

    new_path = tmp_path / "[g:2027-01-15_08-00] note.md"
    assert calls == [["git", "log", "-1", "--format=%ct"]]
    assert changed == {str(path.resolve()): 1, str(new_path.resolve()): 1}
    assert new_path.exists()
    assert not path.exists()

    again = module.modified(_ctx_for(new_path, status_git=True), system)
    assert again is None
    assert new_path.exists()


def test_status_combines_date_time_and_git_tokens(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)
    monkeypatch.setattr(status_mod, "find_parent_with", lambda _path, _marker: "/repo")
    monkeypatch.setattr(
        status_mod.subprocess,
        "run",
        lambda cmd, **_kwargs: subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="1800000000\n",
            stderr="",
        ),
    )

    path = tmp_path / "note.md"
    path.write_text("--status-date --status-time --status-git\n", encoding="utf-8")

    module = Status()
    ctx = _ctx_for(path, status_date=True, status_time=True, status_git=True)
    system = System(event=FileModifiedEvent(str(path)), global_template=[], modules=[module])

    changed = module.modified(ctx, system)

    new_path = tmp_path / "[d:2026-05-17] [t:08:09] [g:2027-01-15_08-00] note.md"
    assert changed == {str(path.resolve()): 1, str(new_path.resolve()): 1}
    assert new_path.exists()
    assert not path.exists()


def test_status_git_update_uses_age_and_tracks_for_background(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "note.md"
    path.write_text("--status-git update\n", encoding="utf-8")

    now_ts = 200000.0
    last_commit = now_ts - (3.5 * 3600.0)

    monkeypatch.setattr(status_mod, "find_parent_with", lambda _path, _marker: "/repo")
    monkeypatch.setattr(status_mod.time, "time", lambda: now_ts)
    monkeypatch.setattr(
        status_mod.subprocess,
        "run",
        lambda cmd, **_kwargs: subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=f"{int(last_commit)}\n",
            stderr="",
        ),
    )

    module = Status()
    ctx = _ctx_for(path, status_git=True, arg_lines={"status_git": [1]})
    system = System(event=FileModifiedEvent(str(path)), global_template=[], modules=[module])

    changed = module.modified(ctx, system)
    new_path = tmp_path / "[g:3h] note.md"

    assert changed == {str(path.resolve()): 1, str(new_path.resolve()): 1}
    assert new_path.exists()
    assert not path.exists()


def test_status_tick_updates_tracked_filename_on_next_minute(
    tmp_path: Path, monkeypatch
) -> None:
    values = iter(
        [
            datetime(2026, 5, 17, 8, 9, 0),
            datetime(2026, 5, 17, 8, 10, 0),
        ]
    )

    class _StepDateTime:
        @classmethod
        def now(cls):
            return next(values)

    monkeypatch.setattr(status_mod, "datetime", _StepDateTime)

    path = tmp_path / "note.md"
    path.write_text("--status-time\n", encoding="utf-8")

    module = Status()
    ctx = _ctx_for(path, status_time=True)
    system = System(event=FileModifiedEvent(str(path)), global_template=[], modules=[module])

    first_changed = module.modified(ctx, system)
    first_path = tmp_path / "[t:08:09] note.md"
    second_path = tmp_path / "[t:08:10] note.md"

    assert first_changed == {str(path.resolve()): 1, str(first_path.resolve()): 1}
    assert first_path.exists()

    module._tick_once()

    assert second_path.exists()
    assert not first_path.exists()


def test_status_git_update_changes_in_background_tick(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "note.md"
    path.write_text("--status-git update\n", encoding="utf-8")

    now_values = iter([200000.0, 203600.0])  # 3h -> 4h
    monkeypatch.setattr(status_mod.time, "time", lambda: next(now_values))
    monkeypatch.setattr(status_mod, "find_parent_with", lambda _path, _marker: "/repo")
    monkeypatch.setattr(
        status_mod.subprocess,
        "run",
        lambda cmd, **_kwargs: subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=f"{int(200000.0 - (3.5 * 3600.0))}\n",
            stderr="",
        ),
    )

    module = Status()
    system = System(event=FileModifiedEvent(str(path)), global_template=[], modules=[module])

    first_changed = module.modified(
        _ctx_for(path, status_git=True, arg_lines={"status_git": [1]}),
        system,
    )
    first_path = tmp_path / "[g:3h] note.md"
    second_path = tmp_path / "[g:4h] note.md"

    assert first_changed == {str(path.resolve()): 1, str(first_path.resolve()): 1}
    assert first_path.exists()

    module._tick_once()

    assert second_path.exists()
    assert not first_path.exists()


def test_status_ticker_starts_only_after_first_status_tag(
    tmp_path: Path, monkeypatch
) -> None:
    starts = {"count": 0}

    class _CountThread:
        def __init__(self, target=None, daemon=None):
            self.target = target
            self.daemon = daemon

        def start(self):
            starts["count"] += 1

    monkeypatch.setattr(status_mod.threading, "Thread", _CountThread)
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    no_tag_path = tmp_path / "plain.md"
    no_tag_path.write_text("body\n", encoding="utf-8")
    tagged_path = tmp_path / "tag.md"
    tagged_path.write_text("--status-time\n", encoding="utf-8")

    module = Status()
    assert starts["count"] == 0

    system = System(event=FileModifiedEvent(str(no_tag_path)), global_template=[], modules=[module])
    changed_plain = module.modified(_ctx_for(no_tag_path), system)
    assert changed_plain is None
    assert starts["count"] == 0

    changed_tagged = module.modified(_ctx_for(tagged_path, status_time=True), system)
    assert changed_tagged is not None
    assert starts["count"] == 1
