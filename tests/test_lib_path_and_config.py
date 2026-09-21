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
    path_is_inside,
    path_matches_selector,
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


def test_path_is_inside_matches_root_and_children(tmp_path: Path) -> None:
    root = tmp_path / "Notes"
    child = root / "daily" / "todo.md"
    child.parent.mkdir(parents=True)
    child.write_text("x\n", encoding="utf-8")

    assert path_is_inside(str(root), str(root)) is True
    assert path_is_inside(str(child), str(root)) is True


def test_path_is_inside_rejects_sibling_with_same_prefix(tmp_path: Path) -> None:
    root = tmp_path / "Notes"
    sibling = tmp_path / "Notes-old" / "todo.md"
    root.mkdir()
    sibling.parent.mkdir()
    sibling.write_text("x\n", encoding="utf-8")

    assert path_is_inside(str(sibling), str(root)) is False


@pytest.mark.parametrize(
    ("path", "selector", "expected"),
    [
        ("notes/.lucy/config.txt", ".lucy", True),
        ("notes/.lucy", ".lucy", True),
        ("notes/.lucy-backup/config.txt", ".lucy", False),
        ("notes/some.lucy/config.txt", ".lucy", False),
        ("notes/private/drafts/note.md", "private/drafts", True),
        ("notes/private/drafts-old/note.md", "private/drafts", False),
        ("notes/other/drafts/note.md", "private/drafts", False),
        ("notes/private/note.md", "private/", True),
        ("notes/private/note.md", "note.md", True),
        ("notes/note.md", "", False),
    ],
)
def test_path_matches_relative_selector(
    tmp_path: Path, path: str, selector: str, expected: bool
) -> None:
    assert path_matches_selector(str(tmp_path / path), selector) is expected


def test_path_matches_absolute_selector_and_symlink_target(tmp_path: Path) -> None:
    ignored_dir = tmp_path / ".lucy"
    ignored_dir.mkdir()
    source = ignored_dir / "config.txt"
    source.write_text("config\n", encoding="utf-8")
    alias = tmp_path / "alias"
    alias.symlink_to(ignored_dir, target_is_directory=True)

    assert path_matches_selector(str(source), str(ignored_dir))
    assert path_matches_selector(str(source), str(source))
    assert not path_matches_selector(str(source) + ".bak", str(source))
    assert not path_matches_selector(
        str(tmp_path / ".lucy-old/config.txt"), str(ignored_dir)
    )
    assert path_matches_selector(str(alias / "config.txt"), ".lucy")
    assert path_matches_selector(str(alias / "config.txt"), str(ignored_dir))


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
