from __future__ import annotations

import sys
from pathlib import Path

import pytest

from demon_lucy.lib.args import (
    delete_args_from_string,
    get_args_from_file,
    get_config_args,
    merge_known_args,
    parse_args,
    setup_config_and_cli_args,
)


def test_parse_args_handles_bool_and_nargs():
    template = [
        ("--fmt-todo", bool, False, "", False),
        ("--fmt-blank", str, [], "", False),
        ("--name", str, None, "", False),
        ("--tags", str, [], "", False),
    ]

    known, unknown = parse_args(
        args=[
            "--fmt-todo",
            "--fmt-blank",
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

    assert known["fmt_todo"] is True
    assert known["fmt_blank"] == ["up", "down"]
    assert known["name"] == "alice"
    assert known["tags"] == ["x", "y"]
    assert unknown == ["--unknown"]


def test_parse_args_supports_required_field_in_template_item():
    template = [
        ("--required-path", str, None, "", True),
    ]

    known, unknown = parse_args(
        args=["--required-path", "/tmp/a.md"],
        template=template,
    )

    assert known["required_path"] == "/tmp/a.md"
    assert unknown == []


def test_get_config_args_reads_lines_and_ignores_comments(tmp_path: Path):
    cfg = tmp_path / "config.txt"
    cfg.write_text(
        "# comment\n--name jane\n\n--count 7\n",
        encoding="utf-8",
    )
    template = [
        ("--name", str, None, "", False),
        ("--count", int, 0, "", False),
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
        ("--name", str, None, "", False),
        ("--count", int, 0, "", False),
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
        # ('--banner "Hello world" body --fmt-todo --x=1 tail\n', ["--banner", "--fmt-todo", "--x"], "body tail\n"),
        (
            "prefix --fmt-todo one --fmt-todo two\n",
            ["--fmt-todo"],
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


def test_get_args_from_file_skips_non_utf8_files(tmp_path: Path):
    path = tmp_path / "image.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")

    template = [
        ("--help", bool, False, "", False),
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
        ("--sys-config-path", str, "config.txt", "", False),
        ("--sys-watch-paths", str, [], "", False),
        ("--sys-log-level", str, "warning", "", False),
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
        ("--sys-config-path", str, "config.txt", "", False),
        ("--count", int, 0, "", False),
    ]

    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--count", "oops"],
    )

    known, unknown = setup_config_and_cli_args(template=template)
    assert known == {}
    assert unknown == ["--count", "oops"]
