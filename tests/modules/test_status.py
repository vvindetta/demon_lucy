from __future__ import annotations

import subprocess
from datetime import datetime
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


def _ctx_for(
    path: Path,
    *,
    status_values: list[str] | None = None,
) -> Context:
    return Context(
        path=str(path),
        config={
            "status": list(status_values or []),
        },
        arg_lines={},
    )


def test_status_date_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    path = tmp_path / "note.md"
    path.write_text("--status date\n", encoding="utf-8")

    module = Status()
    ctx = _ctx_for(path, status_values=["date"])
    system = System(event=FileModifiedEvent(str(path)), global_template=[], modules=[module])

    changed = module.modified(ctx, system)

    new_path = tmp_path / "17-05"
    assert changed == {str(path.resolve()): 1, str(new_path.resolve()): 1}
    assert new_path.exists()
    assert not path.exists()


def test_status_date_time_order(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    path = tmp_path / "note.md"
    path.write_text("--status date time\n", encoding="utf-8")

    module = Status()
    ctx = _ctx_for(path, status_values=["date", "time"])
    system = System(event=FileModifiedEvent(str(path)), global_template=[], modules=[module])

    changed = module.modified(ctx, system)

    new_path = tmp_path / "17-05 | 08:09"
    assert changed == {str(path.resolve()): 1, str(new_path.resolve()): 1}
    assert new_path.exists()
    assert not path.exists()


def test_status_time_date_order(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    path = tmp_path / "note.md"
    path.write_text("--status time date\n", encoding="utf-8")

    module = Status()
    ctx = _ctx_for(path, status_values=["time", "date"])
    system = System(event=FileModifiedEvent(str(path)), global_template=[], modules=[module])

    changed = module.modified(ctx, system)

    new_path = tmp_path / "08:09 | 17-05"
    assert changed == {str(path.resolve()): 1, str(new_path.resolve()): 1}
    assert new_path.exists()
    assert not path.exists()


def test_status_time_with_seconds_order(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    path = tmp_path / "note.md"
    path.write_text("--status date time-with-seconds\n", encoding="utf-8")

    module = Status()
    ctx = _ctx_for(path, status_values=["date", "time-with-seconds"])
    system = System(event=FileModifiedEvent(str(path)), global_template=[], modules=[module])

    changed = module.modified(ctx, system)

    new_path = tmp_path / "17-05 | 08:09:00"
    assert changed == {str(path.resolve()): 1, str(new_path.resolve()): 1}
    assert new_path.exists()
    assert not path.exists()


def test_status_git_writes_sync_timestamp_once(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "note.md"
    path.write_text("--status git\n", encoding="utf-8")

    last_commit = 1_800_000_000.0

    monkeypatch.setattr(status_mod, "find_parent_with", lambda _path, _marker: "/repo")
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
    system = System(event=FileModifiedEvent(str(path)), global_template=[], modules=[module])

    first_changed = module.modified(_ctx_for(path, status_values=["git"]), system)
    first_path = tmp_path / "Last Git Sync: 1800000000"

    assert first_changed == {str(path.resolve()): 1, str(first_path.resolve()): 1}
    assert first_path.exists()

    second_changed = module.modified(_ctx_for(first_path, status_values=["git"]), system)
    assert second_changed is None
    assert first_path.exists()


def test_status_git_update_uses_minutes_and_ticks(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "note.md"
    path.write_text("--status git update\n", encoding="utf-8")

    now_values = iter([200000.0, 203600.0])  # 210m -> 270m
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

    first_changed = module.modified(_ctx_for(path, status_values=["git", "update"]), system)
    first_path = tmp_path / "From last Git sync: 210"
    second_path = tmp_path / "From last Git sync: 270"

    assert first_changed == {str(path.resolve()): 1, str(first_path.resolve()): 1}
    assert first_path.exists()

    module._tick_once()

    assert second_path.exists()
    assert not first_path.exists()


def test_status_ticker_starts_only_after_first_status_use(tmp_path: Path, monkeypatch) -> None:
    starts = {"count": 0}

    class _CountThread:
        def __init__(self, target=None, daemon=None):
            self.target = target
            self.daemon = daemon

        def start(self):
            starts["count"] += 1

    monkeypatch.setattr(status_mod.threading, "Thread", _CountThread)
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    no_status_path = tmp_path / "plain.md"
    no_status_path.write_text("body\\n", encoding="utf-8")
    status_path = tmp_path / "tag.md"
    status_path.write_text("--status time\\n", encoding="utf-8")

    module = Status()
    assert starts["count"] == 0

    system = System(event=FileModifiedEvent(str(no_status_path)), global_template=[], modules=[module])

    changed_plain = module.modified(_ctx_for(no_status_path), system)
    assert changed_plain is None
    assert starts["count"] == 0

    changed_status = module.modified(_ctx_for(status_path, status_values=["time"]), system)
    assert changed_status is not None
    assert starts["count"] == 1


def test_status_bootstrap_scans__status_dir_after_restart_like_event(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    notes_root = tmp_path / "notes"
    status_dir = notes_root / "_status"
    status_dir.mkdir(parents=True, exist_ok=True)

    status_file = status_dir / "dead.md"
    status_file.write_text("--status time\n", encoding="utf-8")

    trigger_file = notes_root / "random.md"
    trigger_file.write_text("body\n", encoding="utf-8")

    module = Status()
    system = System(
        event=FileModifiedEvent(str(trigger_file)),
        global_template=[],
        modules=[module],
    )

    changed = module.modified(_ctx_for(trigger_file), system)

    revived_path = status_dir / "08:09"
    assert changed == {str(status_file.resolve()): 1, str(revived_path.resolve()): 1}
    assert revived_path.exists()
    assert not status_file.exists()


def test_status_bootstrap_scans_only_once_even_if_first_scan_found_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    notes_root = tmp_path / "notes"
    notes_root.mkdir(parents=True, exist_ok=True)
    trigger_file = notes_root / "random.md"
    trigger_file.write_text("body\n", encoding="utf-8")

    module = Status()
    system = System(
        event=FileModifiedEvent(str(trigger_file)),
        global_template=[],
        modules=[module],
    )

    first_changed = module.modified(_ctx_for(trigger_file), system)
    assert first_changed is None

    status_dir = notes_root / "_status"
    status_dir.mkdir(parents=True, exist_ok=True)
    status_file = status_dir / "late.md"
    status_file.write_text("--status time\n", encoding="utf-8")

    second_changed = module.modified(_ctx_for(trigger_file), system)
    assert second_changed is None
    assert status_file.exists()
