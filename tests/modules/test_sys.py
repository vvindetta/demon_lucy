from __future__ import annotations

from pathlib import Path

import pytest
from watchdog.events import FileModifiedEvent

from demon_lucy.lib.args.models import KnownArg, ParsedArgs
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.lib.args.sources import parse_note_args
from demon_lucy.lib.ascii_art import (
    LUCY_EYE_CLASSIC,
    LUCY_EYE_DOUBLE,
    LUCY_EYE_GLOW,
    LUCY_EYE_POINTED,
    LUCY_EYE_VERTICAL,
)
from demon_lucy.lib.operating_system import OperatingSystem
from demon_lucy.modules.abstract_module import Context, RunMode, System
from demon_lucy.modules.graph import Graph
from demon_lucy.modules.sys import Sys
from demon_lucy.modules.sys import neofetch as neofetch_module
from demon_lucy.runtime import DEMON_LUCY_STARTUP_TEMPLATE

_TEMPLATE = [*DEMON_LUCY_STARTUP_TEMPLATE, *Sys.template]
_BASE_TOKENS = [
    "--sys-notification-provider",
    "disable",
    "--sys-notification-min-interval-seconds",
    "0",
]


class _StatusLikeModule:
    name = "status"
    template = [
        KnownArg(name="status", value_type=str, default=[], description="status args"),
        KnownArg(
            name="status-banner",
            value_type=str,
            default="",
            description="status banner",
        ),
    ]


def _args_for(path: Path, extra_tokens: list[str] | None = None) -> ParsedArgs:
    args = parse_args(args=_BASE_TOKENS, template=_TEMPLATE).merged_with(
        parse_note_args(str(path), _TEMPLATE)
    )
    if not extra_tokens:
        return args
    return args.merged_with(
        parse_args(
            args=extra_tokens,
            template=_TEMPLATE,
            include_defaults=False,
        )
    )


def _context(
    path: Path,
    *,
    extra_tokens: list[str] | None = None,
    run_mode: RunMode = "daemon",
) -> Context:
    return Context(
        path=str(path),
        args=_args_for(path, extra_tokens),
        run_mode=run_mode,
        event_id="evt-test",
        event=FileModifiedEvent(str(path)),
    )


def test_lucy_eye_art_variants_are_available() -> None:
    assert LUCY_EYE_VERTICAL[1] == "       _..--'      |      '--.._"
    assert LUCY_EYE_POINTED[1] == "       _..--'      ^      '--.._"
    assert LUCY_EYE_DOUBLE == (
        "            ___.......___",
        "      _..--'      ||     '--.._",
        "  <--'            ||           '-->",
        "      '--..__     ||    __..--'",
        "              '-------'",
    )
    assert LUCY_EYE_GLOW[2] == "   <--'           (*)            '-->"
    assert LUCY_EYE_CLASSIC == (
        "          _______________",
        "     _..-'       |       '-.._",
        " <--'            |            '-->",
        "     '--.._      |      _..--'",
        "           '-----------'",
    )


def test_help_lists_neofetch_last() -> None:
    assert Sys._command_help_lines()[-1].startswith("* --neofetch:")


def test_neofetch_lines_show_lucy_runtime_information() -> None:
    lines = neofetch_module.neofetch_lines(
        run_mode="daemon",
        operating_system=OperatingSystem.LINUX,
        module_count=14,
        watch_path_count=2,
        opened_events_disabled=False,
        git_sync_age="3m ago",
    )

    assert lines == [
        "            ___.......___\n",
        "      _..--'      ||     '--.._\n",
        "  <--'            ||           '-->\n",
        "      '--..__     ||    __..--'\n",
        "              '-------'\n",
        "\n",
        "             Demon Lucy\n",
        "          Mode      daemon\n",
        "          Uptime    0m\n",
        "          Modules   14\n",
        "          Watch     2 paths\n",
        "          Opened    enabled\n",
        "          Git sync  3m ago\n",
    ]


