import os
import stat
from pathlib import Path

import pytest

import demon_lucy.modules.research_map.paths as paths_mod
from demon_lucy.modules.research_map.documents import ResearchMapError
from demon_lucy.modules.research_map.paths import (
    PutTarget,
    classify_put_target,
    discover_map_dirs,
    resolve_map_dir,
    resolve_root,
    safe_tmp_file,
    validate_map_name,
)
from demon_lucy.modules.research_map.storage import (
    atomic_copy,
    atomic_write_text_if_changed,
    publish_exclusive_text,
)


def test_discovery_uses_immediate_non_symlink_map_directories(tmp_path: Path) -> None:
    root = tmp_path / "maps"
    root.mkdir()
    good = root / "lucy_map"
    good.mkdir()
    (root / "ignored").mkdir()
    (good / "nested_map").mkdir()
    os.symlink(good, root / "link_map", target_is_directory=True)

    assert discover_map_dirs(resolve_root(str(root))) == {
        "lucy_map": good.resolve()
    }


@pytest.mark.parametrize(
    "name",
    ["", "_map", "../bad_map", "nested/bad_map", "bad", "UPPER_map", "a_b_map"],
)
def test_validate_map_name_rejects_unsafe_or_unsuffixed_names(name: str) -> None:
    with pytest.raises(ResearchMapError, match="map name"):
        validate_map_name(name)


def test_resolve_map_dir_rejects_symlink(tmp_path: Path) -> None:
    root = tmp_path / "maps"
    root.mkdir()
    real = root / "real_map"
    real.mkdir()
    link = root / "link_map"
    os.symlink(real, link, target_is_directory=True)

    with pytest.raises(ResearchMapError, match="symlink"):
        resolve_map_dir(root.resolve(), "link_map", must_exist=True)


def test_safe_tmp_file_rejects_file_outside_configured_tmp_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    source = tmp_path / "outside.md"
    source.write_text("body", encoding="utf-8")
    monkeypatch.setattr(paths_mod, "TMP_ROOT", allowed)

    with pytest.raises(ResearchMapError, match=f"below {allowed}"):
        safe_tmp_file(str(source))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("index.md", PutTarget(Path("index.md"), overwrite=True)),
        (
            "b-nodes/1_example.md",
            PutTarget(Path("b-nodes/1_example.md"), overwrite=True),
        ),
        (
            "b-nodes/1_example/1.1_child.md",
            PutTarget(Path("b-nodes/1_example/1.1_child.md"), overwrite=True),
        ),
        (
            ".attach/diagram.png",
            PutTarget(Path(".attach/diagram.png"), overwrite=False),
        ),
    ],
)
def test_classify_put_target_accepts_only_mutable_contract(
    value: str,
    expected: PutTarget,
) -> None:
    assert classify_put_target(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "questions.md",
        "artifacts/a1-result.md",
        "nodes/1_node.md",
        "../index.md",
        "/tmp/index.md",
    ],
)
def test_classify_put_target_rejects_derived_immutable_or_unsafe_paths(
    value: str,
) -> None:
    with pytest.raises(ResearchMapError, match="target"):
        classify_put_target(value)


def test_atomic_write_is_idempotent_and_preserves_mode(tmp_path: Path) -> None:
    path = tmp_path / "index.md"
    path.write_text("old\n", encoding="utf-8")
    path.chmod(0o640)

    assert atomic_write_text_if_changed(path, "new\n") is True
    first_mtime = path.stat().st_mtime_ns
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert atomic_write_text_if_changed(path, "new\n") is False
    assert path.stat().st_mtime_ns == first_mtime


def test_publish_exclusive_text_refuses_existing_target(tmp_path: Path) -> None:
    path = tmp_path / "node.md"
    publish_exclusive_text(path, "first\n", mode=0o644)

    with pytest.raises(FileExistsError):
        publish_exclusive_text(path, "second\n", mode=0o644)

    assert path.read_text(encoding="utf-8") == "first\n"


def test_atomic_copy_overwrites_document_but_never_attachment(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"new")
    document = tmp_path / "index.md"
    document.write_bytes(b"old")
    attachment = tmp_path / "image.png"

    assert atomic_copy(source, document, overwrite=True) is True
    assert document.read_bytes() == b"new"
    assert atomic_copy(source, attachment, overwrite=False) is True
    with pytest.raises(FileExistsError):
        atomic_copy(source, attachment, overwrite=False)
    assert attachment.read_bytes() == b"new"


def test_storage_rejects_symlink_target_and_parent(tmp_path: Path) -> None:
    real = tmp_path / "real.md"
    real.write_text("real", encoding="utf-8")
    target = tmp_path / "target.md"
    os.symlink(real, target)

    with pytest.raises(ResearchMapError, match="symlink"):
        atomic_write_text_if_changed(target, "replacement")

    real_dir = tmp_path / "real-dir"
    real_dir.mkdir()
    link_dir = tmp_path / "link-dir"
    os.symlink(real_dir, link_dir, target_is_directory=True)
    with pytest.raises(ResearchMapError, match="symlink"):
        publish_exclusive_text(link_dir / "node.md", "body", mode=0o644)
