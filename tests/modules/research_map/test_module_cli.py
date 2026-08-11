from pathlib import Path

import pytest

from demon_lucy.lib.args.models import ArgSource, ParsedArgs, UnknownArg
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.module_manager import ModuleManager
from demon_lucy.modules.research_map import ResearchMap
from demon_lucy.modules.research_map.config import RESEARCH_MAP_TEMPLATE
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
                UnknownArg(token=token, source=ArgSource.CLI)
                for token in cli_tokens
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
                "--research-map-init", "lucy_map",
                "--research-map-init-title", "Lucy",
                "--research-map-init-goal", "Goal",
                "--research-map-init-seed", "Seed",
                "--research-map-init-registry-summary", "Lucy map operations",
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
                "--research-map-new-node", "lucy_map",
                "--research-map-node-question", "Question?",
                "--research-map-node-label", "Question",
                "--research-map-node-summary", "Root question state",
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
    assert "[Lucy](lucy_map/index.md) - Lucy map operations" in (
        root / "index.md"
    ).read_text(encoding="utf-8")


def test_cli_validation_failure_is_expected_module_error(tmp_path: Path) -> None:
    root = tmp_path / "maps"
    root.mkdir()
    (root / "index.md").write_text(
        "# Maps\n\n## Active\n\n"
        "- [Broken](broken_map/index.md) - Broken map\n",
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
