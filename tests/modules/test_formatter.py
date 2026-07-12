from __future__ import annotations

from pathlib import Path

import pytest
from watchdog.events import FileCreatedEvent, FileModifiedEvent, FileMovedEvent

from demon_lucy.modules.abstract_module import Context, System
from demon_lucy.modules.formatter import Formatter
from demon_lucy.lib.dynamic_blocks.parser import (
    format_dynamic_block,
    format_fenced_body,
)


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


def test_apply_todo_does_not_change_dynamic_block_lists(tmp_path: Path):
    block = format_dynamic_block(
        arg="graph",
        params={"source": "past.md", "pattern": "sleep", "period": "week"},
        body=format_fenced_body("- generated row", info="text"),
    )
    note = tmp_path / "note.md"
    note.write_text(block + "- task\n", encoding="utf-8")

    changed = Formatter()._apply(
        path=str(note),
        config={
            "formatter_todo": True,
            "formatter_blank": [],
            "formatter_date": False,
        },
        arg_lines={},
    )

    assert changed == {str(note.resolve()): 1}
    text = note.read_text(encoding="utf-8")
    assert "- source: past.md\n" in text
    assert "- generated row\n" in text
    assert text.endswith("- [ ] task\n")


def test_apply_date_does_not_change_dynamic_block_body(tmp_path: Path):
    block = format_dynamic_block(
        arg="example",
        params={"value": "one"},
        body=format_fenced_body("--- 10", info="text"),
    )
    note = tmp_path / "note.md"
    note.write_text("--- 9.01.2030\n" + block + "--- 10\n", encoding="utf-8")

    changed = Formatter()._apply(
        path=str(note),
        config={
            "formatter_todo": False,
            "formatter_blank": [],
            "formatter_date": True,
        },
        arg_lines={},
    )

    assert changed == {str(note.resolve()): 1}
    text = note.read_text(encoding="utf-8")
    assert "```text\n--- 10\n```" in text
    assert text.endswith("--- 10.01.2030\n")


@pytest.mark.parametrize(
    ("initial_text", "expected_text"),
    [
        (
            "--- 9.01.2030\ntext\n--- 10\n",
            "--- 9.01.2030\ntext\n--- 10.01.2030\n",
        ),
        (
            "--- 31.12.2030 note\n--- 1\n",
            "--- 31.12.2030 note\n--- 1.01.2031\n",
        ),
        (
            "--- 28.02.2028\n--- 29\n--- 1\n",
            "--- 28.02.2028\n--- 29.02.2028\n--- 1.03.2028\n",
        ),
        (
            "--- 28.02.2030\n--- 1\n",
            "--- 28.02.2030\n--- 1.03.2030\n",
        ),
    ],
)
def test_apply_completes_only_next_archive_dates(
    tmp_path: Path,
    initial_text: str,
    expected_text: str,
):
    note = tmp_path / "archive.md"
    note.write_text(initial_text, encoding="utf-8")

    changed = Formatter()._apply(
        path=str(note),
        config={
            "formatter_todo": False,
            "formatter_blank": [],
            "formatter_date": True,
        },
        arg_lines={},
    )

    assert changed == {str(note.resolve()): 1}
    assert note.read_text(encoding="utf-8") == expected_text


@pytest.mark.parametrize(
    "initial_text",
    [
        "--- 9.01.2030\n--- 11\n",
        "--- 10.01.2030\n--- 9\n",
        "--- 31.02.2030\n--- 1\n",
        "--- 10\n",
        "--- 10.01.2030\n--- 9.01.2030\n--- 10\n",
        "--- 9.01.2030\n--- 10\n--- 12\n",
    ],
)
def test_apply_leaves_ambiguous_date_sequences_unchanged(
    tmp_path: Path,
    initial_text: str,
):
    note = tmp_path / "archive.md"
    note.write_text(initial_text, encoding="utf-8")

    changed = Formatter()._apply(
        path=str(note),
        config={
            "formatter_todo": False,
            "formatter_blank": [],
            "formatter_date": True,
        },
        arg_lines={},
    )

    assert changed is None
    assert note.read_text(encoding="utf-8") == initial_text


def test_apply_never_rewrites_full_archive_dates(tmp_path: Path):
    initial_text = "--- 1.1.2030 comment\n--- 02.01.2030\n"
    note = tmp_path / "archive.md"
    note.write_text(initial_text, encoding="utf-8")

    changed = Formatter()._apply(
        path=str(note),
        config={
            "formatter_todo": False,
            "formatter_blank": [],
            "formatter_date": True,
        },
        arg_lines={},
    )

    assert changed is None
    assert note.read_text(encoding="utf-8") == initial_text


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
    note.write_text("--archive-pair\nalpha\n", encoding="utf-8")

    module = Formatter()
    changed = module._apply(
        path=str(note),
        config={
            "formatter_todo": False,
            "formatter_blank": ["up"],
        },
        arg_lines={},
        global_template=module.template + [("--archive-pair", str, [], "", False)],
    )

    assert changed == {str(note.resolve()): 1}

    lines = note.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "--archive-pair"
    assert lines[1:31] == [""] * 30
    assert lines[31] == "alpha"


def test_apply_removes_formatter_flags_and_preserves_other_flags(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text(
        "--archive-pair --formatter-blank up 2 --formatter-todo\n- task\n",
        encoding="utf-8",
    )

    module = Formatter()
    changed = module._apply(
        path=str(note),
        config={
            "formatter_todo": True,
            "formatter_blank": ["up", "2"],
        },
        arg_lines={
            "formatter_blank": [1, 1],
            "formatter_todo": [1],
        },
        global_template=module.template + [("--archive-pair", str, [], "", False)],
    )

    assert changed == {str(note.resolve()): 1}
    assert note.read_text(encoding="utf-8") == "--archive-pair\n\n\n- [ ] task\n"


def test_apply_removes_formatter_only_command_line(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("--formatter-todo\n- task\n", encoding="utf-8")

    module = Formatter()
    changed = module._apply(
        path=str(note),
        config={
            "formatter_todo": True,
            "formatter_blank": [],
        },
        arg_lines={"formatter_todo": [1]},
        global_template=module.template,
    )

    assert changed == {str(note.resolve()): 1}
    assert note.read_text(encoding="utf-8") == "- [ ] task\n"


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
