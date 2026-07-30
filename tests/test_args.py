from __future__ import annotations

import shlex
import sys
from enum import StrEnum
from pathlib import Path

import pytest

from demon_lucy.lib.args.line_edit import delete_args_from_string
from demon_lucy.lib.args.models import (
    ArgSource,
    KnownArg,
    ParsedArgs,
    UnknownArg,
)
from demon_lucy.lib.args.parser import (
    is_valid_flag_token,
    parse_args,
    resolve_unknown_args,
    split_arg_line,
)
from demon_lucy.lib.args.sources import (
    _parse_config_args,
    load_args,
    parse_note_args,
)


class _Mode(StrEnum):
    FIRST = "first"
    SECOND = "second"


def test_parse_args_handles_bool_and_nargs():
    template = [
        KnownArg(name="formatter-todo", value_type=bool, default=False),
        KnownArg(name="formatter-blank", value_type=str, default=[]),
        KnownArg(name="name"),
        KnownArg(name="tags", value_type=str, default=[]),
    ]

    parsed = parse_args(
        args=[
            "--formatter-todo",
            "--formatter-blank",
            "up",
            "down",
            "--name",
            "alice",
            "--tags",
            "x",
            "y",
            "--unknown",
        ],
        template=template,
    )

    assert parsed.require("formatter-todo").value is True
    assert parsed.require("formatter-blank").value == ["up", "down"]
    assert parsed.require("name").value == "alice"
    assert parsed.require("tags").value == ["x", "y"]
    assert parsed.unknown == (UnknownArg(token="--unknown", source=ArgSource.CLI),)


def test_parse_args_allows_empty_list_flag_value():
    template = [
        KnownArg(name="archive-local", value_type=str, default=[]),
        KnownArg(name="name"),
    ]

    parsed = parse_args(
        args=["--archive-local", "--name", "note"],
        template=template,
    )

    assert parsed.require("archive-local").value == []
    assert parsed.require("name").value == "note"
    assert parsed.unknown == ()


def test_parse_args_repeated_list_flag_uses_last_value():
    template = [
        KnownArg(name="alias", value_type=str, default=[]),
    ]

    parsed = parse_args(
        args=[
            "--alias",
            "b=--banner {args}",
            "--alias",
            "todo=--formatter-todo",
        ],
        template=template,
    )

    assert parsed.require("alias").value == ["todo=--formatter-todo"]
    assert parsed.unknown == ()


def test_parse_args_supports_required_field_in_template_item():
    template = [
        KnownArg(name="required-path", required=True),
    ]

    parsed = parse_args(
        args=["--required-path", "/tmp/a.md"],
        template=template,
    )

    assert parsed.require("required-path").value == "/tmp/a.md"
    assert parsed.unknown == ()


def test_parse_args_returns_enum_members_for_fixed_string_domains():
    template = [
        KnownArg(
            name="mode",
            value_type=_Mode,
            default=_Mode.FIRST,
        )
    ]

    defaults = parse_args(args=[], template=template)
    parsed = parse_args(args=["--mode", "SECOND"], template=template)

    assert defaults.require("mode").value is _Mode.FIRST
    assert defaults.require("mode").source is ArgSource.DEFAULT
    assert defaults.unknown == ()
    assert parsed.require("mode").value is _Mode.SECOND
    assert parsed.require("mode").source is ArgSource.CLI
    assert parsed.unknown == ()


def test_resolve_unknown_args_preserves_source_and_line() -> None:
    template = [
        KnownArg(
            name="workspace-init",
            value_type=str,
            default="",
            description="Initialize a workspace.",
            required=False,
        ),
    ]
    unknown = (
        UnknownArg(
            token="--workspace-init",
            source=ArgSource.CONFIG,
            line=4,
        ),
        UnknownArg(
            token="/notes",
            source=ArgSource.CONFIG,
            line=4,
        ),
        UnknownArg(token="--banner", source=ArgSource.CLI),
    )

    unresolved = ParsedArgs(unknown=unknown)
    parsed = resolve_unknown_args(
        args=unresolved.unknown_from(ArgSource.CONFIG),
        template=template,
    )

    argument = parsed.require("workspace-init")
    assert argument.value == "/notes"
    assert argument.source is ArgSource.CONFIG
    assert argument.lines == (4,)
    assert parsed.unknown == ()


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("--banner", True),
        ("--voice-recorder-path=arecord", True),
        ("--formatter_todo", False),
        ("--", False),
        ("---", False),
        ("--1abc", False),
        ("--bad.value", False),
        ("text", False),
    ],
)
def test_is_valid_flag_token(token: str, expected: bool):
    assert is_valid_flag_token(token) is expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            r"--sys-watch-paths C:\Users\name\Notes",
            ["--sys-watch-paths", r"C:\Users\name\Notes"],
        ),
        (
            r'--include "C:\My Notes\file.md"',
            ["--include", r"C:\My Notes\file.md"],
        ),
        (
            r"--include \\server\share\Notes\file.md",
            ["--include", r"\\server\share\Notes\file.md"],
        ),
        (
            r"--graph-regex log.md \b(foo|bar)\b",
            ["--graph-regex", "log.md", r"\b(foo|bar)\b"],
        ),
        (
            r"--value some\ value",
            ["--value", "some\\", "value"],
        ),
    ],
)
def test_split_arg_line_preserves_backslashes(
    line: str,
    expected: list[str],
) -> None:
    assert split_arg_line(line) == expected


