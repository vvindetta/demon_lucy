from __future__ import annotations

from pathlib import Path

import pytest
from watchdog.events import FileCreatedEvent, FileModifiedEvent, FileMovedEvent

from demon_lucy.lib.args.models import KnownArg
from demon_lucy.modules.abstract_module import System
from demon_lucy.modules.formatter import Formatter
from demon_lucy.lib.dynamic_blocks.parser import (
    format_dynamic_block,
    format_fenced_body,
)
from tests.args_support import make_args, make_context, result_changes


def _run_formatter(
    module: Formatter,
    *,
    path: str,
    values: dict[str, object],
    lines: dict[str, list[int]] | None = None,
    global_template: list[KnownArg] | None = None,
):
    return module._apply(
        path=path,
        args=make_args(
            module.template,
            values,
            lines=lines,
        ),
        global_template=global_template or module.template,
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
    changed = _run_formatter(
        module,
        path=str(note),
        values={
            "formatter-todo": enabled,
            "formatter-blank": [],
        },
        lines={},
    )

    assert (changed is not None) is expected_changed
    assert note.read_text(encoding="utf-8") == expected_text


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_apply_preserves_line_endings(tmp_path: Path, newline: bytes):
    note = tmp_path / "todo.md"
    note.write_bytes(newline.join((b"- task", b"text", b"")))

    changed = _run_formatter(
        Formatter(),
        path=str(note),
        values={
            "formatter-todo": True,
            "formatter-blank": [],
        },
        lines={},
    )

    assert changed == {str(note.resolve()): 1}
    assert note.read_bytes() == newline.join((b"- [ ] task", b"text", b""))


def test_apply_todo_does_not_change_dynamic_block_lists(tmp_path: Path):
    block = format_dynamic_block(
        arg="graph",
        params={"source": "past.md", "pattern": "sleep", "period": "week"},
        body=format_fenced_body("- generated row", info="text"),
    )
    note = tmp_path / "note.md"
    note.write_text(block + "- task\n", encoding="utf-8")

    changed = _run_formatter(
        Formatter(),
        path=str(note),
        values={
            "formatter-todo": True,
            "formatter-blank": [],
            "formatter-date": False,
        },
        lines={},
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

    changed = _run_formatter(
        Formatter(),
        path=str(note),
        values={
            "formatter-todo": False,
            "formatter-blank": [],
            "formatter-date": True,
        },
        lines={},
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
            "--- 9.01.2030\n--- 11\n",
            "--- 9.01.2030\n--- 11.01.2030\n",
        ),
        (
            "--- 9.01.2030\n--- 10\n--- 12\n",
            "--- 9.01.2030\n--- 10.01.2030\n--- 12.01.2030\n",
        ),
        (
            "--- 31.12.2030 note\n--- 1\n",
            "--- 31.12.2030 note\n--- 1.01.2031\n",
        ),
        (
            "--- 31.12.2030\n--- 3\n",
            "--- 31.12.2030\n--- 3.01.2031\n",
        ),
        (
            "--- 28.02.2028\n--- 29\n--- 1\n",
            "--- 28.02.2028\n--- 29.02.2028\n--- 1.03.2028\n",
        ),
        (
            "--- 28.02.2028\n--- 1\n",
            "--- 28.02.2028\n--- 1.03.2028\n",
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

    changed = _run_formatter(
        Formatter(),
        path=str(note),
        values={
            "formatter-todo": False,
            "formatter-blank": [],
            "formatter-date": True,
        },
        lines={},
    )

    assert changed == {str(note.resolve()): 1}
    assert note.read_text(encoding="utf-8") == expected_text


@pytest.mark.parametrize(
    ("prefix", "argument_lines"),
    [
        ("--formatter-date\n", {"formatter-date": [1]}),
        ("", {}),
    ],
)
def test_apply_date_remains_enabled_for_future_updates(
    tmp_path: Path,
    prefix: str,
    argument_lines: dict,
) -> None:
    note = tmp_path / "archive.md"
    note.write_text(prefix + "--- 9.01.2030\n--- 10\n", encoding="utf-8")
    values = {
        "formatter-todo": False,
        "formatter-blank": [],
        "formatter-date": True,
    }

    first_changed = _run_formatter(
        Formatter(),
        path=str(note),
        values=values,
        lines=argument_lines,
    )
    note.write_text(
        note.read_text(encoding="utf-8") + "--- 11\n",
        encoding="utf-8",
    )
    second_changed = _run_formatter(
        Formatter(),
        path=str(note),
        values=values,
        lines=argument_lines,
    )

    assert first_changed == {str(note.resolve()): 1}
    assert second_changed == {str(note.resolve()): 1}
    assert note.read_text(encoding="utf-8") == (
        prefix + "--- 9.01.2030\n--- 10.01.2030\n--- 11.01.2030\n"
    )


@pytest.mark.parametrize(
    "initial_text",
    [
        "--- 31.02.2030\n--- 1\n",
        "--- 10\n",
        "--- 31.01.2030\n--- 30\n",
    ],
)
def test_apply_leaves_ambiguous_date_sequences_unchanged(
    tmp_path: Path,
    initial_text: str,
):
    note = tmp_path / "archive.md"
    note.write_text(initial_text, encoding="utf-8")

    changed = _run_formatter(
        Formatter(),
        path=str(note),
        values={
            "formatter-todo": False,
            "formatter-blank": [],
            "formatter-date": True,
        },
        lines={},
    )

    assert changed is None
    assert note.read_text(encoding="utf-8") == initial_text


@pytest.mark.parametrize(
    ("initial_text", "expected_text"),
    [
        (
            "--- 10\n--- 11\n--- 9.01.2030\n--- 12\n",
            "--- 10\n--- 11\n--- 9.01.2030\n--- 12.01.2030\n",
        ),
        (
            "--- 9.01.2030\n--- 11\n--- 31.02.2030\n--- 1\n--- 5.03.2030\n--- 8\n",
            "--- 9.01.2030\n--- 11.01.2030\n--- 31.02.2030\n--- 1\n"
            "--- 5.03.2030\n--- 8.03.2030\n",
        ),
        (
            "--- 31.01.2030\n--- 30\n--- 1\n--- 5.03.2030\n--- 6\n",
            "--- 31.01.2030\n--- 30\n--- 1\n--- 5.03.2030\n--- 6.03.2030\n",
        ),
        (
            "--- 10.01.2030\n--- 9.01.2030\n--- 10\n",
            "--- 10.01.2030\n--- 9.01.2030\n--- 10.01.2030\n",
        ),
    ],
)
def test_apply_date_resumes_after_next_full_date(
    tmp_path: Path,
    initial_text: str,
    expected_text: str,
) -> None:
    note = tmp_path / "archive.md"
    note.write_text(initial_text, encoding="utf-8")

    changed = _run_formatter(
        Formatter(),
        path=str(note),
        values={
            "formatter-todo": False,
            "formatter-blank": [],
            "formatter-date": True,
        },
        lines={},
    )

    assert changed == {str(note.resolve()): 1}
    assert note.read_text(encoding="utf-8") == expected_text


def test_apply_never_rewrites_full_archive_dates(tmp_path: Path):
    initial_text = "--- 1.1.2030 comment\n--- 02.01.2030\n"
    note = tmp_path / "archive.md"
    note.write_text(initial_text, encoding="utf-8")

    changed = _run_formatter(
        Formatter(),
        path=str(note),
        values={
            "formatter-todo": False,
            "formatter-blank": [],
            "formatter-date": True,
        },
        lines={},
    )

    assert changed is None
    assert note.read_text(encoding="utf-8") == initial_text


@pytest.mark.parametrize(
    ("values", "initial_text", "expected_leading", "expected_trailing"),
    [
        (
            {
                "formatter-todo": False,
                "formatter-blank": ["down"],
            },
            "title\nbody\n\n",
            0,
            30,
        ),
        (
            {
                "formatter-todo": False,
                "formatter-blank": ["up"],
            },
            "\n  \ntitle\nbody\n",
            30,
            0,
        ),
        (
            {
                "formatter-todo": False,
                "formatter-blank": ["up", "down"],
            },
            "\n\ntitle\nbody\n\n",
            30,
            30,
        ),
        (
            {
                "formatter-todo": False,
                "formatter-blank": ["up", "20"],
            },
            "\n\ntitle\nbody\n\n",
            20,
            1,
        ),
        (
            {
                "formatter-todo": False,
                "formatter-blank": ["down", "7"],
            },
            "title\nbody\n\n",
            0,
            7,
        ),
        (
            {
                "formatter-todo": False,
                "formatter-blank": ["both", "12"],
            },
            "\n\ntitle\nbody\n\n",
            12,
            12,
        ),
    ],
)
def test_apply_adds_blank_lines_by_flags(
    tmp_path: Path,
    values: dict[str, object],
    initial_text: str,
    expected_leading: int,
    expected_trailing: int,
):
    note = tmp_path / "note.md"
    note.write_text(initial_text, encoding="utf-8")

    module = Formatter()
    changed = _run_formatter(module, path=str(note), values=values, lines={})

    assert changed == {str(note.resolve()): 1}

    content_lines = note.read_text(encoding="utf-8").splitlines()
    assert _count_leading_blank_lines(content_lines) == expected_leading
    assert _count_trailing_blank_lines(content_lines) == expected_trailing


def test_apply_is_idempotent_on_second_run(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("title\nbody\n", encoding="utf-8")

    module = Formatter()
    values = {
        "formatter-todo": False,
        "formatter-blank": ["up", "down"],
    }

    first = _run_formatter(module, path=str(note), values=values, lines={})
    second = _run_formatter(module, path=str(note), values=values, lines={})

    assert first == {str(note.resolve()): 1}
    assert second is None


def test_apply_returns_none_for_blank_only_file(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("\n\n\n", encoding="utf-8")

    module = Formatter()
    changed = _run_formatter(
        module,
        path=str(note),
        values={
            "formatter-todo": False,
            "formatter-blank": ["up", "down"],
        },
        lines={},
    )

    assert changed is None
    assert note.read_text(encoding="utf-8") == "\n\n\n"


def test_blank_up_keeps_first_line_with_flags_in_place(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("--archive-pair\nalpha\n", encoding="utf-8")

    module = Formatter()
    changed = _run_formatter(
        module,
        path=str(note),
        values={
            "formatter-todo": False,
            "formatter-blank": ["up"],
        },
        lines={},
        global_template=module.template
        + [KnownArg(name="archive-pair", value_type=str, default=[])],
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
    changed = _run_formatter(
        module,
        path=str(note),
        values={
            "formatter-todo": True,
            "formatter-blank": ["up", "2"],
        },
        lines={
            "formatter-blank": [1, 1],
            "formatter-todo": [1],
        },
        global_template=module.template
        + [KnownArg(name="archive-pair", value_type=str, default=[])],
    )

    assert changed == {str(note.resolve()): 1}
    assert note.read_text(encoding="utf-8") == "--archive-pair\n\n\n- [ ] task\n"


def test_apply_removes_formatter_only_command_line(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("--formatter-todo\n- task\n", encoding="utf-8")

    module = Formatter()
    changed = _run_formatter(
        module,
        path=str(note),
        values={
            "formatter-todo": True,
            "formatter-blank": [],
        },
        lines={"formatter-todo": [1]},
        global_template=module.template,
    )

    assert changed == {str(note.resolve()): 1}
    assert note.read_text(encoding="utf-8") == "- [ ] task\n"


def test_apply_autocompletes_argument_prefixes(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text(
        "--formatter-autocomplete --formatter-t\n"
        "--gra past.md www\n"
        "--graph-r past.md www\n"
        "--archive-pair now.md past.md\n",
        encoding="utf-8",
    )

    module = Formatter()
    changed = _run_formatter(
        module,
        path=str(note),
        values={
            "formatter-todo": False,
            "formatter-blank": [],
            "formatter-autocomplete": True,
        },
        lines={"formatter-autocomplete": [1]},
        global_template=module.template
        + [
            KnownArg(name="archive", value_type=bool, default=False),
            KnownArg(name="archive-pair", value_type=str, default=[]),
            KnownArg(name="graph", value_type=str, default=[]),
            KnownArg(name="graph-regex", value_type=str, default=[]),
        ],
    )

    assert changed == {str(note.resolve()): 1}
    assert note.read_text(encoding="utf-8") == (
        "--formatter-autocomplete --formatter-todo\n"
        "--graph past.md www\n"
        "--graph-regex past.md www\n"
        "--archive-pair now.md past.md\n"
    )


def test_apply_autocompletes_to_common_argument_prefix(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("--formatter-autocomplete --formatter\n", encoding="utf-8")

    module = Formatter()
    changed = _run_formatter(
        module,
        path=str(note),
        values={
            "formatter-todo": False,
            "formatter-blank": [],
            "formatter-autocomplete": True,
        },
        lines={"formatter-autocomplete": [1]},
        global_template=module.template,
    )

    assert changed == {str(note.resolve()): 1}
    assert note.read_text(encoding="utf-8") == (
        "--formatter-autocomplete --formatter-\n"
    )


def test_apply_autocomplete_remains_enabled_for_future_updates(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text(
        "--formatter-autocomplete\n--gra past.md sleep\n",
        encoding="utf-8",
    )
    module = Formatter()
    values = {
        "formatter-todo": False,
        "formatter-blank": [],
        "formatter-autocomplete": True,
    }
    argument_lines = {"formatter-autocomplete": [1]}
    global_template = module.template + [
        KnownArg(name="graph", value_type=str, default=[]),
        KnownArg(name="banner", value_type=str, default=[]),
    ]

    first_changed = _run_formatter(
        module,
        path=str(note),
        values=values,
        lines=argument_lines,
        global_template=global_template,
    )
    note.write_text(
        note.read_text(encoding="utf-8") + "--ban title\n",
        encoding="utf-8",
    )
    second_changed = _run_formatter(
        module,
        path=str(note),
        values=values,
        lines=argument_lines,
        global_template=global_template,
    )

    assert first_changed == {str(note.resolve()): 1}
    assert second_changed == {str(note.resolve()): 1}
    assert note.read_text(encoding="utf-8") == (
        "--formatter-autocomplete\n--graph past.md sleep\n--banner title\n"
    )


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

    ctx = make_context(
        str(note),
        module.template,
        {
            "formatter-todo": False,
            "formatter-blank": ["down"],
        },
        event=event_factory(str(note)),
    )
    system = System(
        global_template=module.template,
        modules=[module],
    )

    result = getattr(module, method_name)(ctx, system)

    assert called == [str(note)]
    assert result_changes(result) == {str(note): 1}
