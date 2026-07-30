from __future__ import annotations

from pathlib import Path

import pytest
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.modules.abstract_module import System
from demon_lucy.modules.cmd import Cmd, CmdStream
from tests.args_support import make_context


def test_cmd_stream_default_is_typed_enum() -> None:
    parsed = parse_args(args=[], template=Cmd.template)

    assert parsed.unknown == ()
    assert parsed.require("cmd-stream").value is CmdStream.BOTH


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
    ctx = make_context(
        "/tmp/x.md",
        Cmd.template,
        {"cmd": tokens},
        lines={"cmd": lines},
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

    ctx = make_context(
        str(note),
        Cmd.template,
        {
            "cmd": ["echo", "hello"],
            "cmd-timeout-seconds": 5,
            "cmd-output-max-bytes": 1000,
            "cmd-stream": CmdStream.BOTH,
        },
        lines={"cmd": (1, 1)},
    )
    system = System(
        global_template=[],
        modules=[module],
    )

    changed = module.modified(ctx, system)
    content = note.read_text(encoding="utf-8")

    assert changed == {str(note): 1}
    assert "--- echo ---\n" in content
    assert "OUT\n" in content
    # assert "tail\n" in content
