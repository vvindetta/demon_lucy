from __future__ import annotations

from pathlib import Path

import pytest
from watchdog.events import FileCreatedEvent, FileModifiedEvent, FileMovedEvent

from lucy_notes_manager.modules.abstract_module import Context, System
from lucy_notes_manager.modules.formatter import Formatter


def _count_leading_blank_lines(lines: list[str]) -> int:
    count = 0
    for line in lines:
        if line.strip():
            break
        count += 1
    return count


def _count_trailing_blank_lines(lines: list[str]) -> int:
    count = 0
    for line in reversed(lines):
        if line.strip():
            break
        count += 1
    return count


@pytest.mark.parametrize(
    ("enabled", "expected_changed", "expected_text"),
    [
        (True, True, "- [ ] task\n- [ ] already\ntext\n"),
        (False, False, "- task\n- [ ] already\ntext\n"),
    ],
)
def test_apply_todo_flag_controls_checkbox_formatting(
    tmp_path: Path,
    enabled: bool,
    expected_changed: bool,
    expected_text: str,
):
    note = tmp_path / "todo.md"
    note.write_text("- task\n- [ ] already\ntext\n", encoding="utf-8")

    module = Formatter()
    changed = module._apply(
        path=str(note),
        config={
            "formatter_todo": enabled,
            "formatter_blank": [],
        },
        arg_lines={},
    )

    assert (changed is not None) is expected_changed
    assert note.read_text(encoding="utf-8") == expected_text


@pytest.mark.parametrize(
    ("config", "initial_text", "expected_leading", "expected_trailing"),
    [
        (
            {
                "formatter_todo": False,
                "formatter_blank": ["down"],
            },
            "title\nbody\n\n",
            0,
            30,
        ),
        (
            {
                "formatter_todo": False,
                "formatter_blank": ["up"],
            },
            "\n  \ntitle\nbody\n",
            30,
            0,
        ),
        (
            {
                "formatter_todo": False,
                "formatter_blank": ["up", "down"],
            },
            "\n\ntitle\nbody\n\n",
            30,
            30,
        ),
        (
            {
                "formatter_todo": False,
                "formatter_blank": ["up", "20"],
            },
            "\n\ntitle\nbody\n\n",
            20,
            1,
        ),
        (
            {
                "formatter_todo": False,
                "formatter_blank": ["down", "7"],
            },
            "title\nbody\n\n",
            0,
            7,
        ),
        (
            {
                "formatter_todo": False,
                "formatter_blank": ["both", "12"],
            },
            "\n\ntitle\nbody\n\n",
            12,
            12,
        ),
    ],
)
def test_apply_adds_blank_lines_by_flags(
    tmp_path: Path,
    config: dict[str, object],
    initial_text: str,
    expected_leading: int,
    expected_trailing: int,
):
    note = tmp_path / "note.md"
    note.write_text(initial_text, encoding="utf-8")

    module = Formatter()
    changed = module._apply(path=str(note), config=config, arg_lines={})

    assert changed == {str(note.resolve()): 1}

    content_lines = note.read_text(encoding="utf-8").splitlines()
    assert _count_leading_blank_lines(content_lines) == expected_leading
    assert _count_trailing_blank_lines(content_lines) == expected_trailing


def test_apply_is_idempotent_on_second_run(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("title\nbody\n", encoding="utf-8")

    module = Formatter()
    config = {
        "formatter_todo": False,
        "formatter_blank": ["up", "down"],
    }

    first = module._apply(path=str(note), config=config, arg_lines={})
    second = module._apply(path=str(note), config=config, arg_lines={})

    assert first == {str(note.resolve()): 1}
    assert second is None


def test_apply_returns_none_for_blank_only_file(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("\n\n\n", encoding="utf-8")

    module = Formatter()
    changed = module._apply(
        path=str(note),
        config={
            "formatter_todo": False,
            "formatter_blank": ["up", "down"],
        },
        arg_lines={},
    )

    assert changed is None
    assert note.read_text(encoding="utf-8") == "\n\n\n"


def test_blank_up_keeps_first_line_with_flags_in_place(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("--formatter-todo\nalpha\n", encoding="utf-8")

    module = Formatter()
    changed = module._apply(
        path=str(note),
        config={
            "formatter_todo": False,
            "formatter_blank": ["up"],
        },
        arg_lines={},
        global_template=module.template,
    )

    assert changed == {str(note.resolve()): 1}

    lines = note.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "--formatter-todo"
    assert lines[1:31] == [""] * 30
    assert lines[31] == "alpha"


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
    note = tmp_path / "note.md"
    note.write_text("title\n", encoding="utf-8")
    module = Formatter()

    called = []
    monkeypatch.setattr(
        module,
        "_apply",
        lambda **kwargs: called.append(kwargs["path"]) or {kwargs["path"]: 1},
    )

    ctx = Context(
        path=str(note),
        config={
            "formatter_todo": False,
            "formatter_blank": ["down"],
        },
        arg_lines={},
    )
    system = System(
        event=event_factory(str(note)),
        global_template=[],
        modules=[module],
    )

    result = getattr(module, method_name)(ctx, system)

    assert called == [str(note)]
    assert result == {str(note): 1}
