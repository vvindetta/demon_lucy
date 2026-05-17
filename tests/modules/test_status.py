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
    status_banner_values: list[str] | None = None,
    status_dot: bool = False,
) -> Context:
    return Context(
        path=str(path),
        config={
            "status": list(status_values or []),
            "status_banner": list(status_banner_values or []),
            "status_dot": bool(status_dot),
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

    new_path = tmp_path / "17-05 08:09"
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

    new_path = tmp_path / "08:09 17-05"
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

    new_path = tmp_path / "17-05 08:09:00"
    assert changed == {str(path.resolve()): 1, str(new_path.resolve()): 1}
    assert new_path.exists()
    assert not path.exists()


def test_status_banner_renames_and_rotates_with_speed(tmp_path: Path, monkeypatch) -> None:
    now_state = {"value": 10.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])

    path = tmp_path / "note.md"
    path.write_text('--status-banner "Work sentence" 2000\n', encoding="utf-8")

    module = Status()
    system = System(event=FileModifiedEvent(str(path)), global_template=[], modules=[module])

    first_changed = module.modified(
        _ctx_for(path, status_banner_values=["Work sentence", "2000"]),
        system,
    )
    first_path = tmp_path / "Work sentence"
    assert first_changed == {str(path.resolve()): 1, str(first_path.resolve()): 1}
    assert first_path.exists()

    module._tick_once()
    assert first_path.exists()

    now_state["value"] = 12.1
    module._tick_once()
    second_path = tmp_path / "ork sentenceW"
    assert second_path.exists()
    assert not first_path.exists()


def test_status_banner_combines_with_status_tokens(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    path = tmp_path / "note.md"
    path.write_text('--status date time\n--status-banner "Focus now" 3000\n', encoding="utf-8")

    module = Status()
    ctx = _ctx_for(
        path,
        status_values=["date", "time"],
        status_banner_values=["Focus now", "3000"],
    )
    system = System(event=FileModifiedEvent(str(path)), global_template=[], modules=[module])

    changed = module.modified(ctx, system)

    new_path = tmp_path / "17-05 08:09 Focus now"
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


def test_status_git_update_uses_compact_units_and_ticks(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "note.md"
    path.write_text("--status git update\n", encoding="utf-8")

    now_state = {"value": 200000.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])
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
    first_path = tmp_path / "From last sync: 3h"
    second_path = tmp_path / "From last sync: 4h"

    assert first_changed == {str(path.resolve()): 1, str(first_path.resolve()): 1}
    assert first_path.exists()

    now_state["value"] = 203600.0
    module._tick_once()

    assert second_path.exists()
    assert not first_path.exists()


def test_status_ticker_interval_keeps_git_fast_window_temporary(monkeypatch) -> None:
    now_state = {"value": 1000.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])

    module = Status()
    module._set_tracked_parts("/tmp/git-status-note", ["git_update"])

    assert module._ticker_interval_seconds() == 2.0

    now_state["value"] = 1121.0
    assert module._ticker_interval_seconds() == 60.0


def test_status_ticker_interval_prefers_second_precision(monkeypatch) -> None:
    now_state = {"value": 1000.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])

    module = Status()
    module._set_tracked_parts("/tmp/seconds-status-note", ["time_with_seconds"])

    assert module._ticker_interval_seconds() == 1.0


def test_status_ticker_interval_uses_banner_speed(monkeypatch) -> None:
    now_state = {"value": 1000.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])

    module = Status()
    module._set_tracked_parts(
        "/tmp/banner-status-note",
        [],
        banner_text="Focus",
        banner_speed_ms=5000,
    )

    assert module._ticker_interval_seconds() == 5.0


def test_status_dot_prefixes_status_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    path = tmp_path / "note.md"
    path.write_text("--status date time\n--status-dot\n", encoding="utf-8")

    module = Status()
    ctx = _ctx_for(path, status_values=["date", "time"], status_dot=True)
    system = System(event=FileModifiedEvent(str(path)), global_template=[], modules=[module])

    changed = module.modified(ctx, system)
    new_path = tmp_path / ". 17-05 08:09"

    assert changed == {str(path.resolve()): 1, str(new_path.resolve()): 1}
    assert new_path.exists()
    assert not path.exists()


def test_status_banner_uses_max_chars_window(tmp_path: Path, monkeypatch) -> None:
    now_state = {"value": 10.0}
    monkeypatch.setattr(status_mod.time, "time", lambda: now_state["value"])

    path = tmp_path / "note.md"
    path.write_text('--status-banner "Working hard" 2000 4\n', encoding="utf-8")

    module = Status()
    system = System(event=FileModifiedEvent(str(path)), global_template=[], modules=[module])
    first_changed = module.modified(
        _ctx_for(path, status_banner_values=["Working hard", "2000", "4"]),
        system,
    )
    first_path = tmp_path / "Work"
    assert first_changed == {str(path.resolve()): 1, str(first_path.resolve()): 1}
    assert first_path.exists()

    module._tick_once()
    assert first_path.exists()

    now_state["value"] = 12.1
    module._tick_once()
    second_path = tmp_path / "orki"
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


def test_status_bootstrap_scans_dot_status_dir_after_restart_like_event(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(status_mod, "datetime", _FakeDateTime)

    notes_root = tmp_path / "notes"
    status_dir = notes_root / ".status"
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

    status_dir = notes_root / ".status"
    status_dir.mkdir(parents=True, exist_ok=True)
    status_file = status_dir / "late.md"
    status_file.write_text("--status time\n", encoding="utf-8")

    second_changed = module.modified(_ctx_for(trigger_file), system)
    assert second_changed is None
    assert status_file.exists()
