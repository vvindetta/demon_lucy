from pathlib import Path

import pytest

from demon_lucy.modules.research_map.artifacts import create_artifact
from demon_lucy.modules.research_map.documents import ResearchMapError
from demon_lucy.modules.research_map.maps import init_map
from demon_lucy.modules.research_map.models import ResearchMapStatus
from demon_lucy.modules.research_map.nodes import create_node, read_nodes
from demon_lucy.modules.research_map.questions import rebuild_questions
from demon_lucy.modules.research_map.validation import validate_map


def make_map(tmp_path: Path) -> tuple[Path, Path]:
    init_map(
        root=tmp_path, map_name="topic_map", title="Topic", goal="Goal", seed="Seed"
    )
    map_dir = tmp_path / "topic_map"
    node = create_node(
        map_dir=map_dir,
        title="Topic",
        label="Topic",
        parent=None,
        summary=None,
        status=ResearchMapStatus.OPEN,
    )
    return map_dir, node


@pytest.mark.parametrize(
    "relative",
    [
        "diagram.png",
        "b-nodes/diagram.png",
        "b-nodes/1_topic/diagram.png",
        "b-nodes/1_topic/images/detail/diagram.png",
        ".attach/diagram.png",
        ".attach/images/diagram.png",
        "sources/images/diagram.png",
    ],
)
def test_nodes_and_artifacts_can_link_images_anywhere_in_map(
    tmp_path: Path,
    relative: str,
) -> None:
    map_dir, node = make_map(tmp_path)
    image = map_dir / relative
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"image")
    node.write_text(node.read_text() + f"\n![Diagram](../{relative})\n")
    artifact = create_artifact(
        map_dir=map_dir,
        title="Result",
        body=f"![Diagram](../{relative})",
        question=None,
    )
    rebuild_questions(map_dir)

    result = validate_map(map_dir)
    assert result.is_valid, result.errors
    assert artifact.exists()
    assert read_nodes(map_dir) == {"1": node}
    if not relative.startswith(".attach/"):
        assert not (map_dir / ".attach").exists()


def test_supporting_files_do_not_become_nodes_or_get_rewritten(tmp_path: Path) -> None:
    map_dir, node = make_map(tmp_path)
    folder = map_dir / "b-nodes" / "1_topic" / "sources"
    folder.mkdir(parents=True)
    files = {
        folder / "guide.pdf": b"%PDF document",
        folder / "guide.pdf.md": b"Extracted text\n\n| A |\n|---|\n| 1 |\n",
        folder / "reference.md": b"---\ntitle: Reference\n---\nSource material\n",
        folder / "measurements.csv": b"x,y\n1,2\n",
        folder / "excerpt.md": b"---\nSource text, not metadata\n---\nExcerpt\n",
        folder / "raw.md": b"---\n[Unfinished source snippet\n---\nText\n",
    }
    for path, data in files.items():
        path.write_bytes(data)
    (folder / "pending").mkdir()
    node.write_text(node.read_text() + "\n[Guide](1_topic/sources/guide.pdf.md)\n")
    rebuild_questions(map_dir)
    result = validate_map(map_dir)

    assert result.is_valid, result.errors
    assert read_nodes(map_dir) == {"1": node}
    assert {path: path.read_bytes() for path in files} == files
    assert "guide.pdf.md" not in (map_dir / "questions.md").read_text()
    next_node = create_node(
        map_dir=map_dir,
        title="Next",
        label="Next",
        parent=None,
        summary=None,
        status=ResearchMapStatus.OPEN,
    )
    assert next_node.name == "2_next.md"


@pytest.mark.parametrize(
    "filename,content,message",
    [
        ("2_broken.md", "No metadata", "missing YAML frontmatter"),
        (
            "renamed.md",
            '---\nid: "2"\ntype: node\nstatus: open\n---\nTopic',
            "filename must start with 2_",
        ),
    ],
)
def test_invalid_nodes_are_not_ignored_as_attachments(
    tmp_path: Path,
    filename: str,
    content: str,
    message: str,
) -> None:
    map_dir, _ = make_map(tmp_path)
    (map_dir / "b-nodes" / filename).write_text(content)
    assert any(message in error for error in validate_map(map_dir).errors)


@pytest.mark.parametrize(
    "target,message",
    [
        ("https://example.com/image.png", "stored locally"),
        ("../../outside.png", "inside the map"),
        ("../missing.png", "broken link"),
        ("../b-nodes", "image is not a file"),
    ],
)
def test_local_images_still_require_real_files_within_map(
    tmp_path: Path,
    target: str,
    message: str,
) -> None:
    map_dir, node = make_map(tmp_path)
    (tmp_path / "outside.png").write_bytes(b"outside")
    body = f"![Image]({target})"
    node.write_text(node.read_text() + "\n" + body + "\n")
    rebuild_questions(map_dir)

    assert any(message in error for error in validate_map(map_dir).errors)
    with pytest.raises(ResearchMapError, match=message):
        create_artifact(map_dir=map_dir, title="Invalid", body=body, question=None)


def test_symlinked_image_cannot_escape_map(tmp_path: Path) -> None:
    map_dir, node = make_map(tmp_path)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    (map_dir / "b-nodes" / "image.png").symlink_to(outside)
    node.write_text(node.read_text() + "\n![Image](image.png)\n")
    rebuild_questions(map_dir)
    errors = validate_map(map_dir).errors
    assert any("symlink" in error for error in errors)
    assert any("inside the map" in error for error in errors)
