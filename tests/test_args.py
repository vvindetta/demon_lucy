from __future__ import annotations

import sys
from enum import StrEnum
from pathlib import Path

import pytest

from demon_lucy.lib.args.line_edit import delete_args_from_string
from demon_lucy.lib.args.completion import (
    complete_flag_prefixes_in_line,
    complete_flag_token,
)
from demon_lucy.lib.args.parser import (
    ArgTemplate,
    get_args_from_file,
    get_config_args,
    is_valid_flag_token,
    merge_known_args,
    parse_args,
    setup_config_and_cli_args,
)


class _Mode(StrEnum):
    FIRST = "first"
    SECOND = "second"


def test_parse_args_handles_bool_and_nargs():
    template = [
        ArgTemplate(name="--formatter-todo", value_type=bool, default=False),
        ArgTemplate(name="--formatter-blank", value_type=str, default=[]),
        ArgTemplate(name="--name"),
        ArgTemplate(name="--tags", value_type=str, default=[]),
    ]

    known, unknown = parse_args(
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

    assert known["formatter_todo"] is True
    assert known["formatter_blank"] == ["up", "down"]
    assert known["name"] == "alice"
    assert known["tags"] == ["x", "y"]
    assert unknown == ["--unknown"]


def test_parse_args_allows_empty_list_flag_value():
    template = [
        ArgTemplate(name="--archive-local", value_type=str, default=[]),
        ArgTemplate(name="--name"),
    ]

    known, unknown = parse_args(
        args=["--archive-local", "--name", "note"],
        template=template,
    )

    assert known["archive_local"] == []
    assert known["name"] == "note"
    assert unknown == []


def test_parse_args_repeated_list_flag_uses_last_value():
    template = [
        ArgTemplate(name="--alias", value_type=str, default=[]),
    ]

    known, unknown = parse_args(
        args=[
            "--alias",
            "b=--banner {args}",
            "--alias",
            "todo=--formatter-todo",
        ],
        template=template,
    )

    assert known["alias"] == ["todo=--formatter-todo"]
    assert unknown == []


def test_parse_args_supports_required_field_in_template_item():
    template = [
        ArgTemplate(name="--required-path", required=True),
    ]

    known, unknown = parse_args(
        args=["--required-path", "/tmp/a.md"],
        template=template,
    )

    assert known["required_path"] == "/tmp/a.md"
    assert unknown == []


def test_parse_args_returns_enum_members_for_fixed_string_domains():
    template = [
        ArgTemplate(
            name="--mode",
            value_type=_Mode,
            default=_Mode.FIRST,
        )
    ]

    defaults, default_unknown = parse_args(args=[], template=template)
    parsed, unknown = parse_args(args=["--mode", "SECOND"], template=template)

    assert defaults["mode"] is _Mode.FIRST
    assert default_unknown == []
    assert parsed["mode"] is _Mode.SECOND
    assert unknown == []


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("--banner", True),
        ("--voice-recorder-path=arecord", True),
        ("--formatter_todo", True),
        ("--", False),
        ("---", False),
        ("--1abc", False),
        ("--bad.value", False),
        ("text", False),
    ],
)
def test_is_valid_flag_token(token: str, expected: bool):
    assert is_valid_flag_token(token) is expected


def test_get_config_args_reads_lines_and_ignores_comments(tmp_path: Path):
    cfg = tmp_path / "config.txt"
    cfg.write_text(
        "# comment\n--name jane\n\n--count 7\n",
        encoding="utf-8",
    )
    template = [
        ArgTemplate(name="--name"),
        ArgTemplate(name="--count", value_type=int, default=0),
    ]

    known, unknown = get_config_args(str(cfg), template)
    assert known["name"] == "jane"
    assert known["count"] == 7
    assert unknown == []


