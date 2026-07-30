from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from watchdog.events import (
    FileModifiedEvent,
    FileMovedEvent,
    FileOpenedEvent,
    FileSystemEvent,
)

from demon_lucy.file_handler import FileHandler
from demon_lucy.lib.args.models import KnownArg, ParsedArgs
from demon_lucy.module_manager import ModuleManager


class _FakeModules:
    def __init__(
        self,
        ignore_maps: list[dict[str, int] | None] | None = None,
        values: dict | None = None,
    ) -> None:
        self.calls = 0
        self.paths: list[str] = []
        self._ignore_maps = list(ignore_maps or [])
        resolved_values = {
            "sys_watch_paths": [],
            "sys_ignore_move_paths": [".status"],
        }
        resolved_values.update(values or {})
        self.args = ParsedArgs(
            known=tuple(
                KnownArg(
                    name=key.replace("_", "-"),
                    value=value,
                )
                for key, value in resolved_values.items()
            )
        )

    def run(
        self,
        path: str,
        event: FileSystemEvent,
        event_id: str | None = None,
    ) -> dict[str, int] | None:
        _ = (event, event_id)
        self.calls += 1
        self.paths.append(path)
        if self._ignore_maps:
            return self._ignore_maps.pop(0)
        return None


def _modified_event(src: str) -> FileModifiedEvent:
    return FileModifiedEvent(src)


def _opened_event(src: str) -> FileOpenedEvent:
    return FileOpenedEvent(src)


def _moved_event(src: str, dest: str) -> FileMovedEvent:
    return FileMovedEvent(src, dest)


def _mk_handler(
    modules: object,
    cooldown: int = 20,
    process_opened_events: bool = True,
) -> FileHandler:
    return FileHandler(
        modules=cast(ModuleManager, modules),
        open_cooldown_seconds=cooldown,
        process_opened_events=process_opened_events,
    )


def test_process_file_marks_and_consumes_ignore_map(tmp_path: Path) -> None:
    file_path = tmp_path / "a.md"
    file_path.write_text("x\n", encoding="utf-8")

    modules = _FakeModules(ignore_maps=[{str(file_path): 1}])
    handler = _mk_handler(modules)
    ev = _modified_event(str(file_path))

    handler.on_modified(ev)
    handler.on_modified(ev)  # ignored once
    handler.on_modified(ev)  # processed again

    assert modules.calls == 2


@pytest.mark.parametrize(
    "relative_path",
    [
        ".hidden",
        ".git/config",
    ],
)
def test_process_file_skips_hidden_and_git_paths(
    tmp_path: Path, relative_path: str
) -> None:
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x\n", encoding="utf-8")

    modules = _FakeModules()
    handler = _mk_handler(modules)

    handler.on_modified(_modified_event(str(path)))

    assert modules.calls == 0


def test_opened_event_respects_cooldown(tmp_path: Path, monkeypatch) -> None:
    file_path = tmp_path / "b.md"
    file_path.write_text("x\n", encoding="utf-8")

    times = iter([0.0, 1.0, 11.0])
    monkeypatch.setattr(
        "demon_lucy.file_handler.time.monotonic",
        lambda: next(times),
    )

    modules = _FakeModules()
    handler = _mk_handler(modules, cooldown=10)
    ev = _opened_event(str(file_path))

    handler.on_opened(ev)
    handler.on_opened(ev)
    handler.on_opened(ev)

    assert modules.calls == 2


def test_opened_event_can_be_disabled(tmp_path: Path, monkeypatch) -> None:
    file_path = tmp_path / "b.md"
    file_path.write_text("x\n", encoding="utf-8")

    monkeypatch.setattr(
        "demon_lucy.file_handler.time.monotonic",
        lambda: 0.0,
    )

    modules = _FakeModules()
    handler = _mk_handler(modules, process_opened_events=False)

    handler.on_opened(_opened_event(str(file_path)))

    assert modules.calls == 0
    assert handler._last_open_ts == {}
    assert handler._last_open_dir_ts == {}


def test_opened_event_respects_cooldown_for_same_directory(
    tmp_path: Path, monkeypatch
) -> None:
    file_a = tmp_path / "a.md"
    file_b = tmp_path / "b.md"
    file_a.write_text("x\n", encoding="utf-8")
    file_b.write_text("x\n", encoding="utf-8")

    times = iter([0.0, 1.0, 11.0])
    monkeypatch.setattr(
        "demon_lucy.file_handler.time.monotonic",
        lambda: next(times),
    )

    modules = _FakeModules()
    handler = _mk_handler(modules, cooldown=10)

    handler.on_opened(_opened_event(str(file_a)))
    handler.on_opened(_opened_event(str(file_b)))
    handler.on_opened(_opened_event(str(file_b)))

    assert modules.calls == 2


@pytest.mark.parametrize(
    "relative_path",
    [
        ".hidden",
        ".git/config",
    ],
)
def test_opened_event_skips_hidden_and_git_paths(
    tmp_path: Path, relative_path: str, monkeypatch
) -> None:
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x\n", encoding="utf-8")

    monkeypatch.setattr(
        "demon_lucy.file_handler.time.monotonic",
        lambda: 0.0,
    )

    modules = _FakeModules()
    handler = _mk_handler(modules, cooldown=10)
    handler.on_opened(_opened_event(str(path)))

    assert modules.calls == 0
    assert handler._last_open_ts == {}
    assert handler._last_open_dir_ts == {}


