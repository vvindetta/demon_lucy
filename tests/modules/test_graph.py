from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from watchdog.events import FileModifiedEvent

from demon_lucy.lib.dynamic_blocks.parser import (
    format_dynamic_block,
    format_fenced_body,
    parse_dynamic_blocks,
)
from demon_lucy.module_manager import ModuleManager
from demon_lucy.modules.abstract_module import Context, System
from demon_lucy.modules.graph import Graph
from demon_lucy.modules.graph.params import (
    GraphPeriod,
    GraphView,
    graph_arg_template,
    graph_params_from_command,
    normalize_graph_params,
)


def _ctx_for(path: Path, *, hide_allowed_values: bool = False) -> Context:
    return Context(
        path=str(path),
        config={
            "graph": [],
            "graph_regex": [],
            "sys_dynamic_block_hide_allowed_values": hide_allowed_values,
        },
        arg_lines={},
    )


def _system(module: Graph, path: Path) -> System:
    return System(
        event=FileModifiedEvent(str(path)),
        global_template=Graph.template,
        modules=[module],
        event_id="evt-test",
    )


def _run_graph(
    note: Path,
    *,
    graph_lines: list[int] | None = None,
    regex_lines: list[int] | None = None,
    hide_allowed_values: bool = False,
) -> dict[str, int] | None:
    module = Graph()
    ctx = _ctx_for(note, hide_allowed_values=hide_allowed_values)
    if graph_lines:
        ctx.arg_lines["graph"] = graph_lines
    if regex_lines:
        ctx.arg_lines["graph_regex"] = regex_lines
    return module.modified(ctx, _system(module, note))


def _text_body(rows: list[str]) -> str:
    return "time        count  graph\n" + "".join(rows)


def _manager() -> ModuleManager:
    return ModuleManager(
        modules=[Graph()],
        args=[],
        system_config={
            "sys_notification_provider": "disable",
            "sys_notification_min_interval_seconds": 0.0,
            "sys_ignore_paths": [],
        },
    )


def test_graph_command_and_block_use_the_same_normalized_params() -> None:
    from_command = graph_params_from_command(
        "--graph",
        ["past.md", "deep", "sleep", "week"],
    )
    from_block = normalize_graph_params(
        "graph",
        {
            "source": "past.md",
            "pattern": "deep sleep",
            "period": "week",
        },
    )

    assert from_command == from_block
    assert from_command.view is GraphView.ASCII


def test_graph_command_and_block_default_to_year() -> None:
    from_command = graph_params_from_command(
        "--graph",
        ["past.md", "sleep"],
    )
    from_block = normalize_graph_params(
        "graph",
        {
            "source": "past.md",
            "pattern": "sleep",
        },
    )

    assert from_command.period is GraphPeriod.YEAR
    assert from_block.period is GraphPeriod.YEAR


def test_graph_literal_command_creates_dynamic_text_block(tmp_path: Path) -> None:
    archive = tmp_path / "past.md"
    archive.write_text(
        "--- 01.07.2026 ---\n"
        "sleep\n"
        "--- 02.07.2026 ---\n"
        "nothing\n"
        "--- 03.07.2026 ---\n"
        "sleep sleep\n"
        "--- 04.07.2026 ---\n"
        "day\n"
        "--- 05.07.2026 ---\n"
        "sleep sleep\nsleep sleep\n"
        "--- 06.07.2026 ---\n"
        "sleep\n"
        "--- 07.07.2026 ---\n"
        "sleep sleep sleep\n",
        encoding="utf-8",
    )
    note = tmp_path / "graph.md"
    note.write_text("--graph past.md sleep week\n", encoding="utf-8")

    changed = _run_graph(note, graph_lines=[1])

    assert changed == {str(note): 1}
    assert note.read_text(encoding="utf-8") == format_dynamic_block(
        arg="graph",
        params={
            "source": "past.md",
            "pattern": "sleep",
            "period": "week",
            "view": "ascii",
        },
        body=format_fenced_body(
            _text_body(
                [
                    "2026-07-01      1  [######]\n",
                    "2026-07-02      0  |\n",
                    "2026-07-03      2  [######][######]\n",
                    "2026-07-04      0  |\n",
                    "2026-07-05      4  [######][######][######][######]\n",
                    "2026-07-06      1  [######]\n",
                    "2026-07-07      3  [######][######][######]\n",
                ]
            ),
            info="text",
        ),
        arg_template=graph_arg_template("graph"),
    )


def test_graph_command_can_hide_parameter_allowed_values(tmp_path: Path) -> None:
    archive = tmp_path / "past.md"
    archive.write_text("--- 01.01.2026\nsleep\n", encoding="utf-8")
    note = tmp_path / "graph.md"
    note.write_text("--graph past.md sleep all\n", encoding="utf-8")

    changed = _run_graph(note, graph_lines=[1], hide_allowed_values=True)

    assert changed == {str(note): 1}
    text = note.read_text(encoding="utf-8")
    assert "- period: all\n" in text
    assert "- view: ascii\n" in text
    assert "[week|month|year|all]" not in text


