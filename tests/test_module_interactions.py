from __future__ import annotations

import os
from pathlib import Path

import pytest
from watchdog.events import FileCreatedEvent, FileModifiedEvent, FileMovedEvent

import demon_lucy.modules.banner as banner_mod
import demon_lucy.modules.plasma_widget as plasma_mod
from demon_lucy.lib.args.models import ArgSource, ParsedArgs
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.lib.notifications import NotificationProvider
from demon_lucy.module_manager import ModuleManager
from demon_lucy.modules.abstract_module import AbstractModule
from demon_lucy.modules.archive import Archive
from demon_lucy.modules.banner import Banner
from demon_lucy.modules.dropdir import DropDir
from demon_lucy.modules.formatter import Formatter
from demon_lucy.modules.linker import Linker
from demon_lucy.modules.plasma_widget import PlasmaWidget
from demon_lucy.modules.plasma_widget.markdown_codec import _doc_to_md
from demon_lucy.modules.plasma_widget.mirror_mapper import _mirror_html_to_items
from demon_lucy.modules.plasma_widget.plasma_html_codec import _html_to_doc
from demon_lucy.modules.renamer import Renamer
from demon_lucy.runtime import DEMON_LUCY_STARTUP_TEMPLATE


def _startup_args(
    root: Path,
    *,
    priorities: tuple[str, ...] = (),
    extra: tuple[str, ...] = (),
) -> ParsedArgs:
    tokens = [
        "--sys-config-path",
        str(root / ".lucy" / "config.txt"),
        "--sys-watch-paths",
        str(root),
        "--sys-notification-provider",
        NotificationProvider.DISABLE,
    ]
    if priorities:
        tokens.extend(["--sys-modules-priority", *priorities])
    tokens.extend(extra)
    return parse_args(
        args=tokens,
        template=DEMON_LUCY_STARTUP_TEMPLATE,
        source=ArgSource.CONFIG,
    )


def _manager(
    root: Path,
    modules: list[AbstractModule],
    *,
    priorities: tuple[str, ...] = (),
    extra: tuple[str, ...] = (),
) -> ModuleManager:
    return ModuleManager(
        modules=modules,
        startup_args=_startup_args(
            root,
            priorities=priorities,
            extra=extra,
        ),
    )


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    git_dir = repo / ".git"
    git_dir.mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return repo


@pytest.fixture(autouse=True)
def _reset_plasma_state(monkeypatch):
    monkeypatch.setattr(plasma_mod, "_STATE_BY_KEY", {})
    monkeypatch.setattr(plasma_mod, "_INIT_DONE_BY_KEY", {})


def _widget_markdown(widget_path: Path) -> str:
    return _doc_to_md(_html_to_doc(widget_path.read_text(encoding="utf-8")))


def test_renamer_formatter_linker_pipeline_on_created_note(tmp_path: Path):
    repo = _make_repo(tmp_path)
    note = repo / "notes" / "draft.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "--rename daily.md\n"
        "--formatter-todo\n"
        "--linker-root\n"
        "- first task\n"
        "- [x] done\n",
        encoding="utf-8",
    )

    manager = _manager(
        repo,
        [Linker(), Formatter(), Renamer()],
        priorities=("renamer=10", "formatter=20", "linker=30"),
    )

    ignore = manager.run(str(note), FileCreatedEvent(str(note)))

    renamed = repo / "notes" / "daily.md"
    link_path = repo / "daily.md"
    assert ignore == {
        str(note): 1,
        str(renamed): 2,
        str(link_path.absolute()): 1,
    }
    assert not note.exists()
    assert renamed.read_text(encoding="utf-8") == (
        "--rename daily.md\n--linker-root\n- [ ] first task\n- [x] done\n"
    )
    assert link_path.is_symlink()
    assert os.path.realpath(link_path) == str(renamed.resolve())


