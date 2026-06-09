from __future__ import annotations

from pathlib import Path

import pytest

from demon_lucy.lib.path import (
    abs_expand_path,
    canonical_path,
    find_parent_with,
    find_parent_git_repo,
    git_dir_for_repo_root,
    path_has_component,
)


def test_abs_expand_path_and_canonical_path(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "note.md"
    target.parent.mkdir(parents=True)
    target.write_text("x\n", encoding="utf-8")

    odd = str(tmp_path / "a" / "b" / ".." / "b" / "note.md")
    assert abs_expand_path(odd).endswith("note.md")
    assert canonical_path(odd) == str(target.resolve())


@pytest.mark.parametrize(
    ("relative_path", "expected"),
    [
        (".git/config", True),
        ("notes.md", False),
    ],
)
def test_path_has_component_detects_git_dir(
    tmp_path: Path, relative_path: str, expected: bool
) -> None:
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x\n", encoding="utf-8")
    assert path_has_component(str(path), ".git") is expected


def test_find_parent_with_git_marker(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    nested = repo / "x" / "y" / "note.md"
    nested.parent.mkdir(parents=True)
    nested.write_text("x\n", encoding="utf-8")

    assert find_parent_with(str(nested), ".git") == str(repo.resolve())
    # assert find_parent_with(str(tmp_path / "outside.txt"), ".git") is None


def test_find_parent_git_repo_requires_valid_git_metadata(tmp_path: Path) -> None:
    root = tmp_path / "home"
    (root / ".git").mkdir(parents=True)
    nested = root / "Notes" / "note.md"
    nested.parent.mkdir(parents=True)
    nested.write_text("x\n", encoding="utf-8")

    assert git_dir_for_repo_root(str(root)) is None
    assert find_parent_git_repo(str(nested)) is None


def test_git_dir_for_repo_root_supports_gitdir_file(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    git_dir = tmp_path / "actual-git-dir"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (repo_root / ".git").write_text(
        f"gitdir: {git_dir}\n",
        encoding="utf-8",
    )

    assert git_dir_for_repo_root(str(repo_root)) == str(git_dir.resolve())
