from pathlib import Path

import pytest

import demon_lucy.modules.research_map.maps as maps_mod
from demon_lucy.modules.research_map.maps import init_map


def make_registry_root(tmp_path: Path) -> Path:
    root = tmp_path / "maps"
    root.mkdir()
    (root / "index.md").write_text(
        "# Maps\n\n## Active\n\n## Completed\n\nRetain this text.\n",
        encoding="utf-8",
    )
    return root


def test_init_map_registers_summary_and_creates_only_required_paths(
    tmp_path: Path,
) -> None:
    root = make_registry_root(tmp_path)

    changed = init_map(
        root=root,
        map_name="lucy_map",
        title="Lucy research",
        goal="Move map mechanics into Lucy",
        seed="Initial user wording",
        registry_summary="Lucy owns deterministic map mechanics",
        timestamp="2026-08-08 12:00",
    )

    map_dir = root / "lucy_map"
    assert (map_dir / "b-nodes").is_dir()
    assert (map_dir / "index.md").read_text(encoding="utf-8").endswith(
        "## Seed\n\n> Initial user wording\n"
    )
    assert "Goal: Move map mechanics into Lucy" in (
        map_dir / "index.md"
    ).read_text(encoding="utf-8")
    assert "## Main Branches" in (map_dir / "index.md").read_text(
        encoding="utf-8"
    )
    assert "# Questions" in (map_dir / "questions.md").read_text(
        encoding="utf-8"
    )
    assert (map_dir / "questions.md").is_file()
    assert not (map_dir / ".attach").exists()
    assert not (map_dir / "artifacts").exists()
    registry = (root / "index.md").read_text(encoding="utf-8")
    assert (
        "- [Lucy research](lucy_map/index.md) - "
        "Lucy owns deterministic map mechanics"
    ) in registry
    assert "## Completed\n\nRetain this text.\n" in registry
    assert set(changed) == {
        str((root / "index.md").resolve()),
        str((map_dir / "index.md").resolve()),
        str((map_dir / "questions.md").resolve()),
    }


def test_init_map_creates_registry_when_missing(tmp_path: Path) -> None:
    root = tmp_path / "maps"
    root.mkdir()

    init_map(
        root=root,
        map_name="lucy_map",
        title="Lucy research",
        goal="Create the first map",
        seed="Initial wording",
        registry_summary="First map in this root",
        timestamp="2026-08-08 12:00",
    )

    assert (root / "index.md").read_text(encoding="utf-8") == (
        "# Research Maps\n\n"
        "## Active\n\n"
        "- [Lucy research](lucy_map/index.md) - First map in this root\n"
    )


def test_init_map_rolls_back_exact_initial_map_when_registry_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_registry_root(tmp_path)

    def fail_write(_path: Path, _content: str) -> bool:
        raise OSError("registry write failed")

    monkeypatch.setattr(maps_mod, "atomic_write_text_if_changed", fail_write)

    with pytest.raises(OSError, match="registry write failed"):
        init_map(
            root=root,
            map_name="lucy_map",
            title="Lucy",
            goal="Goal",
            seed="Seed",
            registry_summary="Summary",
            timestamp="2026-08-08 12:00",
        )

    assert not (root / "lucy_map").exists()
    assert "lucy_map/index.md" not in (root / "index.md").read_text(
        encoding="utf-8"
    )
