import pytest

from demon_lucy.lib.args.models import KnownArg
from demon_lucy.lib.args.sources import parse_note_args
from demon_lucy.lib.command_blocks import CommandBlock, find_command_blocks
from demon_lucy.modules.new import New


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("--new first\nbody\n--end\nafter\n", [CommandBlock(0, 2, 3)]),
        ("--new first\n--new nested\nbody", [CommandBlock(0, 3, 3)]),
        (
            "--end\n--new=first\n--end\n--new second\n",
            [CommandBlock(1, 2, 3), CommandBlock(3, 4, 4)],
        ),
        (
            "```\n--new code\n```\n--new actual\n```\n--end\n```\n--end\n",
            [CommandBlock(3, 7, 8)],
        ),
        ("--- example begin ---\n--new generated\n--end\n", []),
        ("    --new code\n\t--new code\n", []),
    ],
)
def test_command_block_boundaries(text, expected):
    assert (
        find_command_blocks(text.splitlines(keepends=True), opening_flag="--new")
        == expected
    )


def test_note_parser_does_not_expose_body_flags_or_unknown_commands(tmp_path):
    note = tmp_path / "note.md"
    note.write_text(
        "--new child\n--rename wrong.md\n--unloaded-module\n--end\n--rename correct.md\n"
    )
    parsed = parse_note_args(
        str(note), New.template + [KnownArg(name="rename", value_type=str)]
    )
    assert parsed.require("new").value == ["child"]
    assert parsed.require("rename").value == "correct.md"
    assert parsed.require("rename").lines == (5,)
    assert not parsed.unknown