def test_moved_event_uses_destination_path(tmp_path: Path) -> None:
    src = tmp_path / "old.md"
    dst = tmp_path / "new.md"
    src.write_text("x\n", encoding="utf-8")

    modules = _FakeModules()
    handler = _mk_handler(modules)

    handler.on_moved(_moved_event(str(src), str(dst)))

    assert modules.calls == 1
    assert modules.paths[0] == str(dst.resolve())


def test_move_ignore_dirs_are_derived_from_module_args(tmp_path: Path) -> None:
    extra_move_ignore_dir = tmp_path / "panel-status"
    modules = _FakeModules(
        values={
            "sys_watch_paths": [str(tmp_path)],
            "sys_ignore_move_paths": [".status", str(extra_move_ignore_dir)],
        },
    )
    handler = _mk_handler(modules)

    assert handler._move_ignore_dirs == [
        str((tmp_path / ".status").resolve()),
        str(extra_move_ignore_dir.resolve()),
    ]


def test_moved_event_inside_move_ignore_dir_is_skipped(tmp_path: Path) -> None:
    status_dir = tmp_path / ".status"
    status_dir.mkdir()
    src = status_dir / "08:09"
    dst = status_dir / "08:10"
    src.write_text("x\n", encoding="utf-8")

    modules = _FakeModules(values={"sys_watch_paths": [str(tmp_path)]})
    handler = _mk_handler(modules)

    handler.on_moved(_moved_event(str(src), str(dst)))

    assert modules.calls == 0


def test_move_ignore_dir_consumes_pending_ignore_map(tmp_path: Path) -> None:
    status_dir = tmp_path / ".status"
    status_dir.mkdir()
    src = status_dir / "08:09"
    dst = status_dir / "08:10"
    src.write_text("x\n", encoding="utf-8")

    modules = _FakeModules(
        ignore_maps=[{str(src): 1, str(dst): 1}, None],
        values={"sys_watch_paths": [str(tmp_path)]},
    )
    handler = _mk_handler(modules)

    handler.on_modified(_modified_event(str(src)))
    handler.on_moved(_moved_event(str(src), str(dst)))
    handler.on_modified(_modified_event(str(dst)))

    assert modules.calls == 2
    assert modules.paths == [str(src.resolve()), str(dst.resolve())]


def test_modified_event_inside_status_dir_is_processed(tmp_path: Path) -> None:
    status_dir = tmp_path / ".status"
    status_dir.mkdir()
    path = status_dir / "08:09"
    path.write_text("x\n", encoding="utf-8")

    modules = _FakeModules(values={"sys_watch_paths": [str(tmp_path)]})
    handler = _mk_handler(modules)

    handler.on_modified(_modified_event(str(path)))

    assert modules.calls == 1
    assert modules.paths[0] == str(path.resolve())


def test_moved_event_into_status_dir_from_outside_is_processed(tmp_path: Path) -> None:
    status_dir = tmp_path / ".status"
    status_dir.mkdir()
    src = tmp_path / "note.md"
    dst = status_dir / "note.md"
    src.write_text("x\n", encoding="utf-8")

    modules = _FakeModules(values={"sys_watch_paths": [str(tmp_path)]})
    handler = _mk_handler(modules)

    handler.on_moved(_moved_event(str(src), str(dst)))

    assert modules.calls == 1
    assert modules.paths[0] == str(dst.resolve())


def test_modified_event_ignores_exact_number_of_future_events(tmp_path: Path) -> None:
    file_path = tmp_path / "counter.md"
    file_path.write_text("x\n", encoding="utf-8")

    modules = _FakeModules(ignore_maps=[{str(file_path): 2}, None])
    handler = _mk_handler(modules)
    ev = _modified_event(str(file_path))

    handler.on_modified(ev)  # processed, sets ignore=2
    handler.on_modified(ev)  # ignored (remaining=1)
    handler.on_modified(ev)  # ignored (remaining=0)
    handler.on_modified(ev)  # processed again

    assert modules.calls == 2


def test_moved_event_is_ignored_when_src_or_dest_is_marked(tmp_path: Path) -> None:
    src = tmp_path / "old.md"
    dst = tmp_path / "new.md"
    src.write_text("x\n", encoding="utf-8")

    src_modules = _FakeModules(ignore_maps=[{str(src): 1}, None])
    src_handler = _mk_handler(src_modules)
    src_handler.on_modified(_modified_event(str(src)))  # marks src to ignore
    src_handler.on_moved(_moved_event(str(src), str(dst)))
    assert src_modules.calls == 1

    dst_modules = _FakeModules(ignore_maps=[{str(dst): 1}, None])
    dst_handler = _mk_handler(dst_modules)
    dst_handler.on_modified(_modified_event(str(src)))  # marks dst to ignore
    dst_handler.on_moved(_moved_event(str(src), str(dst)))
    assert dst_modules.calls == 1


def test_ignore_path_is_normalized_before_matching_event_path(tmp_path: Path) -> None:
    file_path = tmp_path / "norm.md"
    file_path.write_text("x\n", encoding="utf-8")
    odd_form = str(tmp_path / "." / "sub" / ".." / "norm.md")

    modules = _FakeModules(ignore_maps=[{odd_form: 1}, None])
    handler = _mk_handler(modules)

    handler.on_modified(_modified_event(str(file_path)))
    handler.on_modified(_modified_event(str(file_path.resolve())))

    assert modules.calls == 1