def test_get_config_args_skips_invalid_quote_line(tmp_path: Path):
    cfg = tmp_path / "config.txt"
    cfg.write_text(
        '--name "john\n--count 7\n',
        encoding="utf-8",
    )
    template = [
        ArgTemplate(name="--name"),
        ArgTemplate(name="--count", value_type=int, default=0),
    ]

    known, unknown = get_config_args(str(cfg), template)
    assert known["name"] is None
    assert known["count"] == 7
    assert unknown == []


def test_merge_known_args_overwrites_only_when_value_is_meaningful():
    base = {"a": 1, "b": "x", "c": ["old"]}
    overwrite = {"a": None, "b": "", "c": ["new"], "d": 5}

    merged = merge_known_args(base, overwrite)

    assert merged == {"a": 1, "b": "x", "c": ["new"], "d": 5}


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


def test_complete_flag_token_uses_only_unique_template_prefixes() -> None:
    flags = (
        "--formatter-todo",
        "--formatter-date",
        "--formatter-complete-args",
        "--graph-regex",
    )

    assert complete_flag_token("--graph-r", flags) == "--graph-regex"
    assert complete_flag_token("--formatter-d", flags) == "--formatter-date"
    assert complete_flag_token("--formatter", flags) == "--formatter"
    assert complete_flag_token("formatter-d", flags) == "formatter-d"


def test_complete_flag_prefixes_in_line_rewrites_command_flags_only() -> None:
    template = [
        ArgTemplate(name="--formatter-todo", value_type=bool, default=False),
        ArgTemplate(name="--formatter-date", value_type=bool, default=False),
        ArgTemplate(name="--graph-regex", value_type=str, default=[]),
    ]

    assert (
        complete_flag_prefixes_in_line("--graph-r past.md www\n", template=template)
        == "--graph-regex past.md www\n"
    )
    assert (
        complete_flag_prefixes_in_line(
            "  --formatter-d --graph-r=pattern\n",
            template=template,
        )
        == "  --formatter-date --graph-regex=pattern\n"
    )
    assert (
        complete_flag_prefixes_in_line("text --graph-r\n", template=template)
        == "text --graph-r\n"
    )
    assert (
        complete_flag_prefixes_in_line('--formatter-d "--graph-r"\n', template=template)
        == '--formatter-date "--graph-r"\n'
    )


def test_get_args_from_file_skips_non_utf8_files(tmp_path: Path):
    path = tmp_path / "image.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")

    template = [
        ArgTemplate(name="--help", value_type=bool, default=False),
    ]

    known, unknown, arg_lines = get_args_from_file(str(path), template)
    assert known == {}
    assert unknown == []
    assert arg_lines == {}


def test_setup_config_and_cli_args_keeps_config_values_when_cli_uses_defaults(
    tmp_path: Path, monkeypatch
):
    cfg = tmp_path / "daemon.cfg"
    cfg.write_text(
        '--sys-watch-paths "/notes/a" "/notes/b"\n--sys-log-level debug\n',
        encoding="utf-8",
    )

    template = [
        ArgTemplate(name="--sys-config-path", value_type=str, default="config.txt"),
        ArgTemplate(name="--sys-watch-paths", value_type=str, default=[]),
        ArgTemplate(name="--sys-log-level", value_type=str, default="warning"),
    ]

    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--sys-config-path", str(cfg)],
    )

    known, unknown = setup_config_and_cli_args(template=template)

    assert unknown == []
    assert known["sys_watch_paths"] == ["/notes/a", "/notes/b"]
    assert known["sys_log_level"] == "debug"


def test_setup_config_and_cli_args_returns_empty_known_on_invalid_startup_value(
    monkeypatch,
):
    template = [
        ArgTemplate(name="--sys-config-path", value_type=str, default="config.txt"),
        ArgTemplate(name="--count", value_type=int, default=0),
    ]

    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--count", "oops"],
    )

    known, unknown = setup_config_and_cli_args(template=template)
    assert known == {}
    assert unknown == ["--count", "oops"]
