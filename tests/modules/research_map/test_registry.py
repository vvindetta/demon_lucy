from pathlib import Path

import pytest

from demon_lucy.modules.research_map.documents import ResearchMapError
from demon_lucy.modules.research_map.registry import (
    read_registry,
    register_map,
    update_registry_entry,
)


def make_registry_root(tmp_path: Path) -> Path:
    root = tmp_path / "maps"
    root.mkdir()
    (root / "index.md").write_text(
        "# Maps\n\n## Active\n\n## Completed\n\nRetain this text.\n",
        encoding="utf-8",
    )
    return root


def make_map(root: Path, name: str, title: str = "Title") -> Path:
    map_dir = root / name
    map_dir.mkdir()
    (map_dir / "index.md").write_text(f"# {title}\n", encoding="utf-8")
    return map_dir


def test_register_map_adds_summary_and_preserves_other_sections(tmp_path: Path) -> None:
    root = make_registry_root(tmp_path)
    make_map(root, "lucy_map", "Lucy")

    changed = register_map(
        root,
        map_name="lucy_map",
        label="Lucy",
        summary="Human-authored summary",
    )

    text = (root / "index.md").read_text(encoding="utf-8")
    assert "- [Lucy](lucy_map/index.md) - Human-authored summary" in text
    assert "## Completed\n\nRetain this text.\n" in text
    assert changed == {str((root / "index.md").resolve()): 1}
    assert read_registry(root)[0].summary == "Human-authored summary"


def test_registry_label_refresh_preserves_summary(tmp_path: Path) -> None:
    root = make_registry_root(tmp_path)
    make_map(root, "lucy_map")
    register_map(
        root,
        map_name="lucy_map",
        label="Old title",
        summary="Human-authored summary",
    )

    changed = update_registry_entry(
        root,
        map_name="lucy_map",
        label="New title",
    )

    text = (root / "index.md").read_text(encoding="utf-8")
    assert "[New title](lucy_map/index.md) - Human-authored summary" in text
    assert changed == {str((root / "index.md").resolve()): 1}


def test_registry_rejects_duplicate_target(tmp_path: Path) -> None:
    root = make_registry_root(tmp_path)
    make_map(root, "lucy_map")
    register_map(root, map_name="lucy_map", label="Lucy", summary="First")

    with pytest.raises(ResearchMapError, match="already registered"):
        register_map(root, map_name="lucy_map", label="Lucy", summary="Second")
