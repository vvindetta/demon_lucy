import pytest

from demon_lucy.lib.args.models import ArgSource
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.modules.research_map.config import (
    RESEARCH_MAP_TEMPLATE,
    command_from_args,
)
from demon_lucy.modules.research_map.documents import ResearchMapError
from demon_lucy.modules.research_map.models import (
    NewNodeCommand,
    RegisterMapCommand,
    ResearchMapStatus,
)


def test_template_has_typed_defaults() -> None:
    parsed = parse_args(args=[], template=RESEARCH_MAP_TEMPLATE)
    assert parsed.require("research-map-root").value == ""
    assert parsed.require("research-map-node-status").value is ResearchMapStatus.OPEN


def test_command_from_args_builds_typed_new_node_command() -> None:
    parsed = parse_args(
        args=[
            "--research-map-new-node", "lucy_map",
            "--research-map-node-question", "How does Lucy update maps?",
            "--research-map-node-label", "Automatic updates",
            "--research-map-node-summary", "Summary for the root branch",
            "--research-map-node-status", "parked",
        ],
        template=RESEARCH_MAP_TEMPLATE,
        source=ArgSource.CLI,
    )
    assert command_from_args(parsed) == NewNodeCommand(
        map_name="lucy_map",
        question="How does Lucy update maps?",
        label="Automatic updates",
        parent=None,
        summary="Summary for the root branch",
        status=ResearchMapStatus.PARKED,
    )


def test_command_rejects_two_actions() -> None:
    parsed = parse_args(
        args=["--research-map-rebuild", "lucy_map", "--research-map-validate", "lucy_map"],
        template=RESEARCH_MAP_TEMPLATE,
        source=ArgSource.CLI,
    )
    with pytest.raises(ResearchMapError, match="exactly one research map action"):
        command_from_args(parsed)


def test_command_from_args_builds_register_command() -> None:
    parsed = parse_args(
        args=[
            "--research-map-register", "imported_map",
            "--research-map-register-label", "Imported",
            "--research-map-register-summary", "Imported research map",
        ],
        template=RESEARCH_MAP_TEMPLATE,
        source=ArgSource.CLI,
    )
    assert command_from_args(parsed) == RegisterMapCommand(
        map_name="imported_map", label="Imported", summary="Imported research map"
    )


def test_command_rejects_supporting_arg_for_another_action() -> None:
    parsed = parse_args(
        args=[
            "--research-map-validate", "lucy_map",
            "--research-map-node-label", "Unused",
        ],
        template=RESEARCH_MAP_TEMPLATE,
        source=ArgSource.CLI,
    )
    with pytest.raises(ResearchMapError, match="not valid with --research-map-validate"):
        command_from_args(parsed)
