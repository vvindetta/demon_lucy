from __future__ import annotations

import time
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import pytest

from demon_lucy.lib.args.parser import (
    ArgTemplate,
    normalize_template_params,
)
from demon_lucy.lib.dynamic_blocks.parser import (
    format_dynamic_block,
    format_fenced_body,
    parse_dynamic_blocks,
)
from demon_lucy.lib.dynamic_blocks.refresh import refresh_dynamic_blocks


class _Period(StrEnum):
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"
    ALL = "all"


def test_parse_fenced_block_uses_arg_params_and_body_offsets() -> None:
    text = (
        "before\n"
        "--- graph begin ---\n"
        "- source: past.md\n"
        "- pattern: sleep:deep\n"
        "- period [week|month|year|all]: week\n"
        "\n"
        "```text\n"
        "old body\n"
        "```\n"
        "\n"
        "--- graph end ---\n"
        "after\n"
    )

    blocks = parse_dynamic_blocks(text)

    assert len(blocks) == 1
    block = blocks[0]
    assert block.arg == "graph"
    assert block.params == {
        "source": "past.md",
        "pattern": "sleep:deep",
        "period": "week",
    }
    assert block.body == "```text\nold body\n```\n"
    assert text[block.body_start : block.body_end] == block.body
    assert block.line == 2
    assert block.end_line == 11


def test_format_block_shows_and_hides_enum_values() -> None:
    arg_template = ArgTemplate(
        name="--graph",
        params=(
            ArgTemplate(
                name="period",
                value_type=_Period,
                default=_Period.YEAR,
            ),
        ),
    )

    shown = format_dynamic_block(
        arg="graph",
        params={"period": "year"},
        body="body",
        arg_template=arg_template,
    )
    hidden = format_dynamic_block(
        arg="graph",
        params={"period": "year"},
        body="body",
        arg_template=arg_template,
        show_allowed_values=False,
    )

    assert "- period [week|month|year|all]: year\n" in shown
    assert "- period: year\n" in hidden
    assert "updated:" in shown
    assert "less than a minute ago\n" in shown
    assert parse_dynamic_blocks(shown)[0].params == {"period": "year"}


def test_arg_template_normalizes_enum_values_and_defaults() -> None:
    params = (
        ArgTemplate(name="source", required=True),
        ArgTemplate(
            name="period",
            value_type=_Period,
            default=_Period.YEAR,
        ),
    )

    assert normalize_template_params(
        {"source": " past.md ", "period": "WEEK"},
        params,
    ) == {"source": "past.md", "period": _Period.WEEK}
    assert normalize_template_params(
        {"source": "past.md"},
        params,
    ) == {"source": "past.md", "period": _Period.YEAR}

    with pytest.raises(ValueError, match="unsupported.*period"):
        normalize_template_params(
            {"source": "past.md", "period": "day"},
            params,
        )


def test_parse_raw_markdown_and_multiple_same_arg_blocks() -> None:
    first = format_dynamic_block(
        arg="graph",
        params={"source": "past.md", "pattern": "sleep", "period": "week"},
        body="| time | count |\n|---|---:|\n| today | 1 |",
    )
    second = format_dynamic_block(
        arg="graph",
        params={"source": "past.md", "pattern": "work", "period": "month"},
        body=format_fenced_body("work body", info="text"),
    )

    blocks = parse_dynamic_blocks(first + "\n" + second)

    assert [block.params["pattern"] for block in blocks] == ["sleep", "work"]
    assert blocks[0].body.endswith("| today | 1 |\n")
    assert blocks[1].body == "```text\nwork body\n```\n"


def test_format_block_uses_longer_fence_than_body_backticks() -> None:
    text = format_dynamic_block(
        arg="example",
        params={"value": "one"},
        body=format_fenced_body("before\n```\nafter", info="code"),
    )

    assert "````code\n" in text
    assert "\n````\n\n--- example end ---" in text
    assert parse_dynamic_blocks(text)[0].body == (
        "````code\nbefore\n```\nafter\n````\n"
    )


def test_format_and_parse_preserve_crlf() -> None:
    text = format_dynamic_block(
        arg="graph-regex",
        params={"source": "past.md", "pattern": "sleep", "period": "month"},
        body=format_fenced_body(
            "first\nsecond",
            info="text",
            newline="\r\n",
        ),
        newline="\r\n",
    )

    assert "\n" not in text.replace("\r\n", "")
    block = parse_dynamic_blocks(text)[0]
    assert block.body == "```text\r\nfirst\r\nsecond\r\n```\r\n"


