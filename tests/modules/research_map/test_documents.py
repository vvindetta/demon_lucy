from pathlib import Path

import pytest

from demon_lucy.modules.research_map.documents import (
    ResearchMapError,
    contains_markdown_table,
    count_h1_headings,
    ensure_timestamp,
    extract_h1,
    markdown_targets,
    question_sort_key,
    read_document,
    slugify,
)
from demon_lucy.modules.research_map.models import (
    ResearchMapAction,
    ResearchMapStatus,
    ValidationResult,
)


def test_research_map_models_are_typed() -> None:
    assert ResearchMapStatus.OPEN.value == "open"
    assert ResearchMapAction.REGISTER.value == "register"
    assert ResearchMapAction.NEW_NODE.value == "new-node"
    assert ValidationResult(errors=("broken",), warnings=()).is_valid is False


def test_read_document_parses_frontmatter_and_body(tmp_path: Path) -> None:
    path = tmp_path / "node.md"
    path.write_text(
        '---\nid: "1"\ntype: question\nstatus: open\n'
        "created: 2026-08-08 12:00\nupdated: 2026-08-08 12:00\n"
        "---\n\n# Full question?\n",
        encoding="utf-8",
    )

    data, body, text = read_document(path)

    assert data["id"] == "1"
    assert extract_h1(body) == "Full question?"
    assert text.startswith("---\n")


def test_read_document_rejects_missing_frontmatter(tmp_path: Path) -> None:
    path = tmp_path / "broken.md"
    path.write_text("# Missing metadata\n", encoding="utf-8")

    with pytest.raises(ResearchMapError, match="missing YAML frontmatter"):
        read_document(path)


def test_markdown_helpers_use_parser_not_regex_only() -> None:
    text = "# Title\n\n[Node](b-nodes/1_node.md)\n\n| A | B |\n|---|---|\n| 1 | 2 |\n"

    assert markdown_targets(text) == ["b-nodes/1_node.md"]
    assert contains_markdown_table(text) is True
    assert count_h1_headings(text) == 1
    assert question_sort_key("2.10.3") == (2, 10, 3)
    assert slugify("Автоматическое обслуживание") == (
        "автоматическое-обслуживание"
    )


@pytest.mark.parametrize("value", ["", "2026-13-01 12:00", "2026-08-08"])
def test_ensure_timestamp_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ResearchMapError, match="expected YYYY-MM-DD HH:MM"):
        ensure_timestamp(value)
