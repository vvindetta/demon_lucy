from __future__ import annotations

import os
import subprocess
from pathlib import Path

from watchdog.events import FileModifiedEvent

from demon_lucy.modules.abstract_module import Context, System
from demon_lucy.modules.graph import Graph


def _ctx_for(path: Path) -> Context:
    return Context(
        path=str(path),
        config={
            "graph": [],
            "graph_regex": [],
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


def _run_graph(note: Path, *, graph_line: int = 1) -> dict[str, int] | None:
    module = Graph()
    ctx = _ctx_for(note)
    ctx.arg_lines["graph"] = [graph_line]
    return module.modified(ctx, _system(module, note))


def _run_graph_regex(note: Path, *, graph_line: int = 1) -> dict[str, int] | None:
    module = Graph()
    ctx = _ctx_for(note)
    ctx.arg_lines["graph_regex"] = [graph_line]
    return module.modified(ctx, _system(module, note))


def test_graph_literal_week_from_date_sections(tmp_path: Path) -> None:
    archive = tmp_path / "past.md"
    archive.write_text(
        "--- 01.07.2026 ---\n"
        "сон\n"
        "--- 02.07.2026 ---\n"
        "ничего\n"
        "--- 03.07.2026 ---\n"
        "сон сон\n"
        "--- 04.07.2026 ---\n"
        "день\n"
        "--- 05.07.2026 ---\n"
        "сон сон\nсон сон\n"
        "--- 06.07.2026 ---\n"
        "сон\n"
        "--- 07.07.2026 ---\n"
        "сон сон сон\n",
        encoding="utf-8",
    )
    note = tmp_path / "graph.md"
    note.write_text("--graph past.md сон week\n", encoding="utf-8")

    changed = _run_graph(note)

    assert changed == {str(note): 1}
    assert note.read_text(encoding="utf-8") == (
        "--- graph: сон / week ---\n"
        "file: past.md\n"
        "range: 2026-07-01..2026-07-07\n"
        "\n"
        "time        count  graph\n"
        "2026-07-01      1  ######\n"
        "2026-07-02      0  |\n"
        "2026-07-03      2  ############\n"
        "2026-07-04      0  |\n"
        "2026-07-05      4  ########################\n"
        "2026-07-06      1  ######\n"
        "2026-07-07      3  ##################\n"
    )


def test_graph_date_sections_allow_suffix_comments(tmp_path: Path) -> None:
    archive = tmp_path / "past.md"
    archive.write_text(
        "--- 01.01.2026 imported from archive\n"
        "sleep\n"
        "--- 02.01.2026 --- copied section\n"
        "sleep sleep\n"
        "--- 03.01.2026\n"
        "awake\n",
        encoding="utf-8",
    )
    note = tmp_path / "graph.md"
    note.write_text("--graph past.md sleep all\n", encoding="utf-8")

    changed = _run_graph(note)

    assert changed == {str(note): 1}
    assert note.read_text(encoding="utf-8") == (
        "--- graph: sleep / all ---\n"
        "file: past.md\n"
        "range: 2026-01-01..2026-01-03\n"
        "\n"
        "time        count  graph\n"
        "2026-01-01      1  ############\n"
        "2026-01-02      2  ########################\n"
        "2026-01-03      0  |\n"
    )


def test_graph_date_sections_allow_ranges(tmp_path: Path) -> None:
    archive = tmp_path / "past.md"
    archive.write_text(
        "--- 2.01.2026 - 10.01.2026\n"
        "sleep sleep\n"
        "--- 12.01.2026 ... 14.01.2026 copied from old archive\n"
        "sleep\n",
        encoding="utf-8",
    )
    note = tmp_path / "graph.md"
    note.write_text("--graph past.md sleep all\n", encoding="utf-8")

    changed = _run_graph(note)

    assert changed == {str(note): 1}
    text = note.read_text(encoding="utf-8")
    assert "range: 2026-01-02..2026-01-14\n" in text
    assert "2026-01-02      0  |\n" in text
    assert "2026-01-10      2  ########################\n" in text
    assert "2026-01-11      0  |\n" in text
    assert "2026-01-14      1  ############\n" in text


def test_graph_regex_year_groups_by_month(tmp_path: Path) -> None:
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

    changed = _run_graph_regex(note)

    assert changed == {str(note): 1}
    text = note.read_text(encoding="utf-8")
    assert "--- graph: #work / year ---\n" in text
    assert "range: 2026-01..2026-12\n" in text
    assert "2026-01      2  ################\n" in text
    assert "2026-02      1  ########\n" in text
    assert "2026-03      0  |\n" in text
    assert "2026-04      3  ########################\n" in text
    assert "2026-12      0  |\n" in text


def test_graph_invalid_regex_writes_error_block(tmp_path: Path) -> None:
    data = tmp_path / "past.md"
    data.write_text("--- 07.07.2026 ---\nсон\n", encoding="utf-8")
    note = tmp_path / "graph.md"
    note.write_text('--graph-regex past.md "[" week\n', encoding="utf-8")

    changed = _run_graph_regex(note)

    assert changed == {str(note): 1}
    assert note.read_text(encoding="utf-8").startswith(
        "--- graph error ---\n"
        "file: past.md\n"
        "reason: invalid_regex\n"
        "detail: "
    )


def test_graph_without_date_sections_errors_when_git_unavailable(
    tmp_path: Path,
) -> None:
    data = tmp_path / "past.md"
    data.write_text("сон\n", encoding="utf-8")
    note = tmp_path / "graph.md"
    note.write_text("--graph past.md сон\n", encoding="utf-8")

    changed = _run_graph(note)

    assert changed == {str(note): 1}
    assert note.read_text(encoding="utf-8") == (
        "--- graph error ---\n"
        "file: past.md\n"
        "reason: git_history_unavailable\n"
        "detail: not inside a git repository\n"
    )


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

    commit("сон\n", "first", "2026-01-01T12:00:00+0000")
    commit("сон\nспал\nсон\n", "second", "2026-01-03T12:00:00+0000")

    note = repo / "graph.md"
    note.write_text('--graph-regex past.md "сон|спал" all\n', encoding="utf-8")

    changed = _run_graph_regex(note)

    assert changed == {str(note): 1}
    assert note.read_text(encoding="utf-8") == (
        "--- graph: сон|спал / all ---\n"
        "file: past.md\n"
        "range: 2026-01-01..2026-01-03\n"
        "\n"
        "time        count  graph\n"
        "2026-01-01      1  ############\n"
        "2026-01-02      0  |\n"
        "2026-01-03      2  ########################\n"
    )
