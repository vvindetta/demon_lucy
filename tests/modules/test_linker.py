from __future__ import annotations

import os
from pathlib import Path

import demon_lucy.modules.linker as linker_mod
from watchdog.events import FileMovedEvent

from demon_lucy.modules.abstract_module import Context, System
from demon_lucy.modules.linker import Linker


def _setup_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return repo


def test_apply_creates_link_in_repo_root(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    nested = repo / "notes" / "work"
    nested.mkdir(parents=True)
    note = nested / "daily.md"
    note.write_text("hello\n--linker-root\n", encoding="utf-8")

    module = Linker()
    changed = module._apply(
        path=str(note),
        config={
            "linker_root": True,
            "linker_auto_clean_root_links": False,
            "linker_ignore": [],
        },
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
        config={
            "linker_root": False,
            "linker_auto_clean_root_links": False,
            "linker_ignore": [],
        },
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
        config={
            "linker_root": True,
            "linker_auto_clean_root_links": False,
            "linker_ignore": [],
        },
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
        config={
            "linker_root": True,
            "linker_auto_clean_root_links": False,
            "linker_ignore": [],
        },
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
        config={
            "linker_root": True,
            "linker_auto_clean_root_links": False,
            "linker_ignore": [],
        },
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
        config={
            "linker_root": True,
            "linker_auto_clean_root_links": False,
            "linker_ignore": [],
        },
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
        config={
            "linker_root": False,
            "linker_auto_clean_root_links": True,
            "linker_ignore": [],
        },
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
        config={
            "linker_root": True,
            "linker_auto_clean_root_links": True,
            "linker_ignore": [],
        },
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
        config={
            "linker_root": False,
            "linker_auto_clean_root_links": True,
            "linker_ignore": [],
        },
    )

    assert changed is None


def test_apply_skips_link_creation_when_source_matches_ignore_basename(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    note = repo / "notes" / "secret.md"
    note.parent.mkdir(parents=True)
    note.write_text("x\n", encoding="utf-8")

    module = Linker()
    changed = module._apply(
        path=str(note),
        config={
            "linker_root": True,
            "linker_auto_clean_root_links": False,
            "linker_ignore": ["secret.md"],
        },
    )

    assert changed is None
    assert not (repo / "secret.md").exists()


def test_auto_cleanup_keeps_ignored_symlink_by_name(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    notes_dir = repo / "notes"
    notes_dir.mkdir(parents=True)
    note = notes_dir / "x.md"
    note.write_text("x\n", encoding="utf-8")
    other = notes_dir / "y.md"
    other.write_text("y\n", encoding="utf-8")

    keep_link = repo / "x.md"
    delete_link = repo / "y.md"
    os.symlink(os.path.relpath(note, repo), keep_link)
    os.symlink(os.path.relpath(other, repo), delete_link)

    module = Linker()
    changed = module._apply(
        path=str(note),
        config={
            "linker_root": False,
            "linker_auto_clean_root_links": True,
            "linker_ignore": ["x.md"],
        },
    )

    assert changed == {str(delete_link.absolute()): 1}
    assert keep_link.is_symlink()
    assert not delete_link.exists()


def test_auto_cleanup_keeps_symlink_when_target_has_linker_root_flag(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    notes_dir = repo / "notes"
    notes_dir.mkdir(parents=True)
    keep_note = notes_dir / "x.md"
    keep_note.write_text("--linker-root\n", encoding="utf-8")
    delete_note = notes_dir / "y.md"
    delete_note.write_text("y\n", encoding="utf-8")

    keep_link = repo / "x.md"
    delete_link = repo / "y.md"
    os.symlink(os.path.relpath(keep_note, repo), keep_link)
    os.symlink(os.path.relpath(delete_note, repo), delete_link)

    module = Linker()
    changed = module._apply(
        path=str(delete_note),
        config={
            "linker_root": False,
            "linker_auto_clean_root_links": True,
            "linker_ignore": [],
        },
    )

    assert changed == {str(delete_link.absolute()): 1}
    assert keep_link.is_symlink()
    assert not delete_link.exists()


def test_moved_updates_markdown_link_paths_only(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    notes_dir = repo / "notes"
    moved_dir = notes_dir / "log"
    notes_dir.mkdir(parents=True)
    moved_dir.mkdir(parents=True)

    old_path = notes_dir / "day.md"
    new_path = moved_dir / "day.md"
    old_path.write_text("note\n", encoding="utf-8")
    os.rename(old_path, new_path)

    index_path = notes_dir / "index.md"
    index_path.write_text(
        "[good day](day.md)\n"
        '[with title](./day.md#top "T")\n'
        "[external](https://example.com/day.md)\n",
        encoding="utf-8",
    )

    module = Linker()
    config = {
        "linker_root": False,
        "linker_auto_clean_root_links": False,
        "linker_ignore": [],
        "linker_auto_update_md_links": True,
    }
    ctx = Context(path=str(new_path), config=config, arg_lines={})
    system = System(
        event=FileMovedEvent(str(old_path), str(new_path)),
        global_template=[],
        modules=[module],
    )

    changed = module.moved(ctx, system)

    assert changed == {str(index_path.resolve()): 1}
    assert (
        index_path.read_text(encoding="utf-8") == "[good day](log/day.md)\n"
        '[with title](./log/day.md#top "T")\n'
        "[external](https://example.com/day.md)\n"
    )


def test_moved_skips_markdown_rewrite_for_ignored_targets(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    notes_dir = repo / "notes"
    moved_dir = notes_dir / "log"
    notes_dir.mkdir(parents=True)
    moved_dir.mkdir(parents=True)

    old_path = notes_dir / "day.md"
    new_path = moved_dir / "day.md"
    old_path.write_text("note\n", encoding="utf-8")
    os.rename(old_path, new_path)

    index_path = notes_dir / "index.md"
    index_path.write_text("[good day](day.md)\n", encoding="utf-8")

    module = Linker()
    config = {
        "linker_root": False,
        "linker_auto_clean_root_links": False,
        "linker_ignore": ["index.md"],
        "linker_auto_update_md_links": True,
    }
    ctx = Context(path=str(new_path), config=config, arg_lines={})
    system = System(
        event=FileMovedEvent(str(old_path), str(new_path)),
        global_template=[],
        modules=[module],
    )

    changed = module.moved(ctx, system)

    assert changed is None
    assert index_path.read_text(encoding="utf-8") == "[good day](day.md)\n"


def test_moved_updates_link_in_middle_of_line(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    notes_dir = repo / "notes"
    moved_dir = notes_dir / "log"
    notes_dir.mkdir(parents=True)
    moved_dir.mkdir(parents=True)

    old_path = notes_dir / "day.md"
    new_path = moved_dir / "day.md"
    old_path.write_text("note\n", encoding="utf-8")
    os.rename(old_path, new_path)

    index_path = notes_dir / "index.md"
    index_path.write_text(
        "prefix text [good day](day.md) suffix text\n",
        encoding="utf-8",
    )

    module = Linker()
    config = {
        "linker_root": False,
        "linker_auto_clean_root_links": False,
        "linker_ignore": [],
        "linker_auto_update_md_links": True,
    }
    ctx = Context(path=str(new_path), config=config, arg_lines={})
    system = System(
        event=FileMovedEvent(str(old_path), str(new_path)),
        global_template=[],
        modules=[module],
    )

    changed = module.moved(ctx, system)

    assert changed == {str(index_path.resolve()): 1}
    assert (
        index_path.read_text(encoding="utf-8")
        == "prefix text [good day](log/day.md) suffix text\n"
    )


def test_moved_does_not_update_when_flag_is_disabled(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    notes_dir = repo / "notes"
    moved_dir = notes_dir / "log"
    notes_dir.mkdir(parents=True)
    moved_dir.mkdir(parents=True)

    old_path = notes_dir / "day.md"
    new_path = moved_dir / "day.md"
    old_path.write_text("note\n", encoding="utf-8")
    os.rename(old_path, new_path)

    index_path = notes_dir / "index.md"
    index_path.write_text("[good day](day.md)\n", encoding="utf-8")

    module = Linker()
    config = {
        "linker_root": False,
        "linker_auto_clean_root_links": False,
        "linker_ignore": [],
        "linker_auto_update_md_links": False,
    }
    ctx = Context(path=str(new_path), config=config, arg_lines={})
    system = System(
        event=FileMovedEvent(str(old_path), str(new_path)),
        global_template=[],
        modules=[module],
    )

    changed = module.moved(ctx, system)

    assert changed is None
    assert index_path.read_text(encoding="utf-8") == "[good day](day.md)\n"


def test_moved_skips_update_for_txt_target(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    notes_dir = repo / "notes"
    moved_dir = notes_dir / "log"
    notes_dir.mkdir(parents=True)
    moved_dir.mkdir(parents=True)

    old_path = notes_dir / "day.txt"
    new_path = moved_dir / "day.txt"
    old_path.write_text("note\n", encoding="utf-8")
    os.rename(old_path, new_path)

    index_path = notes_dir / "index.md"
    index_path.write_text("[good day](day.txt)\n", encoding="utf-8")

    module = Linker()
    config = {
        "linker_root": False,
        "linker_auto_clean_root_links": False,
        "linker_ignore": [],
        "linker_auto_update_md_links": True,
    }
    ctx = Context(path=str(new_path), config=config, arg_lines={})
    system = System(
        event=FileMovedEvent(str(old_path), str(new_path)),
        global_template=[],
        modules=[module],
    )

    changed = module.moved(ctx, system)

    assert changed is None
    assert index_path.read_text(encoding="utf-8") == "[good day](day.txt)\n"


def test_moved_skips_update_in_txt_source_file(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    notes_dir = repo / "notes"
    moved_dir = notes_dir / "log"
    notes_dir.mkdir(parents=True)
    moved_dir.mkdir(parents=True)

    old_path = notes_dir / "day.md"
    new_path = moved_dir / "day.md"
    old_path.write_text("note\n", encoding="utf-8")
    os.rename(old_path, new_path)

    index_path = notes_dir / "index.txt"
    index_path.write_text("[good day](day.md)\n", encoding="utf-8")

    module = Linker()
    config = {
        "linker_root": False,
        "linker_auto_clean_root_links": False,
        "linker_ignore": [],
        "linker_auto_update_md_links": True,
    }
    ctx = Context(path=str(new_path), config=config, arg_lines={})
    system = System(
        event=FileMovedEvent(str(old_path), str(new_path)),
        global_template=[],
        modules=[module],
    )

    changed = module.moved(ctx, system)

    assert changed is None
    assert index_path.read_text(encoding="utf-8") == "[good day](day.md)\n"


def test_moved_skips_update_for_unsupported_extension(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    notes_dir = repo / "notes"
    moved_dir = notes_dir / "log"
    notes_dir.mkdir(parents=True)
    moved_dir.mkdir(parents=True)

    old_path = notes_dir / "day.log"
    new_path = moved_dir / "day.log"
    old_path.write_text("note\n", encoding="utf-8")
    os.rename(old_path, new_path)

    index_path = notes_dir / "index.md"
    index_path.write_text("[good day](day.log)\n", encoding="utf-8")

    module = Linker()
    config = {
        "linker_root": False,
        "linker_auto_clean_root_links": False,
        "linker_ignore": [],
        "linker_auto_update_md_links": True,
    }
    ctx = Context(path=str(new_path), config=config, arg_lines={})
    system = System(
        event=FileMovedEvent(str(old_path), str(new_path)),
        global_template=[],
        modules=[module],
    )

    changed = module.moved(ctx, system)

    assert changed is None
    assert index_path.read_text(encoding="utf-8") == "[good day](day.log)\n"
