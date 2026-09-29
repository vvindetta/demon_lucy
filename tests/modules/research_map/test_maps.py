from pathlib import Path

import pytest

from demon_lucy.modules.research_map.documents import ResearchMapError
from demon_lucy.modules.research_map.maps import init_map


@pytest.mark.parametrize("name", ["topic_map", "travel/serbia/topic_map"])
def test_init_map_needs_no_registry_and_creates_only_local_files(
    tmp_path: Path, name: str
) -> None:
    changed = init_map(
        root=tmp_path, map_name=name, title="Topic", goal="Goal", seed="Seed"
    )
    map_dir = tmp_path / name
    assert set(path.name for path in map_dir.iterdir()) == {
        "index.md",
        "questions.md",
        "b-nodes",
    }
    index = (map_dir / "index.md").read_text()
    assert "Goal: Goal" in index
    assert index.endswith("## Seed\n\n> Seed\n")
    assert "created:" not in index and "updated:" not in index
    assert not (tmp_path / "index.md").exists()
    assert set(changed) == {str(map_dir / "index.md"), str(map_dir / "questions.md")}


def test_init_map_does_not_read_or_change_existing_shared_index(tmp_path: Path) -> None:
    index = tmp_path / "index.md"
    content = "An unrelated user note, not a map registry.\n"
    index.write_text(content)
    init_map(
        root=tmp_path, map_name="topic_map", title="Topic", goal="Goal", seed="Seed"
    )
    assert index.read_text() == content


def test_init_map_rejects_existing_map_without_overwriting(tmp_path: Path) -> None:
    init_map(
        root=tmp_path, map_name="topic_map", title="Topic", goal="Goal", seed="Seed"
    )
    index = tmp_path / "topic_map" / "index.md"
    original = index.read_bytes()
    with pytest.raises(ResearchMapError, match="already exists"):
        init_map(
            root=tmp_path, map_name="topic_map", title="Other", goal="Goal", seed="Seed"
        )
    assert index.read_bytes() == original


def test_init_map_rejects_symlinked_ancestor(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ResearchMapError, match="symlink"):
        init_map(
            root=tmp_path,
            map_name="link/nested/topic_map",
            title="Topic",
            goal="Goal",
            seed="Seed",
        )
    assert list(outside.iterdir()) == []
