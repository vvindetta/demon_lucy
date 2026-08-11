from pathlib import Path

import pytest

import demon_lucy.modules.research_map.nodes as nodes_mod
from demon_lucy.modules.research_map.documents import ResearchMapError
from demon_lucy.modules.research_map.maps import init_map
from demon_lucy.modules.research_map.models import ResearchMapStatus
from demon_lucy.modules.research_map.nodes import create_node


def make_map(tmp_path: Path) -> Path:
    root = tmp_path / "maps"
    root.mkdir()
    (root / "index.md").write_text("# Maps\n\n## Active\n", encoding="utf-8")
    init_map(
        root=root,
        map_name="lucy_map",
        title="Lucy",
        goal="Goal",
        seed="Seed",
        registry_summary="Test map",
        timestamp="2026-08-08 12:00",
    )
    return root / "lucy_map"


def test_create_node_allocates_root_and_child_ids(tmp_path: Path) -> None:
    map_dir = make_map(tmp_path)
    root = create_node(
        map_dir=map_dir,
        question="Root question?",
        label="Root",
        parent=None,
        summary="Root branch result",
        status=ResearchMapStatus.OPEN,
        timestamp="2026-08-08 12:01",
    )
    child = create_node(
        map_dir=map_dir,
        question="Child question?",
        label="Child",
        parent="1",
        summary=None,
        status=ResearchMapStatus.PARKED,
        timestamp="2026-08-08 12:02",
    )

    assert root == map_dir / "b-nodes" / "1_root.md"
    assert child == map_dir / "b-nodes" / "1_root" / "1.1_child.md"
    assert 'parent: "[1](../1_root.md)"' in child.read_text(encoding="utf-8")
    assert "[1.1 - Child](1_root/1.1_child.md)" in root.read_text(encoding="utf-8")
    assert "## Child Questions" in root.read_text(encoding="utf-8")
    assert (
        "1 - Root [open](b-nodes/1_root.md):\n* Root branch result"
        in (map_dir / "index.md").read_text(encoding="utf-8")
    )


def test_create_node_builds_deep_companion_directory_tree(tmp_path: Path) -> None:
    map_dir = make_map(tmp_path)
    root = create_node(
        map_dir=map_dir,
        question="Как устроена структура?",
        label="Структура",
        parent=None,
        summary="Описание структуры",
        status=ResearchMapStatus.OPEN,
        timestamp="2026-08-08 12:01",
    )
    child = create_node(
        map_dir=map_dir,
        question="Какие есть связи?",
        label="Связи",
        parent="1",
        summary=None,
        status=ResearchMapStatus.OPEN,
        timestamp="2026-08-08 12:02",
    )
    grandchild = create_node(
        map_dir=map_dir,
        question="Какое нужно уточнение?",
        label="Уточнение",
        parent="1.1",
        summary=None,
        status=ResearchMapStatus.OPEN,
        timestamp="2026-08-08 12:03",
    )

    assert root == map_dir / "b-nodes" / "1_структура.md"
    assert child == root.with_suffix("") / "1.1_связи.md"
    assert grandchild == child.with_suffix("") / "1.1.1_уточнение.md"
    assert 'parent: "[1.1](../1.1_связи.md)"' in grandchild.read_text(
        encoding="utf-8"
    )
    assert "[1.1.1 - Уточнение](1.1_связи/1.1.1_уточнение.md)" in (
        child.read_text(encoding="utf-8")
    )


def test_create_node_builds_requested_b_nodes_layout(tmp_path: Path) -> None:
    map_dir = make_map(tmp_path)

    def add(label: str, parent: str | None = None) -> Path:
        return create_node(
            map_dir=map_dir,
            question=f"{label}?",
            label=label,
            parent=parent,
            summary=f"Ветка: {label}" if parent is None else None,
            status=ResearchMapStatus.OPEN,
            timestamp="2026-08-08 12:01",
        )

    created = [
        add("Требования"),
        add("Структура"),
        add("Связи", "2"),
        add("Уточнение", "2.1"),
        add("Частный случай", "2.1.1"),
        add("Ограничения", "2.1"),
        add("Детализация", "2"),
        add("Обновление"),
        add("Автоматизация"),
    ]

    assert [path.relative_to(map_dir).as_posix() for path in created] == [
        "b-nodes/1_требования.md",
        "b-nodes/2_структура.md",
        "b-nodes/2_структура/2.1_связи.md",
        "b-nodes/2_структура/2.1_связи/2.1.1_уточнение.md",
        (
            "b-nodes/2_структура/2.1_связи/2.1.1_уточнение/"
            "2.1.1.1_частный_случай.md"
        ),
        "b-nodes/2_структура/2.1_связи/2.1.2_ограничения.md",
        "b-nodes/2_структура/2.2_детализация.md",
        "b-nodes/3_обновление.md",
        "b-nodes/4_автоматизация.md",
    ]


def test_root_requires_summary_and_child_rejects_it(tmp_path: Path) -> None:
    map_dir = make_map(tmp_path)

    with pytest.raises(ResearchMapError, match="root node summary"):
        create_node(
            map_dir=map_dir,
            question="Root?",
            label="Root",
            parent=None,
            summary=None,
            status=ResearchMapStatus.OPEN,
        )

    create_node(
        map_dir=map_dir,
        question="Root?",
        label="Root",
        parent=None,
        summary="Root result",
        status=ResearchMapStatus.OPEN,
    )
    with pytest.raises(ResearchMapError, match="child node must not have summary"):
        create_node(
            map_dir=map_dir,
            question="Child?",
            label="Child",
            parent="1",
            summary="Wrong",
            status=ResearchMapStatus.OPEN,
        )


def test_create_child_removes_new_file_when_parent_update_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    map_dir = make_map(tmp_path)
    create_node(
        map_dir=map_dir,
        question="Root?",
        label="Root",
        parent=None,
        summary="Root result",
        status=ResearchMapStatus.OPEN,
        timestamp="2026-08-08 12:01",
    )

    def fail_write(_path: Path, _content: str) -> bool:
        raise OSError("parent write failed")

    monkeypatch.setattr(nodes_mod, "atomic_write_text_if_changed", fail_write)

    with pytest.raises(OSError, match="parent write failed"):
        create_node(
            map_dir=map_dir,
            question="Child?",
            label="Child",
            parent="1",
            summary=None,
            status=ResearchMapStatus.OPEN,
            timestamp="2026-08-08 12:02",
        )

    assert sorted(path.name for path in (map_dir / "b-nodes").glob("*.md")) == [
        "1_root.md"
    ]
    assert not (map_dir / "b-nodes" / "1_root").exists()


def test_create_root_removes_new_file_when_index_update_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    map_dir = make_map(tmp_path)

    def fail_write(_path: Path, _content: str) -> bool:
        raise OSError("index write failed")

    monkeypatch.setattr(nodes_mod, "atomic_write_text_if_changed", fail_write)

    with pytest.raises(OSError, match="index write failed"):
        create_node(
            map_dir=map_dir,
            question="Root?",
            label="Root",
            parent=None,
            summary="Root result",
            status=ResearchMapStatus.OPEN,
            timestamp="2026-08-08 12:01",
        )

    assert list((map_dir / "b-nodes").iterdir()) == []