@pytest.mark.parametrize(
    "tokens",
    [
        ["--include", r"C:\Users\name\Notes\file.md"],
        ["--include", r"C:\My Notes\file.md"],
        ["--include", r"\\server\share\Notes\file.md"],
        ["--graph-regex", "log.md", r"\b(foo|bar)\b"],
        ["--value", "text with 'single' and \"double\" quotes"],
        ["--value", ""],
    ],
)
def test_split_arg_line_round_trips_joined_tokens(tokens: list[str]) -> None:
    assert split_arg_line(shlex.join(tokens)) == tokens


def test_line_edit_preserves_windows_path() -> None:
    line = "--include C:\\Users\\name\\Notes\\file.md --formatter-todo\n"

    updated = delete_args_from_string(line, ["--formatter-todo"])

    assert split_arg_line(updated) == [
        "--include",
        r"C:\Users\name\Notes\file.md",
    ]


def test_parse_config_args_reads_lines_and_ignores_comments(
    tmp_path: Path,
):
    cfg = tmp_path / "config.txt"
    cfg.write_text(
        "# comment\n--name jane\n\n--count 7\n",
        encoding="utf-8",
    )
    template = [
        KnownArg(name="name"),
        KnownArg(name="count", value_type=int, default=0),
    ]

    parsed = _parse_config_args(str(cfg), template)
    assert parsed.require("name").value == "jane"
    assert parsed.require("name").source is ArgSource.CONFIG
    assert parsed.require("name").lines == (2,)
    assert parsed.require("count").value == 7
    assert parsed.require("count").source is ArgSource.CONFIG
    assert parsed.require("count").lines == (4,)
    assert parsed.unknown == ()


def test_parse_config_args_preserves_unquoted_windows_path(
    tmp_path: Path,
) -> None:
    cfg = tmp_path / "config.txt"
    cfg.write_text(
        "--sys-watch-paths C:\\Users\\name\\Notes\n",
        encoding="utf-8",
    )
    template = [
        KnownArg(name="sys-watch-paths", value_type=str, default=[]),
    ]

    parsed = _parse_config_args(str(cfg), template)

    assert parsed.require("sys-watch-paths").value == [r"C:\Users\name\Notes"]
    assert parsed.unknown == ()


def test_parse_config_args_skips_invalid_quote_line(tmp_path: Path):
    cfg = tmp_path / "config.txt"
    cfg.write_text(
        '--name "john\n--count 7\n',
        encoding="utf-8",
    )
    template = [
        KnownArg(name="name"),
        KnownArg(name="count", value_type=int, default=0),
    ]

    parsed = _parse_config_args(str(cfg), template)
    assert parsed.require("name").value is None
    assert parsed.require("name").source is ArgSource.DEFAULT
    assert parsed.require("count").value == 7
    assert parsed.unknown == ()


def test_parsed_args_merge_overwrites_by_argument_presence():
    base = ParsedArgs(
        known=(
            KnownArg(name="a", value=1, source=ArgSource.CONFIG),
            KnownArg(name="b", value="x", source=ArgSource.CONFIG),
            KnownArg(
                name="c",
                value=["old"],
                source=ArgSource.CONFIG,
            ),
        ),
    )
    overwrite = ParsedArgs(
        known=(
            KnownArg(name="a", value=None, source=ArgSource.CLI),
            KnownArg(name="b", value="", source=ArgSource.CLI),
            KnownArg(name="c", value=["new"], source=ArgSource.CLI),
            KnownArg(name="d", value=5, source=ArgSource.CLI),
        ),
    )

    merged = base.merged_with(overwrite)

    assert {argument.name: argument.value for argument in merged.known} == {
        "a": None,
        "b": "",
        "c": ["new"],
        "d": 5,
    }
    assert all(argument.source is ArgSource.CLI for argument in merged.known)
    assert merged.find("missing") is None
    with pytest.raises(KeyError, match="missing"):
        merged.require("missing")