def test_banner_formatter_archive_pipeline_archives_complex_note(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(banner_mod.pyfiglet, "figlet_format", lambda _txt: "SPRINT\n")

    repo = _make_repo(tmp_path)
    note = repo / "daily.md"
    archive = repo / "past.md"
    note.write_text(
        "--archive-pair --banner Sprint --formatter-todo\n"
        "\n"
        "--- BACKLOG\n"
        "- call customer\n"
        "- [x] shipped\n"
        "\n"
        "**Important** line\n",
        encoding="utf-8",
    )

    manager = _manager(
        repo,
        [Archive(), Formatter(), Banner()],
        priorities=("banner=10", "formatter=20", "archive=30"),
        extra=(
            "--archive-auto-pair",
            str(note),
            str(archive),
            "12",
        ),
    )

    ignore = manager.run(str(note), FileModifiedEvent(str(note)))

    assert ignore == {str(note.resolve()): 3, str(archive.resolve()): 1}
    assert note.read_text(encoding="utf-8") == ""

    archived = archive.read_text(encoding="utf-8")
    assert "--formatter-todo" not in archived
    assert "---\nSPRINT\n" in archived
    assert "- [ ] call customer\n" in archived
    assert "- [x] shipped\n" in archived
    assert "**Important** line\n" in archived


def test_formatter_blank_command_is_not_archived(tmp_path: Path):
    repo = _make_repo(tmp_path)
    note = repo / "daily.md"
    archive = repo / "past.md"
    note.write_text(
        "--archive-pair --formatter-blank up 3 --formatter-todo\n- inbox task\n",
        encoding="utf-8",
    )

    manager = _manager(
        repo,
        [Archive(), Formatter()],
        priorities=("formatter=10", "archive=20"),
        extra=(
            "--archive-auto-pair",
            str(note),
            str(archive),
            "12",
        ),
    )

    ignore = manager.run(str(note), FileModifiedEvent(str(note)))

    assert ignore == {str(note.resolve()): 2, str(archive.resolve()): 1}
    assert note.read_text(encoding="utf-8") == ""
    archived_lines = archive.read_text(encoding="utf-8").splitlines()
    assert archived_lines[1:] == ["- [ ] inbox task"]
    assert all("--formatter-" not in line for line in archived_lines)


def test_formatter_dropdir_archive_pipeline_formats_before_clean_archive(
    tmp_path: Path,
):
    repo = _make_repo(tmp_path)
    source = repo / "-> now.md"
    archive = repo / "past.md"
    drop_dir = repo / "drop"
    drop_dir.mkdir()
    source.write_text("--formatter-todo\n- inbox task\n**Hot**\n", encoding="utf-8")

    dropped = drop_dir / source.name
    source.rename(dropped)

    manager = _manager(
        repo,
        [Archive(), DropDir(), Formatter()],
        priorities=("formatter=10", "dropdir=20", "archive=30"),
        extra=(
            "--archive-auto-pair",
            str(source),
            str(archive),
            "12",
            "--dropdir-action",
            "drop=--archive-pair",
        ),
    )

    ignore = manager.run(str(dropped), FileMovedEvent(str(source), str(dropped)))

    assert ignore == {
        str(dropped.resolve()): 2,
        str(source.resolve()): 2,
        str(archive.resolve()): 1,
    }
    assert not dropped.exists()
    assert source.read_text(encoding="utf-8") == ""
    archived = archive.read_text(encoding="utf-8")
    assert "--formatter-todo\n" not in archived
    assert "- [ ] inbox task\n" in archived
    assert "**Hot**\n" in archived


def test_banner_formatter_linker_plasma_complex_note_stays_consistent(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(banner_mod.pyfiglet, "figlet_format", lambda _txt: "PLAN\n")

    repo = _make_repo(tmp_path)
    note = repo / "notes" / "daily.md"
    note.parent.mkdir(parents=True)
    widget = repo / "widget.html"
    mirror = repo / "mirror.html"
    note.write_text(
        "--banner Plan --formatter-todo --linker-root\n"
        "- raw task\n"
        "- [x] closed\n"
        "**Important** note\n",
        encoding="utf-8",
    )

    manager = _manager(
        repo,
        [PlasmaWidget(), Linker(), Formatter(), Banner()],
        priorities=(
            "banner=10",
            "formatter=20",
            "linker=30",
            "plasma_widget=40",
        ),
        extra=(
            "--plasma-markdown-note-path",
            str(note),
            "--plasma-widget-path",
            str(widget),
            "--plasma-bold-widget-path",
            str(mirror),
        ),
    )

    manager.run(str(note), FileModifiedEvent(str(note)))

    assert (repo / "daily.md").is_symlink()
    assert "- [ ] raw task" in note.read_text(encoding="utf-8")
    assert "- [ ] raw task" in _widget_markdown(widget)
    assert _mirror_html_to_items(mirror.read_text(encoding="utf-8")) == ["Important"]


def test_linker_and_formatter_keep_the_original_event_path_between_modules(
    tmp_path: Path,
):
    repo = _make_repo(tmp_path)
    note = repo / "notes" / "daily.md"
    note.parent.mkdir(parents=True)
    note.write_text("--linker-root\n--formatter-todo\n- task\n", encoding="utf-8")

    manager = _manager(repo, [Formatter(), Linker()])

    ignore = manager.run(str(note), FileModifiedEvent(str(note)))

    link_path = repo / "daily.md"
    assert ignore == {str(link_path.absolute()): 1, str(note.resolve()): 1}
    assert link_path.is_symlink()
    assert os.path.realpath(link_path) == str(note.resolve())
    assert note.read_text(encoding="utf-8") == "--linker-root\n- [ ] task\n"


def test_formatter_then_linker_priority_override_combines_side_effects(
    tmp_path: Path,
):
    repo = _make_repo(tmp_path)
    note = repo / "notes" / "daily.md"
    note.parent.mkdir(parents=True)
    note.write_text("--linker-root\n--formatter-todo\n- task\n", encoding="utf-8")

    manager = _manager(
        repo,
        [Formatter(), Linker()],
        priorities=("formatter=10", "linker=20"),
    )

    ignore = manager.run(str(note), FileModifiedEvent(str(note)))

    link_path = repo / "daily.md"
    assert ignore == {str(note.resolve()): 1, str(link_path.absolute()): 1}
    assert link_path.is_symlink()
    assert os.path.realpath(link_path) == str(note.resolve())
    assert note.read_text(encoding="utf-8") == "--linker-root\n- [ ] task\n"


def test_sys_ignore_paths_blocks_real_modules_and_side_effects(tmp_path: Path):
    repo = _make_repo(tmp_path)
    ignored_dir = repo / "ignored"
    note = ignored_dir / "daily.md"
    note.parent.mkdir(parents=True)
    note.write_text("--linker-root\n--formatter-todo\n- task\n", encoding="utf-8")

    manager = _manager(
        repo,
        [Formatter(), Linker()],
        extra=("--sys-ignore-paths", str(ignored_dir)),
    )

    ignore = manager.run(str(note), FileModifiedEvent(str(note)))

    assert ignore is None
    assert (
        note.read_text(encoding="utf-8") == "--linker-root\n--formatter-todo\n- task\n"
    )
    assert not (repo / "daily.md").exists()


def test_dropdir_moves_file_back_and_archives_with_absolute_pair(tmp_path: Path):
    repo = _make_repo(tmp_path)
    source = repo / "-> now.md"
    archive = repo / "past.md"
    drop_dir = repo / "drop"
    drop_dir.mkdir()
    source.write_text("drop archive body\n", encoding="utf-8")

    dropped = drop_dir / source.name
    source.rename(dropped)

    manager = _manager(
        repo,
        [Archive(), DropDir()],
        extra=(
            "--archive-auto-pair",
            str(source),
            str(archive),
            "12",
            "--dropdir-action",
            "drop=--archive-pair",
        ),
    )

    ignore = manager.run(str(dropped), FileMovedEvent(str(source), str(dropped)))

    assert ignore == {
        str(dropped.resolve()): 1,
        str(source.resolve()): 2,
        str(archive.resolve()): 1,
    }
    assert not dropped.exists()
    assert source.read_text(encoding="utf-8") == ""
    assert "drop archive body\n" in archive.read_text(encoding="utf-8")


def test_default_formatter_plasma_order_keeps_widget_in_sync(tmp_path: Path):
    repo = _make_repo(tmp_path)
    note = repo / "todo.md"
    widget = repo / "widget.html"
    mirror = repo / "mirror.html"
    note.write_text("--formatter-todo\n- task\n**Bold**\n", encoding="utf-8")

    manager = _manager(
        repo,
        [PlasmaWidget(), Formatter()],
        extra=(
            "--plasma-markdown-note-path",
            str(note),
            "--plasma-widget-path",
            str(widget),
            "--plasma-bold-widget-path",
            str(mirror),
        ),
    )

    ignore = manager.run(str(note), FileModifiedEvent(str(note)))

    assert ignore is not None
    assert note.read_text(encoding="utf-8") == "- [ ] task\n**Bold**\n"
    assert "- [ ] task" in _widget_markdown(widget)
    assert _mirror_html_to_items(mirror.read_text(encoding="utf-8")) == ["Bold"]
