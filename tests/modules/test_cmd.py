from __future__ import annotations

from pathlib import Path

import pytest
from watchdog.events import FileModifiedEvent

from lucy_notes_manager.modules.abstract_module import Context, System
from lucy_notes_manager.modules.cmd import Cmd


@pytest.mark.parametrize(
    ("tokens", "lines", "expected"),
    [
        (["echo", "hello", "ls", "-la"], [1, 1, 2, 2], [(1, ["echo", "hello"]), (2, ["ls", "-la"])]),
        (["pwd", "whoami", "date"], [3, 4, 4], [(3, ["pwd"]), (4, ["whoami", "date"])]),
    ],
)
def test_collect_runs_groups_tokens_by_line(
    tokens: list[str], lines: list[int], expected: list[tuple[int, list[str]]]
):
    module = Cmd()
    ctx = Context(
        path="/tmp/x.md",
        config={"c": tokens},
        arg_lines={"c": lines},
    )

    runs = module._collect_runs(ctx)
    assert [(run.lineno_1based, run.cmd_tokens) for run in runs] == expected


def test_apply_replaces_command_line_with_output_block(tmp_path: Path, monkeypatch):
    note = tmp_path / "note.md"
    note.write_text("--c echo hello tail\n", encoding="utf-8")

    module = Cmd()
    monkeypatch.setattr(module, "_run_cmd", lambda **_kwargs: (0, "OUT\n", ""))

    ctx = Context(
        path=str(note),
        config={
            "c": ["echo", "hello"],
            "cmd_timeout": 5,
            "cmd_max_bytes": 1000,
            "cmd_show_stdout": True,
            "cmd_show_stderr": True,
        },
        arg_lines={"c": [1, 1]},
    )
    system = System(
        event=FileModifiedEvent(str(note)),
        global_template=[],
        modules=[module],
    )

    changed = module.modified(ctx, system)
    content = note.read_text(encoding="utf-8")

    assert changed == {str(note): 1}
    assert "--- echo ---\n" in content
    assert "OUT\n" in content
    # assert "tail\n" in content