def test_neofetch_git_sync_age_prefers_success_marker(monkeypatch) -> None:
    monkeypatch.setattr(
        neofetch_module,
        "find_parent_git_repo",
        lambda _path: "/repo",
    )
    monkeypatch.setattr(
        neofetch_module,
        "read_sync_success_timestamp",
        lambda _repo_root: 1000.0,
    )
    monkeypatch.setattr(
        neofetch_module,
        "git_last_commit_timestamp",
        lambda _repo_root: (_ for _ in ()).throw(
            AssertionError("commit fallback must not run when a marker exists")
        ),
    )

    assert (
        neofetch_module.git_sync_age_text(
            "/repo/note.md",
            now_timestamp=1180.0,
        )
        == "3m ago"
    )


def test_neofetch_git_sync_age_falls_back_to_last_commit(monkeypatch) -> None:
    monkeypatch.setattr(
        neofetch_module,
        "find_parent_git_repo",
        lambda _path: "/repo",
    )
    monkeypatch.setattr(
        neofetch_module,
        "read_sync_success_timestamp",
        lambda _repo_root: None,
    )
    monkeypatch.setattr(
        neofetch_module,
        "git_last_commit_timestamp",
        lambda _repo_root: 1000.0,
    )

    assert (
        neofetch_module.git_sync_age_text(
            "/repo/note.md",
            now_timestamp=11800.0,
        )
        == "3h ago"
    )


@pytest.mark.parametrize(
    ("run_mode", "disabled", "expected"),
    [
        ("daemon", False, "Opened    unavailable"),
        ("oneshot", False, "Opened    enabled"),
        ("daemon", True, "Opened    disabled"),
    ],
)
def test_neofetch_opened_event_state_on_windows(
    run_mode: RunMode,
    disabled: bool,
    expected: str,
) -> None:
    text = "".join(
        neofetch_module.neofetch_lines(
            run_mode=run_mode,
            operating_system=OperatingSystem.WINDOWS,
            module_count=1,
            watch_path_count=1,
            opened_events_disabled=disabled,
            git_sync_age="unavailable",
        )
    )

    assert expected in text


