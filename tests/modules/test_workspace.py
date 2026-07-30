from __future__ import annotations

import logging
from pathlib import Path

from watchdog.events import FileModifiedEvent

from demon_lucy.lib.args.models import ArgSource, ParsedArgs, Template
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.module_manager import ModuleManager
from demon_lucy.modules.abstract_module import Context, System
from demon_lucy.modules.workspace import Workspace
from demon_lucy.runtime import DEMON_LUCY_STARTUP_TEMPLATE

_WORKSPACE_TEMPLATE: Template = [
    *DEMON_LUCY_STARTUP_TEMPLATE,
    *Workspace.template,
]


def _startup_args(
    tmp_path: Path,
    *,
    log_level: str | None = None,
) -> ParsedArgs:
    tokens = ["--sys-watch-paths", str(tmp_path)]
    if log_level is not None:
        tokens.extend(["--sys-log-level", log_level])
    return parse_args(
        args=tokens,
        template=DEMON_LUCY_STARTUP_TEMPLATE,
        source=ArgSource.CONFIG,
    )


def _context(
    path: Path,
    workspace: str,
    event: FileModifiedEvent,
) -> Context:
    return Context(
        path=str(path),
        args=parse_args(
            args=["--workspace-init", workspace],
            template=_WORKSPACE_TEMPLATE,
            source=ArgSource.FILE,
            line=1,
        ),
        run_mode="oneshot",
        event_id="test",
        event=event,
    )


def _system(module: Workspace) -> System:
    return System(
        global_template=_WORKSPACE_TEMPLATE,
        modules=[module],
    )


def test_workspace_default_template_contains_created_files() -> None:
    template_root = Path(Workspace._template_dir)

    assert (template_root / ".lucy" / "config.txt").exists()
    assert (template_root / ".status" / "-- ---- --").exists()
    assert (template_root / ".status" / "Sync").exists()
    assert (template_root / ".archive" / "past.md").exists()
    assert (template_root / "now.md").exists()
    assert (template_root / "welcome.md").exists()


def test_workspace_init_creates_workspace_files_from_note_flag(
    tmp_path: Path,
    caplog,
) -> None:
    trigger = tmp_path / "trigger.md"
    workspace_root = tmp_path / "notes"
    trigger.write_text(f"--workspace-init {workspace_root}\n", encoding="utf-8")

    manager = ModuleManager(
        modules=[Workspace()],
        startup_args=_startup_args(tmp_path),
    )

    with caplog.at_level(logging.INFO, logger="demon_lucy.modules.workspace"):
        changed = manager.run(str(trigger), FileModifiedEvent(str(trigger)))

    assert (workspace_root / ".archive").is_dir()
    assert (workspace_root / ".status").is_dir()
    assert (workspace_root / ".lucy").is_dir()
    assert (workspace_root / "now.md").read_text(encoding="utf-8") == ""
    welcome_text = (workspace_root / "welcome.md").read_text(encoding="utf-8")
    assert "Demon Lucy initialized this workspace." in welcome_text
    assert "daily notes and tasks" in welcome_text
    assert "after 10 hours without changes" in welcome_text
    assert "(you can change this rule in config)" in welcome_text
    assert "Archive pair:" not in welcome_text
    assert f"- `{workspace_root}`" in welcome_text
    assert f"- `--sys-watch-paths {workspace_root}`" in welcome_text
    assert "- `--archive-auto-pair now.md .archive/past.md 10 text`" in welcome_text
    assert "--sys-config-path" not in welcome_text
    assert (workspace_root / ".archive" / "past.md").read_text(encoding="utf-8") == ""
    assert (workspace_root / ".status" / "-- ---- --").read_text(
        encoding="utf-8"
    ) == '--status-animation "-- ---- --" "-<( ✷ )>-" "-< --- >-"\n'
    assert (workspace_root / ".status" / "Sync").read_text(
        encoding="utf-8"
    ) == '--status git update --status-prefix "Sync: "\n'

    config_path = workspace_root / ".lucy" / "config.txt"
    config_text = config_path.read_text(encoding="utf-8")
    assert f"--sys-watch-paths {workspace_root}" in config_text
    assert "--sys-config-path" not in config_text
    assert "--sys-modules workspace archive status" in config_text
    assert "--archive-auto-pair now.md .archive/past.md 10 text" in config_text
    assert "--sys-log-level" not in config_text
    assert "--sys-notification-provider" not in config_text
    assert "--workspace-init" not in config_text
    assert trigger.read_text(encoding="utf-8") == (
        f"workspace init ok: {workspace_root}\n"
    )
    assert "workspace.init_done" in caplog.text
    assert f"workspace={workspace_root}" in caplog.text
    assert "trigger_written=true" in caplog.text

    assert changed == {
        str(trigger.resolve()): 1,
        str(config_path.resolve()): 1,
        str((workspace_root / "welcome.md").resolve()): 1,
        str((workspace_root / ".status" / "-- ---- --").resolve()): 1,
        str((workspace_root / ".status" / "Sync").resolve()): 1,
        str((workspace_root / "now.md").resolve()): 1,
        str((workspace_root / ".archive" / "past.md").resolve()): 1,
    }


