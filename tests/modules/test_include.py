from __future__ import annotations

from pathlib import Path

from watchdog.events import FileModifiedEvent

from demon_lucy.lib.dynamic_blocks.parser import (
    format_dynamic_block,
    parse_dynamic_blocks,
)
from demon_lucy.module_manager import ModuleManager
from demon_lucy.modules.include import Include


def _manager() -> ModuleManager:
    return ModuleManager(
        modules=[Include()],
        args=[],
        system_config={
            "sys_notification_provider": "disable",
            "sys_notification_min_interval_seconds": 0.0,
            "sys_ignore_paths": [],
        },
    )


def _run(manager: ModuleManager, note: Path) -> dict[str, int] | None:
    return manager.run(
        str(note),
        FileModifiedEvent(str(note)),
        event_id="evt-test",
    )


def test_include_command_renders_every_source_line_with_tab(tmp_path: Path) -> None:
    source = tmp_path / "shared file.md"
    source.write_text(
        "# Shared\n"
        "\n"
        "--- graph begin ---\n"
        "- source: archive.md\n"
        "\n"
        "generated\n"
        "\n"
        "--- graph end ---\n",
        encoding="utf-8",
    )
    note = tmp_path / "note.md"
    note.write_text('--include "shared file.md"\n', encoding="utf-8")

    changed = _run(_manager(), note)

    assert changed == {str(note.resolve()): 1}
    block = parse_dynamic_blocks(note.read_text(encoding="utf-8"))[0]
    assert block.arg == "include"
    assert block.params == {"source": "shared file.md"}
    assert block.body == (
        "\t# Shared\n"
        "\t\n"
        "\t--- graph begin ---\n"
        "\t- source: archive.md\n"
        "\t\n"
        "\tgenerated\n"
        "\t\n"
        "\t--- graph end ---\n"
    )
    assert all(
        line.startswith("\t")
        for line in block.body.splitlines(keepends=True)
    )


def test_include_dynamic_block_refreshes_complete_source(tmp_path: Path) -> None:
    source = tmp_path / "shared.md"
    source.write_text("first\n", encoding="utf-8")
    note = tmp_path / "note.md"
    note.write_text(
        format_dynamic_block(
            arg="include",
            params={"source": "shared.md"},
            body="\told",
        ),
        encoding="utf-8",
    )
    manager = _manager()

    first_changed = _run(manager, note)
    source.write_text("second\nline without final newline", encoding="utf-8")
    second_changed = _run(manager, note)

    assert first_changed == {str(note.resolve()): 1}
    assert second_changed == {str(note.resolve()): 1}
    block = parse_dynamic_blocks(note.read_text(encoding="utf-8"))[0]
    assert block.body == "\tsecond\n\tline without final newline\n"


def test_multiple_include_commands_create_independent_blocks(tmp_path: Path) -> None:
    (tmp_path / "one.md").write_text("one\n", encoding="utf-8")
    (tmp_path / "two.md").write_text("two\n", encoding="utf-8")
    note = tmp_path / "note.md"
    note.write_text(
        "--include one.md\n--include two.md\n",
        encoding="utf-8",
    )

    changed = _run(_manager(), note)

    assert changed == {str(note.resolve()): 1}
    blocks = parse_dynamic_blocks(note.read_text(encoding="utf-8"))
    assert [block.params["source"] for block in blocks] == ["one.md", "two.md"]
    assert [block.body for block in blocks] == ["\tone\n", "\ttwo\n"]


def test_include_rejects_target_as_its_own_source(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    original = "--include note.md\n"
    note.write_text(original, encoding="utf-8")

    changed = _run(_manager(), note)

    assert changed is None
    assert note.read_text(encoding="utf-8") == original


def test_include_rejects_source_outside_target_root(tmp_path: Path) -> None:
    notes = tmp_path / "notes"
    notes.mkdir()
    (tmp_path / "outside.md").write_text("outside\n", encoding="utf-8")
    note = notes / "note.md"
    original = "--include ../outside.md\n"
    note.write_text(original, encoding="utf-8")

    changed = _run(_manager(), note)

    assert changed is None
    assert note.read_text(encoding="utf-8") == original


def test_include_preserves_block_when_source_is_missing(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    original = format_dynamic_block(
        arg="include",
        params={"source": "missing.md"},
        body="\tlast available content",
    )
    note.write_text(original, encoding="utf-8")

    changed = _run(_manager(), note)

    assert changed is None
    assert note.read_text(encoding="utf-8") == original
