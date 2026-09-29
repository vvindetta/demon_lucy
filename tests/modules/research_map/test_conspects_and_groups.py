from pathlib import Path

import pytest
from watchdog.events import FileModifiedEvent, DirMovedEvent

from demon_lucy.lib.args.parser import parse_args
from demon_lucy.modules.abstract_module import Context, System
from demon_lucy.modules.research_map import ResearchMap
from demon_lucy.modules.research_map.config import RESEARCH_MAP_TEMPLATE
from demon_lucy.modules.research_map.maps import init_map
from demon_lucy.modules.research_map.models import NodeType, ResearchMapStatus
from demon_lucy.modules.research_map.nodes import create_node, reconcile_root_entries
from demon_lucy.modules.research_map.paths import map_name_for_path
from demon_lucy.modules.research_map.questions import rebuild_questions
from demon_lucy.modules.research_map.validation import validate_map


def make_map(root: Path, name: str = "topic_map") -> Path:
    init_map(root=root, map_name=name, title="Topic", goal="Goal", seed="Seed")
    return root / name


def add(
    map_dir: Path, label: str, *, parent: str | None = None, conspect: bool = False
) -> Path:
    return create_node(
        map_dir=map_dir,
        title="" if conspect else label,
        label=label,
        parent=parent,
        summary=None,
        status=ResearchMapStatus.OPEN,
        node_type=NodeType.CONSPECT if conspect else NodeType.NODE,
    )


def test_empty_conspect_and_its_children_do_not_invent_content(tmp_path: Path) -> None:
    map_dir = make_map(tmp_path)
    parent = add(map_dir, "Links", conspect=True)
    original = parent.read_bytes()
    assert original == b'---\nid: "1"\ntype: conspect\n---\n\n\n'
    add(map_dir, "Detail", parent="1")
    child = add(map_dir, "More", parent="1", conspect=True)
    child.write_text(child.read_text() + "Only this exact text.\n")
    child_before = child.read_bytes()
    add(map_dir, "Deep", parent="1.2", conspect=True)
    rebuild_questions(map_dir)
    assert parent.read_bytes() == original
    assert child.read_bytes() == child_before
    overview = (map_dir / "questions.md").read_text()
    assert "Detail" in overview
    assert "Only this exact text" not in overview and "Links" not in overview
    result = validate_map(map_dir)
    assert result.is_valid, result.errors
    assert rebuild_questions(map_dir) == {}
    assert reconcile_root_entries(map_dir) == {}


def test_grouping_roots_preserves_ids_and_needs_no_parent_node(tmp_path: Path) -> None:
    map_dir = make_map(tmp_path)
    first = add(map_dir, "Petrus")
    second = add(map_dir, "Stalac", conspect=True)
    group = map_dir / "b-nodes" / "nearby" / "day_trip"
    group.mkdir(parents=True)
    originals = {path.name: path.read_bytes() for path in (first, second)}
    for path in (first, second):
        path.rename(group / path.name)
    reconcile_root_entries(map_dir)
    rebuild_questions(map_dir)
    assert sorted(path.name for path in (map_dir / "b-nodes").rglob("*.md")) == sorted(
        originals
    )
    for name, content in originals.items():
        assert (group / name).read_bytes() == content
    result = validate_map(map_dir)
    assert result.is_valid, result.errors
    # Numbering belongs to the map, not the grouping folder.
    assert add(map_dir, "Next").name == "3_next.md"
    child = add(map_dir, "Detail", parent="1")
    assert child == group / "1_petrus" / "1.1_detail.md"
    rebuild_questions(map_dir)
    assert validate_map(map_dir).is_valid