def test_manager_initial_graph_command_writes_once(tmp_path: Path) -> None:
    archive = tmp_path / "past.md"
    archive.write_text("--- 01.01.2026\nsleep\n", encoding="utf-8")
    note = tmp_path / "graph.md"
    note.write_text("--graph past.md sleep all\n", encoding="utf-8")

    changed = _manager().run(
        str(note),
        FileModifiedEvent(str(note)),
        event_id="evt-test",
    )

    assert changed == {str(note.resolve()): 1}
    assert len(parse_dynamic_blocks(note.read_text(encoding="utf-8"))) == 1


def test_graph_date_sections_allow_comments_and_ranges(tmp_path: Path) -> None:
    archive = tmp_path / "past.md"
    archive.write_text(
        "--- 2.01.2026 - 10.01.2026 imported\n"
        "sleep sleep\n"
        "--- 12.01.2026 ... 14.01.2026 copied from archive\n"
        "sleep\n",
        encoding="utf-8",
    )
    note = tmp_path / "graph.md"
    note.write_text("--graph past.md sleep all\n", encoding="utf-8")

    changed = _run_graph(note, graph_lines=[1])

    assert changed == {str(note): 1}
    text = note.read_text(encoding="utf-8")
    assert "--- graph begin ---\n" in text
    assert "2026-01-02      0  |\n" in text
    assert (
        "2026-01-10      2  [######][######]\n"
    ) in text
    assert "2026-01-11      0  |\n" in text
    assert (
        "2026-01-14      1  [######]\n"
        in text
    )


def test_graph_regex_command_uses_arg_name_in_markers(tmp_path: Path) -> None:
    data = tmp_path / "tasks.md"
    data.write_text(
        "--- 01.01.2026 ---\n"
        "#work #work\n"
        "--- 10.02.2026 ---\n"
        "#home\n"
        "--- 11.02.2026 ---\n"
        "#work\n"
        "--- 01.04.2026 ---\n"
        "#work #work #work\n",
        encoding="utf-8",
    )
    note = tmp_path / "graph.md"
    note.write_text('--graph-regex tasks.md "#work" year\n', encoding="utf-8")

    changed = _run_graph(note, regex_lines=[1])

    assert changed == {str(note): 1}
    text = note.read_text(encoding="utf-8")
    assert text.startswith(
        "--- graph-regex begin ---\n"
        "- source: tasks.md\n"
        "- pattern: #work\n"
        "- period [week|month|year|all]: year\n"
        "- view [ascii|markdown|code]: ascii\n"
    )
    assert text.endswith("--- graph-regex end ---\n")
    assert "updated:" in text
    assert "less than a minute ago\n" in text
    assert (
        "2026-01      2  [######][######]\n"
        in text
    )
    assert (
        "2026-04      3  [######][######][######]\n"
    ) in text


@pytest.mark.parametrize(
    "command",
    [
        '--graph-regex past.md "[" week\n',
        "--graph past.md sleep\n",
    ],
)
def test_graph_failed_initial_render_keeps_command(
    tmp_path: Path,
    command: str,
) -> None:
    data = tmp_path / "past.md"
    data.write_text("text without date sections\n", encoding="utf-8")
    note = tmp_path / "graph.md"
    note.write_text(command, encoding="utf-8")

    changed = _run_graph(
        note,
        graph_lines=[1] if command.startswith("--graph ") else None,
        regex_lines=[1] if command.startswith("--graph-regex") else None,
    )

    assert changed is None
    assert note.read_text(encoding="utf-8") == command


def test_multiple_graph_commands_create_independent_blocks(tmp_path: Path) -> None:
    archive = tmp_path / "past.md"
    archive.write_text(
        "--- 01.01.2026\nsleep work work\n--- 02.01.2026\nsleep\n",
        encoding="utf-8",
    )
    note = tmp_path / "graphs.md"
    note.write_text(
        "--graph past.md sleep all\n--graph past.md work all\n",
        encoding="utf-8",
    )

    changed = _run_graph(note, graph_lines=[1, 2])

    assert changed == {str(note): 1}
    blocks = parse_dynamic_blocks(note.read_text(encoding="utf-8"))
    assert len(blocks) == 2
    assert [block.params["pattern"] for block in blocks] == ["sleep", "work"]
    assert "2026-01-01      1" in blocks[0].body
    assert "2026-01-01      2" in blocks[1].body


