from __future__ import annotations

import os
import subprocess
from pathlib import Path

import demon_lucy.modules.linker as linker_mod
import pytest
from demon_lucy.modules.linker import root as linker_root
from watchdog.events import FileModifiedEvent, FileMovedEvent

from demon_lucy.lib.args.models import ParsedArgs
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.lib.operating_system import OperatingSystem
from demon_lucy.modules.abstract_module import Context, System
from demon_lucy.modules.linker import Linker
from demon_lucy.modules.linker.markdown import rewrite_inline_links_for_moved_target
from tests.args_support import result_changes


def _setup_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return repo


def _setup_real_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return repo


def _git_add(repo: Path, *paths: Path) -> None:
    subprocess.run(
        ["git", "add", "--", *[str(path.relative_to(repo)) for path in paths]],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _linker_args(
    *,
    root: bool = False,
    auto_cleanup: bool = False,
    auto_update: bool = True,
    ignore: list[str] | None = None,
) -> ParsedArgs:
    tokens: list[str] = []
    if root:
        tokens.append("--linker-root")
    if auto_cleanup:
        tokens.append("--linker-auto-clean-root-links")
    if auto_update:
        tokens.append("--linker-auto-update-md-links")
    if ignore:
        tokens.extend(["--linker-ignore", *ignore])
    return parse_args(tokens, Linker.template)


def _context(
    path: Path,
    *,
    root: bool = False,
    auto_cleanup: bool = False,
    auto_update: bool = True,
    ignore: list[str] | None = None,
    event: FileModifiedEvent | FileMovedEvent | None = None,
    event_id: str = "test",
) -> Context:
    return Context(
        path=str(path),
        args=_linker_args(
            root=root,
            auto_cleanup=auto_cleanup,
            auto_update=auto_update,
            ignore=ignore,
        ),
        run_mode="oneshot",
        event_id=event_id,
        event=event,
    )


def _system(
    module: Linker,
    operating_system: OperatingSystem = OperatingSystem.LINUX,
) -> System:
    return System(
        global_template=Linker.template,
        modules=[module],
        operating_system=operating_system,
    )


def _apply(
    module: Linker,
    *,
    path: Path,
    operating_system: OperatingSystem = OperatingSystem.LINUX,
    root: bool = False,
    auto_cleanup: bool = False,
    ignore: list[str] | None = None,
    event_id: str = "test",
):
    result = module.created(
        _context(
            path,
            root=root,
            auto_cleanup=auto_cleanup,
            auto_update=False,
            ignore=ignore,
            event_id=event_id,
        ),
        _system(module, operating_system),
    )
    return result_changes(result)


def test_apply_creates_link_in_repo_root(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    nested = repo / "notes" / "work"
    nested.mkdir(parents=True)
    note = nested / "daily.md"
    note.write_text("hello\n--linker-root\n", encoding="utf-8")

    module = Linker()
    changed = _apply(
        module,
        path=note,
        root=True,
    )

    link_path = repo / "daily.md"
    assert changed == {str(link_path.absolute()): 1}
    assert link_path.is_symlink()
    assert os.path.realpath(link_path) == str(note.resolve())


def test_windows_uses_hardlink_when_symlink_privilege_is_unavailable(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    repo = _setup_repo(tmp_path)
    note = repo / "notes" / "daily.md"
    note.parent.mkdir(parents=True)
    note.write_text("hello\n--linker-root\n", encoding="utf-8")

    privilege_error = OSError("symbolic link privilege is unavailable")
    privilege_error.winerror = 1314

    def reject_symlink(_target: str, _link_path: str) -> None:
        raise privilege_error

    monkeypatch.setattr(linker_root.os, "symlink", reject_symlink)

    module = Linker()
    changed = _apply(
        module,
        path=note,
        operating_system=OperatingSystem.WINDOWS,
        event_id="evt-test",
        root=True,
    )

    link_path = repo / "daily.md"
    assert changed == {str(link_path.absolute()): 1}
    assert link_path.is_symlink() is False
    assert os.path.samefile(link_path, note)
    assert "linker.root_symlink_unavailable" in caplog.text
    assert "Windows Developer Mode is disabled" in caplog.text
    assert "fallback=hardlink" in caplog.text


def test_windows_does_not_use_hardlink_for_other_symlink_errors(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    repo = _setup_repo(tmp_path)
    note = repo / "notes" / "daily.md"
    note.parent.mkdir(parents=True)
    note.write_text("hello\n--linker-root\n", encoding="utf-8")
    hardlink_called = False

    def reject_symlink(_target: str, _link_path: str) -> None:
        raise OSError("unexpected symlink error")

    def track_hardlink(_source: str, _link_path: str) -> None:
        nonlocal hardlink_called
        hardlink_called = True

    monkeypatch.setattr(linker_root.os, "symlink", reject_symlink)
    monkeypatch.setattr(linker_root.os, "link", track_hardlink)

    changed = _apply(
        Linker(),
        path=note,
        operating_system=OperatingSystem.WINDOWS,
        event_id="evt-test",
        root=True,
    )

    assert changed is None
    assert hardlink_called is False
    assert "reason=symlink_failed" in caplog.text


def test_apply_returns_none_when_flag_is_disabled(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    note = repo / "notes" / "x.md"
    note.parent.mkdir(parents=True)
    note.write_text("x\n", encoding="utf-8")

    module = Linker()
    changed = _apply(
        module,
        path=note,
    )

    assert changed is None
    assert not (repo / "x.md").exists()


def test_apply_returns_none_when_file_is_already_in_repo_root(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    note = repo / "root.md"
    note.write_text("x\n", encoding="utf-8")

    module = Linker()
    changed = _apply(
        module,
        path=note,
        root=True,
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
    changed = _apply(
        module,
        path=note,
        root=True,
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
    changed = _apply(
        module,
        path=note,
        root=True,
    )

    assert changed is None
    assert top_target.is_symlink()


def test_apply_returns_none_outside_repo(tmp_path: Path, monkeypatch):
    note = tmp_path / "loose.md"
    note.write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(linker_mod, "find_parent_with", lambda _p, _m: None)

    module = Linker()
    changed = _apply(
        module,
        path=note,
        root=True,
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
    changed = _apply(
        module,
        path=note,
        auto_cleanup=True,
    )

    assert changed is not None
    assert set(changed.keys()) == {str(link_a.absolute()), str(link_b.absolute())}
    assert all(value == 1 for value in changed.values())
    assert not link_a.exists()
    assert not link_b.exists()
    assert keep_file.exists()


def test_windows_auto_cleanup_removes_current_source_hardlink(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    note = repo / "notes" / "daily.md"
    note.parent.mkdir(parents=True)
    note.write_text("hello\n", encoding="utf-8")
    hardlink_path = repo / "daily.md"
    os.link(note, hardlink_path)

    changed = _apply(
        Linker(),
        path=note,
        operating_system=OperatingSystem.WINDOWS,
        auto_cleanup=True,
    )

    assert changed == {str(hardlink_path.absolute()): 1}
    assert hardlink_path.exists() is False
    assert note.read_text(encoding="utf-8") == "hello\n"


def test_linux_auto_cleanup_does_not_remove_hardlinks(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    note = repo / "notes" / "daily.md"
    note.parent.mkdir(parents=True)
    note.write_text("hello\n", encoding="utf-8")
    hardlink_path = repo / "daily.md"
    os.link(note, hardlink_path)

    changed = _apply(
        Linker(),
        path=note,
        auto_cleanup=True,
    )

    assert changed is None
    assert hardlink_path.exists()


def test_auto_cleanup_skipped_when_link_top_is_set(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    notes_dir = repo / "notes"
    notes_dir.mkdir(parents=True)
    note = notes_dir / "x.md"
    note.write_text("x\n", encoding="utf-8")
    stale_link = repo / "stale.md"
    os.symlink(os.path.relpath(note, repo), stale_link)

    module = Linker()
    changed = _apply(
        module,
        path=note,
        root=True,
        auto_cleanup=True,
    )

    assert changed == {str((repo / "x.md").absolute()): 1}
    assert stale_link.is_symlink()


def test_auto_cleanup_returns_none_when_no_links(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    note = repo / "notes" / "x.md"
    note.parent.mkdir(parents=True)
    note.write_text("x\n", encoding="utf-8")

    module = Linker()
    changed = _apply(
        module,
        path=note,
        auto_cleanup=True,
    )

    assert changed is None


def test_apply_skips_link_creation_when_source_matches_ignore_basename(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    note = repo / "notes" / "secret.md"
    note.parent.mkdir(parents=True)
    note.write_text("x\n", encoding="utf-8")

    module = Linker()
    changed = _apply(
        module,
        path=note,
        root=True,
        ignore=["secret.md"],
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
    changed = _apply(
        module,
        path=note,
        auto_cleanup=True,
        ignore=["x.md"],
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
    changed = _apply(
        module,
        path=delete_note,
        auto_cleanup=True,
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
    ctx = _context(
        new_path,
        event=FileMovedEvent(str(old_path), str(new_path)),
    )
    system = _system(module)

    changed = module.moved(ctx, system)

    assert result_changes(changed) == {str(index_path.resolve()): 1}
    assert (
        index_path.read_text(encoding="utf-8") == "[good day](log/day.md)\n"
        '[with title](./log/day.md#top "T")\n'
        "[external](https://example.com/day.md)\n"
    )


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_moved_link_rewrite_preserves_line_endings(
    tmp_path: Path,
    newline: bytes,
):
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    markdown_path = notes_dir / "index.md"
    markdown_path.write_bytes(newline.join((b"[good day](day.md)", b"text", b"")))

    changed = rewrite_inline_links_for_moved_target(
        markdown_path=str(markdown_path),
        moved_from_path=str(notes_dir / "day.md"),
        moved_to_path=str(notes_dir / "log" / "day.md"),
    )

    assert changed is True
    assert markdown_path.read_bytes() == newline.join(
        (b"[good day](log/day.md)", b"text", b"")
    )


def test_modified_markdown_link_move_renames_target_file_and_creates_dir(
    tmp_path: Path,
):
    repo = _setup_real_git_repo(tmp_path)
    notes_dir = repo / "notes"
    notes_dir.mkdir()
    target = notes_dir / "day.md"
    target.write_text("note\n", encoding="utf-8")
    index_path = notes_dir / "index.md"
    index_path.write_text("[good day](day.md)\n", encoding="utf-8")
    related_path = notes_dir / "related.md"
    related_path.write_text("[same day](day.md)\n", encoding="utf-8")
    _git_add(repo, target, index_path, related_path)

    index_path.write_text("[good day](log/day.md)\n", encoding="utf-8")

    module = Linker()
    ctx = _context(
        index_path,
        event=FileModifiedEvent(str(index_path)),
    )
    system = _system(module)

    changed = module.modified(ctx, system)

    moved_target = notes_dir / "log" / "day.md"
    assert result_changes(changed) == {
        str(target.resolve()): 1,
        str(moved_target.resolve()): 1,
        str(related_path.resolve()): 1,
    }
    assert not target.exists()
    assert moved_target.read_text(encoding="utf-8") == "note\n"
    assert index_path.read_text(encoding="utf-8") == "[good day](log/day.md)\n"
    assert related_path.read_text(encoding="utf-8") == "[same day](log/day.md)\n"


def test_modified_markdown_link_move_skips_when_target_exists(tmp_path: Path):
    repo = _setup_real_git_repo(tmp_path)
    notes_dir = repo / "notes"
    notes_dir.mkdir()
    target = notes_dir / "day.md"
    target.write_text("note\n", encoding="utf-8")
    existing_target = notes_dir / "log" / "day.md"
    existing_target.parent.mkdir()
    existing_target.write_text("existing\n", encoding="utf-8")
    index_path = notes_dir / "index.md"
    index_path.write_text("[good day](day.md)\n", encoding="utf-8")
    _git_add(repo, target, existing_target, index_path)

    index_path.write_text("[good day](log/day.md)\n", encoding="utf-8")

    module = Linker()
    ctx = _context(
        index_path,
        event=FileModifiedEvent(str(index_path)),
    )
    system = _system(module)

    changed = module.modified(ctx, system)

    assert changed is None
    assert target.read_text(encoding="utf-8") == "note\n"
    assert existing_target.read_text(encoding="utf-8") == "existing\n"


def test_modified_markdown_link_move_skips_when_flag_disabled(tmp_path: Path):
    repo = _setup_real_git_repo(tmp_path)
    notes_dir = repo / "notes"
    notes_dir.mkdir()
    target = notes_dir / "day.md"
    target.write_text("note\n", encoding="utf-8")
    index_path = notes_dir / "index.md"
    index_path.write_text("[good day](day.md)\n", encoding="utf-8")
    _git_add(repo, target, index_path)

    index_path.write_text("[good day](log/day.md)\n", encoding="utf-8")

    module = Linker()
    ctx = _context(
        index_path,
        auto_update=False,
        event=FileModifiedEvent(str(index_path)),
    )
    system = _system(module)

    changed = module.modified(ctx, system)

    assert changed is None
    assert target.exists()
    assert not (notes_dir / "log" / "day.md").exists()


def test_modified_markdown_anchor_change_does_not_move_target(tmp_path: Path):
    repo = _setup_real_git_repo(tmp_path)
    notes_dir = repo / "notes"
    notes_dir.mkdir()
    target = notes_dir / "day.md"
    target.write_text("note\n", encoding="utf-8")
    index_path = notes_dir / "index.md"
    index_path.write_text("[good day](day.md#old)\n", encoding="utf-8")
    _git_add(repo, target, index_path)

    index_path.write_text("[good day](day.md#new)\n", encoding="utf-8")

    module = Linker()
    ctx = _context(
        index_path,
        event=FileModifiedEvent(str(index_path)),
    )
    system = _system(module)

    changed = module.modified(ctx, system)

    assert changed is None
    assert target.exists()


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
    ctx = _context(
        new_path,
        ignore=["index.md"],
        event=FileMovedEvent(str(old_path), str(new_path)),
    )
    system = _system(module)

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
    ctx = _context(
        new_path,
        event=FileMovedEvent(str(old_path), str(new_path)),
    )
    system = _system(module)

    changed = module.moved(ctx, system)

    assert result_changes(changed) == {str(index_path.resolve()): 1}
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
    ctx = _context(
        new_path,
        auto_update=False,
        event=FileMovedEvent(str(old_path), str(new_path)),
    )
    system = _system(module)

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
    ctx = _context(
        new_path,
        event=FileMovedEvent(str(old_path), str(new_path)),
    )
    system = _system(module)

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
    ctx = _context(
        new_path,
        event=FileMovedEvent(str(old_path), str(new_path)),
    )
    system = _system(module)

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
    ctx = _context(
        new_path,
        event=FileMovedEvent(str(old_path), str(new_path)),
    )
    system = _system(module)

    changed = module.moved(ctx, system)

    assert changed is None
    assert index_path.read_text(encoding="utf-8") == "[good day](day.log)\n"
