from pathlib import Path
from unittest.mock import Mock

import pytest

from demon_lucy.lib import notifications
from demon_lucy.lib.args.models import ArgSource, ParsedArgs, UnknownArg
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.module_manager import ModuleManager
from demon_lucy.modules.research_map import ResearchMap
from demon_lucy.modules.research_map.config import RESEARCH_MAP_TEMPLATE
from demon_lucy.modules.research_map.maps import init_map
from demon_lucy.modules.research_map.nodes import read_nodes
from demon_lucy.modules.research_map.validation import validate_map
from demon_lucy.runtime import DEMON_LUCY_STARTUP_TEMPLATE, select_demon_lucy_modules


def _startup_args(root: Path, cli_tokens: list[str]) -> ParsedArgs:
    config = parse_args(
        args=[
            "--research-map-root",
            str(root),
            "--sys-notification-provider",
            "disable",
        ],
        template=[*DEMON_LUCY_STARTUP_TEMPLATE, *RESEARCH_MAP_TEMPLATE],
        source=ArgSource.CONFIG,
    )
    return config.merged_with(
        ParsedArgs(
            unknown=tuple(
                UnknownArg(token=token, source=ArgSource.CLI) for token in cli_tokens
            )
        )
    )


def test_research_map_is_available_but_not_default() -> None:
    assert [module.name for module in select_demon_lucy_modules(["research_map"])] == [
        "research_map"
    ]
    defaults = parse_args(args=[], template=DEMON_LUCY_STARTUP_TEMPLATE)
    assert "research_map" not in defaults.require("sys-modules").value


def test_cli_init_and_new_node_leave_valid_derived_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "maps"
    root.mkdir()
    (root / "index.md").write_text("# Maps\n\n## Active\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    init_manager = ModuleManager(
        [ResearchMap()],
        _startup_args(
            root,
            [
                "--research-map-init",
                "lucy_map",
                "--research-map-init-title",
                "Lucy",
                "--research-map-init-goal",
                "Goal",
                "--research-map-init-seed",
                "Seed",
            ],
        ),
        run_mode="cli",
    )
    assert init_manager.run_cli(event_id="init-1")[1] == 1

    node_manager = ModuleManager(
        [ResearchMap()],
        _startup_args(
            root,
            [
                "--research-map-new-node",
                "lucy_map",
                "--research-map-node-title",
                "Question?",
                "--research-map-node-label",
                "Question",
                "--research-map-node-summary",
                "Root question state",
            ],
        ),
        run_mode="cli",
    )
    changed, modules_run = node_manager.run_cli(event_id="node-1")
    assert modules_run == 1
    assert changed is not None
    assert (root / "lucy_map" / "b-nodes" / "1_question.md").exists()
    assert "1 - Question" in (root / "lucy_map" / "questions.md").read_text(
        encoding="utf-8"
    )
    assert "# Maps\n\n## Active\n" == (root / "index.md").read_text(encoding="utf-8")


def test_cli_validation_failure_is_logged_without_notifications(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    notify = Mock()
    monkeypatch.setattr(notifications, "notify", notify)
    root = tmp_path / "maps"
    root.mkdir()
    (root / "index.md").write_text(
        "# Maps\n\n## Active\n\n" "- [Broken](broken_map/index.md) - Broken map\n",
        encoding="utf-8",
    )
    (root / "broken_map").mkdir()
    manager = ModuleManager(
        [ResearchMap()],
        _startup_args(root, ["--research-map-validate", "broken_map"]),
        run_mode="cli",
    )
    with pytest.raises(ValueError, match="research map validation failed"):
        manager.run_cli(event_id="validate-1")
    assert "research_map.operation_failed" in caplog.text
    notify.assert_not_called()


def test_cli_creates_nested_conspect_without_registry_or_invented_body(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    for tokens in (
        [
            "--research-map-init",
            "travel/topic_map",
            "--research-map-init-title",
            "Topic",
            "--research-map-init-goal",
            "Goal",
            "--research-map-init-seed",
            "Seed",
        ],
        [
            "--research-map-new-node",
            "travel/topic_map",
            "--research-map-node-type",
            "conspect",
            "--research-map-node-label",
            "Notes",
        ],
        ["--research-map-validate", "travel/topic_map"],
    ):
        manager = ModuleManager(
            [ResearchMap()], _startup_args(tmp_path, tokens), run_mode="cli"
        )
        assert manager.run_cli(event_id="conspect")[1] == 1
    document = (tmp_path / "travel/topic_map/b-nodes/1_notes.md").read_text()
    assert "type: conspect" in document
    assert (
        "status:" not in document
        and "created:" not in document
        and "updated:" not in document
    )
    assert document.split("---", 2)[2].strip() == ""
    assert not (tmp_path / "index.md").exists()


@pytest.mark.parametrize(
    "target",
    [
        "guide.pdf",
        "b-nodes/guide.md",
        "b-nodes/components/documents/guide.pdf",
        ".attach/sources/guide.md",
    ],
)
def test_cli_put_supporting_file_anywhere_without_overwriting(
    tmp_path: Path,
    monkeypatch,
    target: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    init_map(
        root=tmp_path, map_name="topic_map", title="Topic", goal="Goal", seed="Seed"
    )
    source = tmp_path / "source"
    source.write_bytes(b"Supporting material\n")
    manager = ModuleManager(
        [ResearchMap()],
        _startup_args(
            tmp_path,
            [
                "--research-map-put",
                "topic_map",
                "--research-map-put-source-path",
                str(source),
                "--research-map-put-target",
                target,
            ],
        ),
        run_mode="cli",
    )
    assert manager.run_cli(event_id="put")[1] == 1
    map_dir = tmp_path / "topic_map"
    assert (map_dir / target).read_bytes() == b"Supporting material\n"
    assert read_nodes(map_dir) == {}
    result = validate_map(map_dir)
    assert result.is_valid, result.errors

    source.write_bytes(b"Replacement")
    with pytest.raises(ValueError):
        manager.run_cli(event_id="put-again")
    assert (map_dir / target).read_bytes() == b"Supporting material\n"
