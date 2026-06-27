from __future__ import annotations

from pathlib import Path

from watchdog.events import FileModifiedEvent

from demon_lucy.lib.args.parser import parse_args
from demon_lucy.module_manager import ModuleManager
from demon_lucy.modules.abstract_module import Context, System
from demon_lucy.modules.workspace import Workspace
from demon_lucy.runtime import DEMON_LUCY_STARTUP_TEMPLATE


def _startup_config(tmp_path: Path) -> dict[str, object]:
    config, unknown = parse_args(args=[], template=DEMON_LUCY_STARTUP_TEMPLATE)
    assert unknown == []
    config.update(
        {
            "sys_watch_paths": [str(tmp_path)],
            "sys_log_level": "info",
            "sys_notification_provider": "disable",
            "sys_notification_min_interval_seconds": 0.0,
            "sys_ignore_paths": [],
        }
    )
    return config


def test_workspace_init_creates_workspace_files_from_note_flag(tmp_path: Path) -> None:
    trigger = tmp_path / "trigger.md"
    workspace_root = tmp_path / "notes"
    trigger.write_text(f"--workspace-init {workspace_root}\n", encoding="utf-8")

    manager = ModuleManager(
        modules=[Workspace()],
        args=[],
        system_config=_startup_config(tmp_path),
    )

    changed = manager.run(str(trigger), FileModifiedEvent(str(trigger)))

    assert (workspace_root / ".archive").is_dir()
    assert (workspace_root / ".status").is_dir()
    assert (workspace_root / "now.md").read_text(encoding="utf-8") == ""
    assert (workspace_root / ".archive" / "past.md").read_text(encoding="utf-8") == ""
    assert (workspace_root / ".status" / "workspace-animation.md").read_text(
        encoding="utf-8"
    ) == '--status-animation "-- ---- --" "-<( ✷ )>-" "-< --- >-"\n'
    assert (workspace_root / ".status" / "workspace-sync.md").read_text(
        encoding="utf-8"
    ) == '--status git update --status-prefix "Sync: "\n'

    config_text = (workspace_root / ".lucy").read_text(encoding="utf-8")
    assert f"--sys-watch-paths {workspace_root}" in config_text
    assert f"--sys-config-path {workspace_root / '.lucy'}" in config_text
    assert "--sys-modules workspace archive status" in config_text
    assert "--archive-auto-pair now.md .archive/past.md 10 text" in config_text
    assert "--sys-log-level info" in config_text
    assert "--workspace-init" not in config_text

    assert changed == {
        str((workspace_root / ".lucy").resolve()): 1,
        str((workspace_root / ".status" / "workspace-animation.md").resolve()): 1,
        str((workspace_root / ".status" / "workspace-sync.md").resolve()): 1,
        str((workspace_root / "now.md").resolve()): 1,
        str((workspace_root / ".archive" / "past.md").resolve()): 1,
    }


def test_workspace_init_resolves_relative_path_from_note_dir(tmp_path: Path) -> None:
    note_dir = tmp_path / "inbox"
    note_dir.mkdir()
    trigger = note_dir / "trigger.md"
    trigger.write_text("body\n", encoding="utf-8")

    module = Workspace()
    ctx = Context(
        path=str(trigger),
        config={"workspace_init": "project"},
        arg_lines={"workspace_init": [1]},
    )
    system = System(
        event=FileModifiedEvent(str(trigger)),
        global_template=Workspace.template,
        modules=[module],
    )

    module.modified(ctx, system)

    assert (note_dir / "project" / ".lucy").exists()
    assert (note_dir / "project" / "now.md").exists()


def test_workspace_init_does_not_overwrite_existing_files(tmp_path: Path) -> None:
    workspace_root = tmp_path / "notes"
    (workspace_root / ".archive").mkdir(parents=True)
    (workspace_root / ".status").mkdir()
    (workspace_root / ".lucy").write_text("custom config\n", encoding="utf-8")
    (workspace_root / "now.md").write_text("keep now\n", encoding="utf-8")
    (workspace_root / ".archive" / "past.md").write_text(
        "keep past\n", encoding="utf-8"
    )

    module = Workspace()
    ctx = Context(
        path=str(tmp_path / "trigger.md"),
        config={"workspace_init": str(workspace_root)},
        arg_lines={"workspace_init": [1]},
    )
    system = System(
        event=FileModifiedEvent(str(tmp_path / "trigger.md")),
        global_template=Workspace.template,
        modules=[module],
    )

    changed = module.modified(ctx, system)

    assert (workspace_root / ".lucy").read_text(encoding="utf-8") == "custom config\n"
    assert (workspace_root / "now.md").read_text(encoding="utf-8") == "keep now\n"
    assert (workspace_root / ".archive" / "past.md").read_text(
        encoding="utf-8"
    ) == "keep past\n"
    assert changed == {
        str((workspace_root / ".status" / "workspace-animation.md").resolve()): 1,
        str((workspace_root / ".status" / "workspace-sync.md").resolve()): 1,
    }
