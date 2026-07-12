from __future__ import annotations

from pathlib import Path

import pytest

from demon_lucy.lib.dynamic_blocks.parser import (
    format_dynamic_block,
    format_fenced_body,
    parse_dynamic_blocks,
)
from demon_lucy.lib.dynamic_blocks.refresh import refresh_dynamic_blocks


def test_parse_fenced_block_uses_arg_params_and_body_offsets() -> None:
    text = (
        "before\n"
        "--- graph begin ---\n"
        "- source: past.md\n"
        "- pattern: sleep:deep\n"
        "- period: week\n"
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