@pytest.mark.parametrize(
    ("line", "args", "expected"),
    [
        # ('--banner "Hello world" body --formatter-todo --x=1 tail\n', ["--banner", "--formatter-todo", "--x"], "body tail\n"),
        (
            "prefix --formatter-todo one --formatter-todo two\n",
            ["--formatter-todo"],
            "prefix\n",
        ),
        (
            "- alpha item\n",
            ["--archive"],
            "- alpha item\n",
        ),
        (
            "- beta item\n",
            ["--archive-pair"],
            "- beta item\n",
        ),
    ],
)
def test_delete_args_from_string_removes_flag_segments(
    line: str, args: list[str], expected: str
):
    assert delete_args_from_string(line, args) == expected


def test_parse_note_args_skips_non_utf8_files(tmp_path: Path):
    path = tmp_path / "image.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")

    template = [
        KnownArg(name="help", value_type=bool, default=False),
    ]

    parsed = parse_note_args(str(path), template)
    assert parsed.known == ()
    assert parsed.unknown == ()


def test_parse_note_args_preserves_windows_path(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    path.write_text(
        "--include C:\\Users\\name\\Notes\\file.md\n",
        encoding="utf-8",
    )
    template = [
        KnownArg(name="include", value_type=str, default=[]),
    ]

    parsed = parse_note_args(str(path), template)

    assert parsed.require("include").value == [r"C:\Users\name\Notes\file.md"]
    assert parsed.require("include").source is ArgSource.FILE
    assert parsed.require("include").lines == (1,)
    assert parsed.unknown == ()


def test_parse_note_args_combines_repeated_list_values(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    path.write_text(
        "--alias first\n--alias second third\n",
        encoding="utf-8",
    )
    template = [
        KnownArg(name="alias", value_type=str, default=[]),
    ]

    parsed = parse_note_args(str(path), template)

    assert parsed.require("alias").value == ["first", "second", "third"]
    assert parsed.require("alias").lines == (1, 2, 2)


def test_load_args_keeps_config_values_when_cli_uses_defaults(
    tmp_path: Path, monkeypatch
):
    cfg = tmp_path / "daemon.cfg"
    cfg.write_text(
        '--sys-watch-paths "/notes/a" "/notes/b"\n--sys-log-level debug\n',
        encoding="utf-8",
    )

    template = [
        KnownArg(name="sys-config-path", value_type=str, default="config.txt"),
        KnownArg(name="sys-watch-paths", value_type=str, default=[]),
        KnownArg(name="sys-log-level", value_type=str, default="warning"),
    ]

    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--sys-config-path", str(cfg)],
    )

    parsed = load_args(template=template)

    assert parsed.unknown == ()
    assert parsed.require("sys-watch-paths").value == ["/notes/a", "/notes/b"]
    assert parsed.require("sys-watch-paths").source is ArgSource.CONFIG
    assert parsed.require("sys-log-level").value == "debug"
    assert parsed.require("sys-log-level").source is ArgSource.CONFIG


def test_load_args_tracks_unknown_sources(
    tmp_path: Path,
    monkeypatch,
):
    cfg = tmp_path / "daemon.cfg"
    cfg.write_text(
        "--sys-log-level debug\n--workspace-init /from-config\n",
        encoding="utf-8",
    )

    template = [
        KnownArg(
            name="sys-config-path",
            value_type=str,
            default="config.txt",
            description="",
            required=False,
        ),
        KnownArg(
            name="sys-log-level",
            value_type=str,
            default="warning",
            description="",
            required=False,
        ),
        KnownArg(
            name="sys-watch-paths",
            value_type=str,
            default=[],
            description="",
            required=False,
        ),
    ]

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--sys-config-path",
            str(cfg),
            "--sys-watch-paths",
            "/from-cli",
            "--banner",
            "hello",
        ],
    )

    parsed = load_args(template=template)

    assert parsed.require("sys-log-level").value == "debug"
    assert parsed.require("sys-log-level").source is ArgSource.CONFIG
    assert parsed.require("sys-watch-paths").value == ["/from-cli"]
    assert parsed.require("sys-watch-paths").source is ArgSource.CLI
    assert parsed.require("sys-config-path").source is ArgSource.CLI
    assert parsed.unknown == (
        UnknownArg(
            token="--workspace-init",
            source=ArgSource.CONFIG,
            line=2,
        ),
        UnknownArg(
            token="/from-config",
            source=ArgSource.CONFIG,
            line=2,
        ),
        UnknownArg(token="--banner", source=ArgSource.CLI),
        UnknownArg(token="hello", source=ArgSource.CLI),
    )


def test_load_args_returns_empty_known_on_invalid_startup_value(
    monkeypatch,
):
    template = [
        KnownArg(name="sys-config-path", value_type=str, default="config.txt"),
        KnownArg(name="count", value_type=int, default=0),
    ]

    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--count", "oops"],
    )

    parsed = load_args(template=template)
    assert parsed.known == ()
    assert parsed.unknown == (
        UnknownArg(token="--count", source=ArgSource.CLI),
        UnknownArg(token="oops", source=ArgSource.CLI),
    )