@pytest.mark.parametrize(
    ("view", "expected", "opening"),
    [
        ("ascii", "time        count  graph\n", "```text\n"),
        ("markdown", "| time | count | graph |\n", None),
        ("code", "| time | count | graph |\n", "```markdown\n"),
    ],
)
def test_existing_block_renders_by_selected_format(
    tmp_path: Path,
    view: str,
    expected: str,
    opening: str | None,
) -> None:
    archive = tmp_path / "past.md"
    archive.write_text("--- 01.01.2026\nsleep\n", encoding="utf-8")
    note = tmp_path / "graph.md"
    note.write_text(
        format_dynamic_block(
            arg="graph",
            params={
                "source": "past.md",
                "pattern": "sleep",
                "period": "all",
                "view": view,
            },
            body=format_fenced_body("old body", info="text"),
        ),
        encoding="utf-8",
    )

    changed = _manager().run(
        str(note),
        FileModifiedEvent(str(note)),
        event_id="evt-test",
    )

    assert changed == {str(note.resolve()): 1}
    text = note.read_text(encoding="utf-8")
    assert expected in text
    parsed = parse_dynamic_blocks(text)[0]
    assert parsed.params["view"] == view
    if opening is None:
        assert "```" not in parsed.body
    else:
        assert parsed.body.startswith(opening)


def test_existing_blocks_refresh_independently_and_preserve_failed_body(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "past.md"
    archive.write_text("--- 01.01.2026\nsleep work work\n", encoding="utf-8")
    good = format_dynamic_block(
        arg="graph",
        params={
            "source": "past.md",
            "pattern": "sleep",
            "period": "all",
            "view": "ascii",
        },
        body=format_fenced_body("old sleep", info="text"),
    )
    failed = format_dynamic_block(
        arg="graph",
        params={
            "source": "past.md",
            "pattern": "work",
            "period": "invalid",
            "view": "ascii",
        },
        body=format_fenced_body("last good work", info="text"),
    )
    note = tmp_path / "graphs.md"
    note.write_text(good + failed, encoding="utf-8")

    changed = _manager().run(
        str(note),
        FileModifiedEvent(str(note)),
        event_id="evt-test",
    )

    assert changed == {str(note.resolve()): 1}
    blocks = parse_dynamic_blocks(note.read_text(encoding="utf-8"))
    assert "2026-01-01      1" in blocks[0].body
    assert blocks[1].body == "```text\nlast good work\n```\n"


def test_unsupported_view_preserves_existing_body(tmp_path: Path) -> None:
    archive = tmp_path / "past.md"
    archive.write_text("--- 01.01.2026\nsleep\n", encoding="utf-8")
    original = format_dynamic_block(
        arg="graph",
        params={
            "source": "past.md",
            "pattern": "sleep",
            "period": "all",
            "view": "mermaid",
        },
        body=format_fenced_body("last good", info="text"),
    )
    note = tmp_path / "graph.md"
    note.write_text(original, encoding="utf-8")

    changed = _manager().run(
        str(note),
        FileModifiedEvent(str(note)),
        event_id="evt-test",
    )

    assert changed is None
    assert note.read_text(encoding="utf-8") == original


def test_graph_rejects_source_outside_target_root(tmp_path: Path) -> None:
    notes = tmp_path / "notes"
    notes.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("--- 01.01.2026\nsleep\n", encoding="utf-8")
    note = notes / "graph.md"
    command = "--graph ../outside.md sleep all\n"
    note.write_text(command, encoding="utf-8")

    changed = _run_graph(note, graph_lines=[1])

    assert changed is None
    assert note.read_text(encoding="utf-8") == command


def test_graph_falls_back_to_git_history_added_lines(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    data = repo / "past.md"

    def commit(text: str, message: str, timestamp: str) -> None:
        data.write_text(text, encoding="utf-8")
        subprocess.run(["git", "add", "past.md"], cwd=repo, check=True)
        env = {
            **os.environ,
            "GIT_AUTHOR_DATE": timestamp,
            "GIT_COMMITTER_DATE": timestamp,
        }
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Demon Lucy",
                "-c",
                "user.email=lucy@example.test",
                "commit",
                "-m",
                message,
            ],
            cwd=repo,
            env=env,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    commit("sleep\n", "first", "2026-01-01T12:00:00+0000")
    commit("sleep\nnap\nsleep\n", "second", "2026-01-03T12:00:00+0000")

    note = repo / "graph.md"
    note.write_text('--graph-regex past.md "sleep|nap" all\n', encoding="utf-8")

    changed = _run_graph(note, regex_lines=[1])

    assert changed == {str(note): 1}
    block = parse_dynamic_blocks(note.read_text(encoding="utf-8"))[0]
    assert block.arg == "graph-regex"
    assert (
        "2026-01-01      1  [######]\n"
        in block.body
    )
    assert "2026-01-02      0  |\n" in block.body
    assert (
        "2026-01-03      2  [######][######]\n"
    ) in block.body
