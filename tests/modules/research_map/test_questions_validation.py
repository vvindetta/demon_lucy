from pathlib import Path

from demon_lucy.modules.research_map.maps import init_map
from demon_lucy.modules.research_map.models import ResearchMapStatus
from demon_lucy.modules.research_map.nodes import create_node
from demon_lucy.modules.research_map.questions import rebuild_questions
from demon_lucy.modules.research_map.validation import validate_map


def _map(tmp_path: Path) -> Path:
    root = tmp_path / "maps"
    root.mkdir()
    (root / "index.md").write_text("# Maps\n\n## Active\n", encoding="utf-8")
    init_map(
        root=root,
        map_name="lucy_map",
        title="Lucy",
        goal="Goal",
        seed="Seed",
    )
    return root / "lucy_map"


def test_rebuild_questions_groups_nodes_and_is_idempotent(tmp_path: Path) -> None:
    map_dir = _map(tmp_path)
    create_node(
        map_dir=map_dir,
        title="Open question?",
        label="Open",
        parent=None,
        summary="Open branch",
        status=ResearchMapStatus.OPEN,
    )
    create_node(
        map_dir=map_dir,
        title="Parked child?",
        label="Parked",
        parent="1",
        summary=None,
        status=ResearchMapStatus.PARKED,
    )

    changed = rebuild_questions(map_dir)
    text = (map_dir / "questions.md").read_text(encoding="utf-8")

    assert "## Open" in text
    assert "1 - Open [open](b-nodes/1_open.md):" in text
    assert "## Parked" in text
    assert "1.1 - Parked [parked](b-nodes/1_open/1.1_parked.md):" in text
    assert changed == {str((map_dir / "questions.md").resolve()): 1}
    assert rebuild_questions(map_dir) == {}


def test_validate_map_accepts_rebuilt_map(tmp_path: Path) -> None:
    map_dir = _map(tmp_path)
    create_node(
        map_dir=map_dir,
        title="Question?",
        label="Question",
        parent=None,
        summary="Question branch",
        status=ResearchMapStatus.OPEN,
    )
    rebuild_questions(map_dir)
    assert validate_map(map_dir).is_valid is True


def test_validate_map_accepts_deep_node_tree(tmp_path: Path) -> None:
    map_dir = _map(tmp_path)
    create_node(
        map_dir=map_dir,
        title="Root?",
        label="Root",
        parent=None,
        summary="Root branch",
        status=ResearchMapStatus.OPEN,
    )
    create_node(
        map_dir=map_dir,
        title="Child?",
        label="Child",
        parent="1",
        summary=None,
        status=ResearchMapStatus.OPEN,
    )
    create_node(
        map_dir=map_dir,
        title="Grandchild?",
        label="Grandchild",
        parent="1.1",
        summary=None,
        status=ResearchMapStatus.DONE,
    )
    rebuild_questions(map_dir)

    result = validate_map(map_dir)

    assert result.is_valid is True, result.errors


def test_validate_map_rejects_node_outside_parent_companion_directory(
    tmp_path: Path,
) -> None:
    map_dir = _map(tmp_path)
    create_node(
        map_dir=map_dir,
        title="Root?",
        label="Root",
        parent=None,
        summary="Root branch",
        status=ResearchMapStatus.OPEN,
    )
    child = create_node(
        map_dir=map_dir,
        title="Child?",
        label="Child",
        parent="1",
        summary=None,
        status=ResearchMapStatus.OPEN,
    )
    misplaced = map_dir / "b-nodes" / child.name
    child.rename(misplaced)

    result = validate_map(map_dir)

    assert any("must be inside" in error for error in result.errors)


def test_validate_map_ignores_unrelated_directory(tmp_path: Path) -> None:
    map_dir = _map(tmp_path)
    (map_dir / "nodes").mkdir()

    result = validate_map(map_dir)

    assert result.is_valid


def test_validate_map_reports_stale_questions_and_broken_links(tmp_path: Path) -> None:
    map_dir = _map(tmp_path)
    node = create_node(
        map_dir=map_dir,
        title="Question?",
        label="Question",
        parent=None,
        summary="Question branch",
        status=ResearchMapStatus.OPEN,
    )
    node.write_text(
        node.read_text(encoding="utf-8") + "\n[Missing](missing.md)\n",
        encoding="utf-8",
    )
    result = validate_map(map_dir)
    assert any("questions.md is stale" in error for error in result.errors)
    assert any("broken link" in error for error in result.errors)


def test_validate_map_does_not_require_root_branch_summary(tmp_path: Path) -> None:
    map_dir = _map(tmp_path)
    create_node(
        map_dir=map_dir,
        title="Question?",
        label="Question",
        parent=None,
        summary="Required summary",
        status=ResearchMapStatus.OPEN,
    )
    rebuild_questions(map_dir)
    index = map_dir / "index.md"
    index.write_text(
        index.read_text(encoding="utf-8").replace("* Required summary\n", ""),
        encoding="utf-8",
    )

    result = validate_map(map_dir)

    assert result.is_valid, result.errors
