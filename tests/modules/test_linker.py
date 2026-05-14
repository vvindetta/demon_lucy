from __future__ import annotations

import os
from pathlib import Path

import lucy_notes_manager.modules.linker as linker_mod
from lucy_notes_manager.modules.linker import Linker


def _setup_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return repo


def test_apply_creates_link_in_repo_root(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    nested = repo / "notes" / "work"
    nested.mkdir(parents=True)
    note = nested / "daily.md"
    note.write_text("hello\n--linker-top\n", encoding="utf-8")

    module = Linker()
    changed = module._apply(
        path=str(note),
        config={"linker_top": True, "linker_auto_clean_up": False},
    )

    link_path = repo / "daily.md"
    assert changed == {str(link_path.absolute()): 1}
    assert link_path.is_symlink()
    assert os.path.realpath(link_path) == str(note.resolve())


def test_apply_returns_none_when_flag_is_disabled(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    note = repo / "notes" / "x.md"
    note.parent.mkdir(parents=True)
    note.write_text("x\n", encoding="utf-8")

    module = Linker()
    changed = module._apply(
        path=str(note),
        config={"linker_top": False, "linker_auto_clean_up": False},
    )

    assert changed is None
    assert not (repo / "x.md").exists()


def test_apply_returns_none_when_file_is_already_in_repo_root(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    note = repo / "root.md"
    note.write_text("x\n", encoding="utf-8")

    module = Linker()
    changed = module._apply(
        path=str(note),
        config={"linker_top": True, "linker_auto_clean_up": False},
    )

    assert changed is None
    assert not note.is_symlink()


def test_apply_returns_none_when_target_exists_as_file(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    note = repo / "notes" / "x.md"
    note.parent.mkdir(parents=True)
    note.write_text("x\n", encoding="utf-8")
    top_target = repo / "x.md"
    top_target.write_text("occupied\n", encoding="utf-8")

    module = Linker()
    changed = module._apply(
        path=str(note),
        config={"linker_top": True, "linker_auto_clean_up": False},
    )

    assert changed is None
    assert top_target.read_text(encoding="utf-8") == "occupied\n"


def test_apply_returns_none_when_same_symlink_already_exists(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    note = repo / "notes" / "x.md"
    note.parent.mkdir(parents=True)
    note.write_text("x\n", encoding="utf-8")

    top_target = repo / "x.md"
    os.symlink(os.path.relpath(note, repo), top_target)

    module = Linker()
    changed = module._apply(
        path=str(note),
        config={"linker_top": True, "linker_auto_clean_up": False},
    )

    assert changed is None
    assert top_target.is_symlink()


def test_apply_returns_none_outside_repo(tmp_path: Path, monkeypatch):
    note = tmp_path / "loose.md"
    note.write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(linker_mod, "find_parent_with", lambda _p, _m: None)

    module = Linker()
    changed = module._apply(
        path=str(note),
        config={"linker_top": True, "linker_auto_clean_up": False},
    )

    assert changed is None


def test_auto_cleanup_removes_symlinks_from_repo_root(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    notes_dir = repo / "notes"
    notes_dir.mkdir(parents=True)
    note = notes_dir / "x.md"
    note.write_text("x\n", encoding="utf-8")
    other = notes_dir / "y.md"
    other.write_text("y\n", encoding="utf-8")

    link_a = repo / "x.md"
    link_b = repo / "y.md"
    os.symlink(os.path.relpath(note, repo), link_a)
    os.symlink(os.path.relpath(other, repo), link_b)
    keep_file = repo / "README.md"
    keep_file.write_text("keep\n", encoding="utf-8")

    module = Linker()
    changed = module._apply(
        path=str(note),
        config={"linker_top": False, "linker_auto_clean_up": True},
    )

    assert changed is not None
    assert set(changed.keys()) == {str(link_a.absolute()), str(link_b.absolute())}
    assert all(value == 1 for value in changed.values())
    assert not link_a.exists()
    assert not link_b.exists()
    assert keep_file.exists()


def test_auto_cleanup_skipped_when_link_top_is_set(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    notes_dir = repo / "notes"
    notes_dir.mkdir(parents=True)
    note = notes_dir / "x.md"
    note.write_text("x\n", encoding="utf-8")
    stale_link = repo / "stale.md"
    os.symlink(os.path.relpath(note, repo), stale_link)

    module = Linker()
    changed = module._apply(
        path=str(note),
        config={"linker_top": True, "linker_auto_clean_up": True},
    )

    assert changed == {str((repo / "x.md").absolute()): 1}
    assert stale_link.is_symlink()


def test_auto_cleanup_returns_none_when_no_links(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    note = repo / "notes" / "x.md"
    note.parent.mkdir(parents=True)
    note.write_text("x\n", encoding="utf-8")

    module = Linker()
    changed = module._apply(
        path=str(note),
        config={"linker_top": False, "linker_auto_clean_up": True},
    )

    assert changed is None