@pytest.mark.parametrize("name", ["topic_map", "travel/serbia/topic_map"])
def test_automatic_maintenance_finds_unregistered_nested_maps_and_leaves_conspect_body(
    tmp_path: Path,
    name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    map_dir = make_map(tmp_path, name)
    node = add(map_dir, "Place")
    conspect = add(map_dir, "Notes", conspect=True)
    conspect.write_text(conspect.read_text() + "Requested wording only.\n")
    original = conspect.read_bytes()
    node.write_text(node.read_text().replace("status: open", "status: done"))
    module = ResearchMap()
    args = parse_args(
        args=["--research-map-root", str(tmp_path)], template=RESEARCH_MAP_TEMPLATE
    )
    ctx = Context(
        path=str(node),
        args=args,
        run_mode="daemon",
        event_id="test",
        event=FileModifiedEvent(str(node)),
    )
    errors = []
    monkeypatch.setattr(
        "demon_lucy.modules.research_map.module.safe_notify",
        lambda **kwargs: errors.append(kwargs),
    )
    system = System(global_template=RESEARCH_MAP_TEMPLATE, modules=[module])
    assert map_name_for_path(tmp_path, str(node)) == name
    assert module.modified(ctx, system) is not None
    assert "## Done" in (map_dir / "questions.md").read_text()
    assert conspect.read_bytes() == original
    assert module.modified(ctx, system) is None
    assert not errors
    assert not (tmp_path / "index.md").exists()


def test_directory_move_refreshes_group_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    map_dir = make_map(tmp_path)
    node = add(map_dir, "Place", conspect=True)
    old_group = map_dir / "b-nodes" / "before"
    old_group.mkdir()
    node.rename(old_group / node.name)
    reconcile_root_entries(map_dir)
    new_group = old_group.with_name("after")
    old_group.rename(new_group)
    module = ResearchMap()
    args = parse_args(
        args=["--research-map-root", str(tmp_path)], template=RESEARCH_MAP_TEMPLATE
    )
    ctx = Context(
        path=str(new_group),
        args=args,
        run_mode="daemon",
        event_id="move",
        event=DirMovedEvent(str(old_group), str(new_group)),
    )
    errors = []
    monkeypatch.setattr(
        "demon_lucy.modules.research_map.module.safe_notify",
        lambda **kwargs: errors.append(kwargs),
    )
    result = module.moved(
        ctx, System(global_template=RESEARCH_MAP_TEMPLATE, modules=[module])
    )
    assert result is not None
    assert "b-nodes/after/1_place.md" in (map_dir / "index.md").read_text()
    assert not errors


def test_grouping_does_not_hide_duplicate_ids_or_broken_links(tmp_path: Path) -> None:
    map_dir = make_map(tmp_path)
    node = add(map_dir, "Place")
    group = map_dir / "b-nodes" / "group"
    group.mkdir()
    (group / node.name).write_text(node.read_text() + "\n[Missing](missing.md)\n")
    errors = validate_map(map_dir).errors
    assert any("duplicate" in error for error in errors)
    assert any("broken link" in error for error in errors)


def test_manual_conspect_is_indexed_without_modifying_its_bytes(tmp_path: Path) -> None:
    map_dir = make_map(tmp_path)
    group = map_dir / "b-nodes" / "places"
    group.mkdir()
    conspect = group / "1_notes.md"
    content = '---\nid: "1"\ntype: conspect\n---\n\nKeep exactly this.\n'
    conspect.write_text(content)
    assert reconcile_root_entries(map_dir)
    assert "[conspect](b-nodes/places/1_notes.md)" in (map_dir / "index.md").read_text()
    assert conspect.read_text() == content
    assert validate_map(map_dir).is_valid
    assert reconcile_root_entries(map_dir) == {}


def test_reconcile_preserves_seed_even_when_it_contains_node_like_links(
    tmp_path: Path,
) -> None:
    map_dir = make_map(tmp_path)
    node = add(map_dir, "Place")
    index = map_dir / "index.md"
    seed = "## Seed\n\n1 - Example [open](old_path.md):\nOriginal request.\n"
    index.write_text(index.read_text().split("## Seed", 1)[0] + seed)
    node.write_text(node.read_text().replace("status: open", "status: done"))
    reconcile_root_entries(map_dir)
    assert index.read_text().endswith(seed)
    assert "1 - Place [done]" in index.read_text()