def test_workspace_init_resolves_relative_path_from_note_dir(tmp_path: Path) -> None:
    note_dir = tmp_path / "inbox"
    note_dir.mkdir()
    trigger = note_dir / "trigger.md"
    trigger.write_text("body\n", encoding="utf-8")

    module = Workspace()
    event = FileModifiedEvent(str(trigger))
    ctx = _context(
        trigger,
        "project",
        event,
    )
    system = _system(module)

    module.modified(ctx, system)

    assert (note_dir / "project" / ".lucy").is_dir()
    assert (note_dir / "project" / ".lucy" / "config.txt").exists()
    assert (note_dir / "project" / "welcome.md").exists()
    assert (note_dir / "project" / "now.md").exists()


def test_workspace_init_keeps_non_default_system_values(tmp_path: Path) -> None:
    trigger = tmp_path / "trigger.md"
    workspace_root = tmp_path / "notes"
    trigger.write_text(f"--workspace-init {workspace_root}\n", encoding="utf-8")
    manager = ModuleManager(
        modules=[Workspace()],
        startup_args=_startup_args(tmp_path, log_level="info"),
    )

    manager.run(str(trigger), FileModifiedEvent(str(trigger)))

    config_text = (workspace_root / ".lucy" / "config.txt").read_text(encoding="utf-8")
    welcome_text = (workspace_root / "welcome.md").read_text(encoding="utf-8")
    assert "--sys-log-level info" in config_text
    assert "- `--sys-log-level info`" in welcome_text


def test_workspace_init_does_not_overwrite_existing_files(tmp_path: Path) -> None:
    workspace_root = tmp_path / "notes"
    (workspace_root / ".archive").mkdir(parents=True)
    (workspace_root / ".status").mkdir()
    (workspace_root / ".lucy").mkdir()
    (workspace_root / ".lucy" / "config.txt").write_text(
        "custom config\n",
        encoding="utf-8",
    )
    (workspace_root / "welcome.md").write_text("custom welcome\n", encoding="utf-8")
    (workspace_root / "now.md").write_text("keep now\n", encoding="utf-8")
    (workspace_root / ".archive" / "past.md").write_text(
        "keep past\n", encoding="utf-8"
    )

    module = Workspace()
    trigger = tmp_path / "trigger.md"
    event = FileModifiedEvent(str(trigger))
    ctx = _context(
        trigger,
        str(workspace_root),
        event,
    )
    system = _system(module)

    changed = module.modified(ctx, system)

    assert (workspace_root / ".lucy" / "config.txt").read_text(
        encoding="utf-8"
    ) == "custom config\n"
    assert (workspace_root / "welcome.md").read_text(encoding="utf-8") == (
        "custom welcome\n"
    )
    assert (workspace_root / "now.md").read_text(encoding="utf-8") == "keep now\n"
    assert (workspace_root / ".archive" / "past.md").read_text(
        encoding="utf-8"
    ) == "keep past\n"
    assert changed == {
        str((workspace_root / ".status" / "-- ---- --").resolve()): 1,
        str((workspace_root / ".status" / "Sync").resolve()): 1,
    }
