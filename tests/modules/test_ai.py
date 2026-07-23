from __future__ import annotations

import json
import subprocess
from pathlib import Path

from watchdog.events import FileModifiedEvent

import demon_lucy.modules.ai as ai_module
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.modules.abstract_module import Context, System
from demon_lucy.modules.ai import Ai
from demon_lucy.modules.ai import runner as ai_runner
from demon_lucy.modules.ai.runner import CodexRunError


def _context(path: Path) -> Context:
    return Context(
        path=str(path),
        config={
            "ai": ["rewrite", "the", "heading"],
            "ai_timeout_seconds": 900,
        },
        arg_lines={"ai": [2, 2, 2]},
    )


def _system(path: Path, module: Ai) -> System:
    return System(
        event=FileModifiedEvent(str(path)),
        global_template=module.template,
        modules=[module],
        event_id="evt-ai",
    )


def test_ai_template_parses_prompt_and_timeout() -> None:
    config, unknown = parse_args(
        args=["--ai", "rewrite", "the heading", "--ai-timeout-seconds", "30"],
        template=Ai.template,
    )

    assert unknown == []
    assert config["ai"] == ["rewrite", "the heading"]
    assert config["ai_timeout_seconds"] == 30


def test_ai_edits_only_command_file_content(
    tmp_path: Path,
    monkeypatch,
) -> None:
    note = tmp_path / "note.md"
    note.write_text("title\n--ai rewrite the heading\nbody\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_run_codex(**kwargs) -> str:
        captured.update(kwargs)
        return str(kwargs["source_content"]).replace("title", "New title")

    monkeypatch.setattr(ai_module, "run_codex", fake_run_codex)
    module = Ai()

    changed = module.modified(_context(note), _system(note, module))

    assert changed == {str(note.resolve()): 1}
    assert captured == {
        "source_path": str(note.resolve()),
        "source_content": "title\n\nbody\n",
        "prompt": "rewrite the heading",
        "timeout_seconds": 900,
    }
    assert note.read_text(encoding="utf-8") == "New title\n\nbody\n"


def test_ai_preserves_source_newlines(tmp_path: Path, monkeypatch) -> None:
    note = tmp_path / "note.md"
    note.write_bytes(b"title\r\n--ai rewrite the heading\r\nbody\r\n")
    monkeypatch.setattr(
        ai_module,
        "run_codex",
        lambda **_kwargs: "New title\n\nbody\n",
    )
    module = Ai()

    changed = module.modified(_context(note), _system(note, module))

    assert changed == {str(note.resolve()): 1}
    assert note.read_bytes() == b"New title\r\n\r\nbody\r\n"


def test_ai_failure_keeps_command_and_source_unchanged(
    tmp_path: Path,
    monkeypatch,
) -> None:
    note = tmp_path / "note.md"
    source_text = "title\n--ai rewrite the heading\nbody\n"
    note.write_text(source_text, encoding="utf-8")
    notifications: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        ai_module,
        "run_codex",
        lambda **_kwargs: (_ for _ in ()).throw(
            CodexRunError("codex_failed", "failed")
        ),
    )
    monkeypatch.setattr(
        ai_module,
        "safe_notify",
        lambda *args, **kwargs: notifications.append((args, kwargs)),
    )
    module = Ai()

    changed = module.modified(_context(note), _system(note, module))

    assert changed is None
    assert note.read_text(encoding="utf-8") == source_text
    assert notifications
    assert notifications[0][0][0] == f"ai:{note.resolve()}"
    assert notifications[0][1]["use_rare_mode"] is True


def test_ai_does_not_overwrite_file_changed_during_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    note = tmp_path / "note.md"
    note.write_text("title\n--ai rewrite the heading\nbody\n", encoding="utf-8")
    notifications: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def change_source_during_run(**_kwargs) -> str:
        note.write_text("user changed this\n", encoding="utf-8")
        return "AI result\n"

    monkeypatch.setattr(ai_module, "run_codex", change_source_during_run)
    monkeypatch.setattr(
        ai_module,
        "safe_notify",
        lambda *args, **kwargs: notifications.append((args, kwargs)),
    )
    module = Ai()

    changed = module.modified(_context(note), _system(note, module))

    assert changed is None
    assert note.read_text(encoding="utf-8") == "user changed this\n"
    assert "file changed while Codex was running" in str(notifications[0][0][1])


def test_codex_runner_passes_only_task_json_to_isolated_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(ai_runner.shutil, "which", lambda _name: "/usr/bin/codex")

    def fake_subprocess_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        output_path = command[command.index("--output-last-message") + 1]
        Path(output_path).write_text(
            json.dumps({"content": "updated\n"}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(ai_runner.subprocess, "run", fake_subprocess_run)

    result = ai_runner.run_codex(
        source_path="/private/notes/note.md",
        source_content="original\n",
        prompt="update it",
        timeout_seconds=42,
    )

    command = captured["command"]
    assert isinstance(command, list)
    assert result == "updated\n"
    assert "/private/notes/note.md" not in command
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "--ephemeral" in command
    assert "never" == command[command.index("--ask-for-approval") + 1]
    assert "features.shell_tool=false" in command
    assert "features.unified_exec=false" in command
    assert "features.apps=false" in command
    assert 'web_search="disabled"' in command
    assert 'permissions.lucy_ai.filesystem={":minimal"="read"}' in command
    assert captured["timeout"] == 42
    assert json.loads(str(captured["input"])) == {
        "path": "/private/notes/note.md",
        "prompt": "update it",
        "content": "original\n",
    }