@pytest.mark.parametrize(
    "text",
    [
        "--- graph begin ---\n- source: one\n- source: two\n\n\n--- graph end ---\n",
        "--- graph begin ---\nsource: one\n\n\n--- graph end ---\n",
        "--- graph begin ---\n- source: one\n\nbody\n\n--- other end ---\n",
        "--- graph end ---\n",
        (
            "--- graph begin ---\n- source: one\n\n"
            "--- graph begin ---\n- source: two\n\n\n--- graph end ---\n"
            "\n--- graph end ---\n"
        ),
    ],
)
def test_parse_rejects_ambiguous_structure(text: str) -> None:
    with pytest.raises(ValueError):
        parse_dynamic_blocks(text)


def test_refresh_updates_same_arg_blocks_independently(tmp_path: Path) -> None:
    text = format_dynamic_block(
        arg="graph",
        params={"pattern": "sleep"},
        body=format_fenced_body("old sleep", info="text"),
    ) + format_dynamic_block(
        arg="graph",
        params={"pattern": "work"},
        body=format_fenced_body("old work", info="text"),
    )

    refreshed, changed = refresh_dynamic_blocks(
        text=text,
        target_path=str(tmp_path / "note.md"),
        renderers={"graph": lambda block, _path: f"new {block.params['pattern']}"},
        event_id="evt-test",
    )

    assert changed == 2
    assert "new sleep\n" in refreshed
    assert "new work\n" in refreshed
    assert "```text" not in refreshed


def test_refresh_preserves_update_time_when_rendered_body_is_unchanged(
    tmp_path: Path,
) -> None:
    first_update = int(time.time() // 60) * 60 - 4 * 60 * 60
    text = format_dynamic_block(
        arg="example",
        params={},
        body="same body",
        updated_timestamp=first_update,
    )
    text = text.replace("4 hours ago", "less than a minute ago")

    refreshed, changed = refresh_dynamic_blocks(
        text=text,
        target_path=str(tmp_path / "note.md"),
        renderers={"example": lambda _block, _path: "same body"},
    )

    assert changed == 1
    expected_at = datetime.fromtimestamp(first_update).astimezone().strftime(
        "%Y.%m.%d %H:%M"
    )
    assert f"updated: {expected_at}, 4 hours ago\n" in refreshed
    block = parse_dynamic_blocks(refreshed)[0]
    assert block.body == "same body\n"
    assert block.updated_timestamp == first_update


def test_refresh_sets_update_time_when_rendered_body_changes(tmp_path: Path) -> None:
    previous_update = time.time() - 4 * 60 * 60
    text = format_dynamic_block(
        arg="example",
        params={},
        body="old body",
        updated_timestamp=previous_update,
    )

    refreshed, changed = refresh_dynamic_blocks(
        text=text,
        target_path=str(tmp_path / "note.md"),
        renderers={"example": lambda _block, _path: "new body"},
    )

    assert changed == 1
    assert "less than a minute ago\n" in refreshed
    assert parse_dynamic_blocks(refreshed)[0].body == "new body\n"


def test_refresh_preserves_failed_unknown_and_successful_blocks(
    tmp_path: Path,
) -> None:
    failed = format_dynamic_block(
        arg="graph",
        params={"pattern": "fail"},
        body=format_fenced_body("last good graph", info="text"),
    )
    unknown = format_dynamic_block(
        arg="summary",
        params={},
        body="last good summary",
    )
    good = format_dynamic_block(
        arg="graph",
        params={"pattern": "ok"},
        body="old",
    )

    def render(block, _path: str) -> str:
        if block.params["pattern"] == "fail":
            raise ValueError("invalid test block")
        return "new markdown"

    refreshed, changed = refresh_dynamic_blocks(
        text=failed + unknown + good,
        target_path=str(tmp_path / "note.md"),
        renderers={"graph": render},
    )

    assert changed == 1
    assert "last good graph" in refreshed
    assert "last good summary" in refreshed
    assert "new markdown\n\n--- graph end ---" in refreshed


def test_refresh_raises_before_partial_change_on_invalid_structure() -> None:
    valid = format_dynamic_block(
        arg="graph",
        params={"pattern": "ok"},
        body=format_fenced_body("old", info="text"),
    )
    malformed = "--- graph begin ---\n- pattern: broken\n"

    with pytest.raises(ValueError):
        refresh_dynamic_blocks(
            text=valid + malformed,
            target_path="note.md",
            renderers={"graph": lambda _block, _path: "new"},
        )
