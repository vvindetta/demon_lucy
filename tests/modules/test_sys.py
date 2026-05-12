from __future__ import annotations

from pathlib import Path

import pytest
from watchdog.events import FileModifiedEvent

from lucy_notes_manager.modules.abstract_module import Context, System
from lucy_notes_manager.modules.sys import Sys


def _base_config() -> dict[str, object]:
    return {
        "mods": False,
        "ping": False,
        "help": False,
        "config": False,
        "sys_event": False,
        "man": [],
    }


def test_man_lines_list_and_specific_name():
    module = Sys()
    system = System(
        event=FileModifiedEvent("/tmp/x"),
        global_template=[
            ("--mods", bool, False, "mods help"),
            ("--formatter-todo", bool, False, "formatter todo help"),
        ],
        modules=[],
    )

    list_lines = module._man_lines(system, ["list"])
    one_lines = module._man_lines(system, ["formatter_todo"])

    assert any("--mods" in line for line in list_lines)
    assert any("--formatter-todo:" in line for line in one_lines)


@pytest.mark.parametrize(
    ("first_line", "config_patch", "arg_lines", "global_template", "expected_lines"),
    [
        (
            "--mods --help\nbody\n",
            {"mods": True, "help": True},
            {"mods": [1], "help": [1]},
            [("--mods", bool, False, ""), ("--help", bool, False, "")],
            ["--- mods+help ---\n", "* --mods: print loaded modules and their priorities\n"],
        ),
        (
            "--ping\n",
            {"ping": True},
            {"ping": [1]},
            [("--ping", bool, False, "Health-check command: prints pong.")],
            ["--- ping ---\n", "* pong\n"],
        ),
    ],
)
def test_apply_inserts_block_for_first_line_flags(
    tmp_path: Path,
    first_line: str,
    config_patch: dict[str, object],
    arg_lines: dict[str, list[int]],
    global_template: list[tuple[str, type, object, str]],
    expected_lines: list[str],
):
    note = tmp_path / "note.md"
    note.write_text(first_line, encoding="utf-8")

    module = Sys()
    config = _base_config()
    config.update(config_patch)
    ctx = Context(
        path=str(note),
        config=config,
        arg_lines=arg_lines,
    )
    system = System(
        event=FileModifiedEvent(str(note)),
        global_template=global_template,
        modules=[module],
    )

    changed = module.modified(ctx, system)
    content = note.read_text(encoding="utf-8")

    assert changed == {str(note): 1}
    for line in expected_lines:
        assert line in content


def test_apply_non_first_line_replacement_with_man(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("head\n--man list\n", encoding="utf-8")

    module = Sys()
    config = _base_config()
    config["man"] = ["list"]
    ctx = Context(
        path=str(note),
        config=config,
        arg_lines={"man": [2]},
    )
    system = System(
        event=FileModifiedEvent(str(note)),
        global_template=[("--man", str, None, "manual")],
        modules=[],
    )

    changed = module._apply(ctx=ctx, system=system)
    content = note.read_text(encoding="utf-8")

    assert changed == {str(note): 1}
    assert "--- man ---\n" in content
    assert "* --man type=str default=None\n" in content
