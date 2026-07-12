from __future__ import annotations

from pathlib import Path

import pytest
from watchdog.events import FileModifiedEvent

from demon_lucy.lib.args.parser import parse_args
from demon_lucy.modules.abstract_module import Context, System
from demon_lucy.modules.cmd import Cmd, CmdStream


def test_cmd_stream_default_is_typed_enum() -> None:
    config, unknown = parse_args(args=[], template=Cmd.template)

    assert unknown == []
    assert config["cmd_stream"] is CmdStream.BOTH


@pytest.mark.parametrize(
    ("tokens", "lines", "expected"),
    [
        (
            ["echo", "hello", "ls", "-la"],
            [1, 1, 2, 2],
            [(1, ["echo", "hello"]), (2, ["ls", "-la"])],
        ),
        (["pwd", "whoami", "date"], [3, 4, 4], [(3, ["pwd"]), (4, ["whoami", "date"])]),
    ],
)
def test_collect_runs_groups_tokens_by_line(
    tokens: list[str], lines: list[int], expected: list[tuple[int, list[str]]]
):
    module = Cmd()
    ctx = Context(
        path="/tmp/x.md",
        config={"cmd": tokens},
        arg_lines={"cmd": lines},
    )

    runs = module._collect_runs(ctx)
    assert [(run.lineno_1based, run.cmd_tokens) for run in runs] == expected


@pytest.mark.parametrize(
    ("raw_stream", "expected"),
    [
        (CmdStream.BOTH, (True, True)),
        (CmdStream.STDOUT, (True, False)),
        (CmdStream.STDERR, (False, True)),
        (CmdStream.NONE, (False, False)),
    ],
)
def test_stream_flags(raw_stream: CmdStream, expected: tuple[bool, bool]):
    assert Cmd._stream_flags(raw_stream) == expected


def test_apply_replaces_command_line_with_output_block(tmp_path: Path, monkeypatch):
    note = tmp_path / "note.md"
    note.write_text("--cmd echo hello tail\n", encoding="utf-8")

    module = Cmd()
    monkeypatch.setattr(module, "_run_cmd", lambda **_kwargs: (0, "OUT\n", ""))

    ctx = Context(
        path=str(note),
        config={
            "cmd": ["echo", "hello"],
            "cmd_timeout_seconds": 5,
            "cmd_output_max_bytes": 1000,
            "cmd_stream": CmdStream.BOTH,
        },
        arg_lines={"cmd": [1, 1]},
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