def test_neofetch_command_writes_runtime_block(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("--neofetch\n", encoding="utf-8")

    module = Sys()
    ctx = _context(
        note,
        extra_tokens=["--sys-watch-paths", "notes", "work"],
    )
    system = System(
        global_template=_TEMPLATE,
        modules=[module],
        operating_system=OperatingSystem.LINUX,
    )

    changed = module.modified(ctx, system)
    text = note.read_text(encoding="utf-8")

    assert changed == {str(note): 1}
    assert text.startswith("--- neofetch ---\n\n")
    assert "--neofetch" not in text
    assert LUCY_EYE_DOUBLE[0] in text
    assert "Mode      daemon" in text
    assert "Modules   1" in text
    assert "Watch     2 paths" in text
    assert "Git sync  unavailable" in text


def test_man_lines_specific_name_and_flag():
    module = Sys()
    system = System(
        global_template=[
            KnownArg(
                name="mods", value_type=bool, default=False, description="mods help"
            ),
            KnownArg(
                name="formatter-todo",
                value_type=bool,
                default=False,
                description="formatter todo help",
            ),
        ],
        modules=[],
    )

    flag_lines = module._man_one_lines(system, ["--mods"])
    one_lines = module._man_one_lines(system, ["formatter-todo"])

    assert any("--mods:" in line for line in flag_lines)
    assert any("--formatter-todo:" in line for line in one_lines)


def test_man_lines_module_name_expands_to_module_flags():
    module = Sys()
    system = System(
        global_template=[
            KnownArg(
                name="status", value_type=str, default=[], description="status args"
            ),
            KnownArg(
                name="status-banner",
                value_type=str,
                default="",
                description="status banner",
            ),
            KnownArg(
                name="mods", value_type=bool, default=False, description="mods help"
            ),
        ],
        modules=[_StatusLikeModule()],
    )

    lines = module._man_one_lines(system, ["status"])

    assert any("--status:" in line for line in lines)
    assert any("--status-banner:" in line for line in lines)
    assert all("--mods:" not in line for line in lines)


def test_man_lines_sys_keyword_expands_to_system_flags():
    module = Sys()
    system = System(
        global_template=[
            *DEMON_LUCY_STARTUP_TEMPLATE,
            KnownArg(
                name="mods", value_type=bool, default=False, description="mods help"
            ),
            KnownArg(
                name="sys-modules-priority",
                value_type=str,
                default=[],
                description="module priority help",
            ),
        ],
        modules=[module],
    )

    lines = module._man_one_lines(system, ["sys"])

    assert any("--sys-watch-paths:" in line for line in lines)
    assert any("--sys-log-level:" in line for line in lines)
    assert any("--sys-modules-priority:" in line for line in lines)
    assert all("--oneshot-event:" not in line for line in lines)
    assert all("--mods:" not in line for line in lines)


def test_man_lines_sys_uses_startup_template_defaults():
    module = Sys()
    system = System(
        global_template=DEMON_LUCY_STARTUP_TEMPLATE + Sys.template,
        modules=[module],
    )

    lines = module._man_one_lines(system, ["sys"])

    assert any(
        "--sys-notification-provider:" in line and "default=auto" in line
        for line in lines
    )
    assert any(
        "--sys-notification-min-interval-seconds:" in line and "default=10.0" in line
        for line in lines
    )
    assert any(
        "--sys-opened-event-cooldown-seconds:" in line and "default=60" in line
        for line in lines
    )


@pytest.mark.parametrize(
    ("first_line", "expected_lines"),
    [
        (
            "--mods --help\nbody\n",
            [
                "--- mods+help ---\n",
                "* --mods: print loaded modules and their priorities\n",
            ],
        ),
        (
            "--ping\n",
            ["++pong!\n"],
        ),
    ],
)
def test_apply_inserts_block_for_first_line_flags(
    tmp_path: Path,
    first_line: str,
    expected_lines: list[str],
):
    note = tmp_path / "note.md"
    note.write_text(first_line, encoding="utf-8")

    module = Sys()
    ctx = _context(note)
    system = System(
        global_template=_TEMPLATE,
        modules=[module],
    )

    changed = module.modified(ctx, system)
    content = note.read_text(encoding="utf-8")

    assert changed == {str(note): 1}
    for line in expected_lines:
        assert line in content


def test_apply_non_first_line_replacement_with_man(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("head\n--man man\n", encoding="utf-8")

    module = Sys()
    ctx = _context(note)
    system = System(
        global_template=[KnownArg(name="man", description="manual")],
        modules=[],
    )

    changed = module._apply(ctx=ctx, system=system)
    content = note.read_text(encoding="utf-8")

    assert changed == {str(note): 1}
    assert "--- man ---\n" in content
    assert "* --man: manual (type=str, default=None)\n" in content


def test_man_graph_description_is_direct() -> None:
    module = Sys()
    system = System(
        global_template=Graph.template,
        modules=[Graph()],
    )

    lines = module._man_one_lines(system, ["graph"])
    text = "".join(lines)

    assert "Replace this command line with" not in text
    assert (
        "* --graph: Build a text graph for a literal search in a file. "
        "Format: --graph file pattern [week|month|year|all]. "
        "Default period: year. (type=str, default=[])\n"
    ) in text
    assert (
        "* --graph-regex: Build a text graph for a regular expression search in a file. "
        "Format: --graph-regex file regex [week|month|year|all]. "
        "Default period: year. (type=str, default=[])\n"
    ) in text


def test_ping_sends_lucy_notification(tmp_path: Path, monkeypatch):
    note = tmp_path / "note.md"
    note.write_text("--ping\n", encoding="utf-8")
    notifications: list[dict[str, object]] = []

    def fake_safe_notify(name, message, **kwargs):
        notifications.append({"name": name, "message": message, **kwargs})
        return True

    monkeypatch.setattr("demon_lucy.modules.sys.safe_notify", fake_safe_notify)

    module = Sys()
    ctx = _context(note)
    system = System(
        global_template=_TEMPLATE,
        modules=[module],
    )

    changed = module.modified(ctx, system)

    assert changed == {str(note): 1}
    assert note.read_text(encoding="utf-8") == "++pong!\n"
    assert notifications == [
        {
            "name": "sys-ping",
            "message": "++pong!",
            "args": ctx.args,
            "title": "Demon Lucy ping",
            "use_rare_mode": False,
        }
    ]
