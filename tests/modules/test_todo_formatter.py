from __future__ import annotations

from pathlib import Path

import pytest
from watchdog.events import FileCreatedEvent, FileModifiedEvent, FileMovedEvent

from lucy_notes_manager.modules.abstract_module import Context, System
from lucy_notes_manager.modules.todo_formatter import TodoFormatter


@pytest.mark.parametrize(
    ("enabled", "expected_changed", "expected_text"),
    [
        (True, True, "- [ ] task\n- [ ] already\ntext\n"),
        (False, False, "- task\n- [ ] already\ntext\n"),
    ],
)
def test_apply_todo_flag_controls_formatting(
    tmp_path: Path,
    enabled: bool,
    expected_changed: bool,
    expected_text: str,
):
    note = tmp_path / "todo.md"
    note.write_text("- task\n- [ ] already\ntext\n", encoding="utf-8")

    module = TodoFormatter()
    changed = module._apply(
        path=str(note),
        config={"todo": enabled},
        arg_lines={},
    )

    assert (changed is not None) is expected_changed
    assert note.read_text(encoding="utf-8") == expected_text


@pytest.mark.parametrize(
    ("method_name", "event_factory"),
    [
        ("created", lambda p: FileCreatedEvent(p)),
        ("modified", lambda p: FileModifiedEvent(p)),
        ("moved", lambda p: FileMovedEvent(p, p)),
    ],
)
def test_event_methods_delegate_to_apply(
    tmp_path: Path,
    monkeypatch,
    method_name: str,
    event_factory,
):
    note = tmp_path / "todo.md"
    note.write_text("- x\n", encoding="utf-8")
    module = TodoFormatter()

    called = []
    monkeypatch.setattr(
        module,
        "_apply",
        lambda **kwargs: called.append(kwargs["path"]) or {kwargs["path"]: 1},
    )

    ctx = Context(path=str(note), config={"todo": True}, arg_lines={})
    system = System(
        event=event_factory(str(note)),
        global_template=[],
        modules=[module],
    )
    result = getattr(module, method_name)(ctx, system)

    assert called == [str(note)]
    assert result == {str(note): 1}
