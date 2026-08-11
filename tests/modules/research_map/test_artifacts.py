import stat
from pathlib import Path

import pytest

from demon_lucy.modules.research_map.artifacts import create_artifact
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


def test_create_artifact_is_sequential_and_read_only(tmp_path: Path) -> None:
    map_dir = make_map(tmp_path)
    first = create_artifact(
        map_dir=map_dir,
        title="Architecture",
        body="## Result\n\nLucy owns map operations.",
        question=None,
        timestamp="2026-08-08 12:01",
    )
    second = create_artifact(
        map_dir=map_dir,
        title="Rollout",
        body="## Result\n\nThe skill calls oneshot.",
        question=None,
        timestamp="2026-08-08 12:02",
    )

    assert first.name == "a1-architecture.md"
    assert second.name == "a2-rollout.md"
    assert first.stat().st_mode & stat.S_IWUSR == 0
    assert first.read_text(encoding="utf-8").count("# Architecture") == 1


def test_create_artifact_accepts_existing_question_in_filename(tmp_path: Path) -> None:
    map_dir = make_map(tmp_path)
    create_node(
        map_dir=map_dir,
        question="Question?",
        label="Question",
        parent=None,
        summary="Question branch",
        status=ResearchMapStatus.OPEN,
        timestamp="2026-08-08 12:01",
    )

    path = create_artifact(
        map_dir=map_dir,
        title="Evidence",
        body="Result.",
        question="1",
        timestamp="2026-08-08 12:02",
    )

    assert path.name == "a1-1-evidence.md"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("# Extra H1", "must not contain another H1"),
        ("| A | B |\n|---|---|\n| 1 | 2 |", "table is not allowed"),
        ("![Remote](https://example.com/a.png)", "stored locally"),
        ("[Missing](missing.md)", "broken link"),
    ],
)
def test_create_artifact_rejects_invalid_body(
    tmp_path: Path,
    body: str,
    message: str,
) -> None:
    map_dir = make_map(tmp_path)

    with pytest.raises(ResearchMapError, match=message):
        create_artifact(
            map_dir=map_dir,
            title="Invalid",
            body=body,
            question=None,
            timestamp="2026-08-08 12:01",
        )

    assert not (map_dir / "artifacts").exists()


def test_create_artifact_rejects_missing_question_without_empty_directory(
    tmp_path: Path,
) -> None:
    map_dir = make_map(tmp_path)

    with pytest.raises(ResearchMapError, match="question does not exist"):
        create_artifact(
            map_dir=map_dir,
            title="Invalid",
            body="Body",
            question="9",
            timestamp="2026-08-08 12:01",
        )

    assert not (map_dir / "artifacts").exists()
