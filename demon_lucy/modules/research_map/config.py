from __future__ import annotations

from collections.abc import Callable

from demon_lucy.lib.args.models import ArgSource, KnownArg, ParsedArgs, Template
from demon_lucy.modules.research_map.documents import ResearchMapError
from demon_lucy.modules.research_map.models import (
    InitMapCommand,
    NewArtifactCommand,
    NewNodeCommand,
    PutCommand,
    RebuildCommand,
    RegisterMapCommand,
    ResearchMapAction,
    ResearchMapCommand,
    ResearchMapStatus,
    ValidateCommand,
)
from demon_lucy.modules.research_map.paths import validate_map_name


def _arg(name: str, value_type: type, default: object, description: str) -> KnownArg:
    return KnownArg(
        name=name,
        value_type=value_type,
        default=default,
        description=description,
        required=False,
    )


RESEARCH_MAP_TEMPLATE: Template = [
    _arg("research-map-root", str, "", "Existing directory containing research maps."),
    _arg("research-map-init", str, "", "Create and register a research map."),
    _arg("research-map-init-title", str, "", "New map title."),
    _arg("research-map-init-goal", str, "", "New map goal."),
    _arg("research-map-init-seed", str, "", "New map seed."),
    _arg(
        "research-map-init-registry-summary",
        str,
        "",
        "Agent-authored registry summary for a new map.",
    ),
    _arg("research-map-register", str, "", "Register an existing research map."),
    _arg("research-map-register-label", str, "", "Registry label."),
    _arg("research-map-register-summary", str, "", "Agent-authored registry summary."),
    _arg("research-map-new-node", str, "", "Create a research-map question node."),
    _arg("research-map-node-question", str, "", "Full node question."),
    _arg("research-map-node-label", str, "", "Short node label."),
    _arg("research-map-node-parent", str, "", "Optional parent question ID."),
    _arg("research-map-node-summary", str, "", "Required root branch summary."),
    _arg(
        "research-map-node-status",
        ResearchMapStatus,
        ResearchMapStatus.OPEN,
        "Node status: open, parked, or done.",
    ),
    _arg("research-map-new-artifact", str, "", "Create an immutable artifact."),
    _arg("research-map-artifact-title", str, "", "Artifact title."),
    _arg("research-map-artifact-body-path", str, "", "Artifact body file below /tmp."),
    _arg("research-map-artifact-question", str, "", "Optional related question ID."),
    _arg("research-map-put", str, "", "Publish a prepared map file."),
    _arg("research-map-put-source-path", str, "", "Prepared source file below /tmp."),
    _arg("research-map-put-target", str, "", "Map-relative destination."),
    _arg("research-map-rebuild", str, "", "Reconcile a research map."),
    _arg("research-map-validate", str, "", "Validate a research map read-only."),
]


_ACTION_FLAGS = {
    "research-map-init": ResearchMapAction.INIT,
    "research-map-register": ResearchMapAction.REGISTER,
    "research-map-new-node": ResearchMapAction.NEW_NODE,
    "research-map-new-artifact": ResearchMapAction.NEW_ARTIFACT,
    "research-map-put": ResearchMapAction.PUT,
    "research-map-rebuild": ResearchMapAction.REBUILD,
    "research-map-validate": ResearchMapAction.VALIDATE,
}

_SUPPORTING_FLAGS = {
    ResearchMapAction.INIT: {
        "research-map-init-title",
        "research-map-init-goal",
        "research-map-init-seed",
        "research-map-init-registry-summary",
    },
    ResearchMapAction.REGISTER: {
        "research-map-register-label",
        "research-map-register-summary",
    },
    ResearchMapAction.NEW_NODE: {
        "research-map-node-question",
        "research-map-node-label",
        "research-map-node-parent",
        "research-map-node-summary",
        "research-map-node-status",
    },
    ResearchMapAction.NEW_ARTIFACT: {
        "research-map-artifact-title",
        "research-map-artifact-body-path",
        "research-map-artifact-question",
    },
    ResearchMapAction.PUT: {
        "research-map-put-source-path",
        "research-map-put-target",
    },
    ResearchMapAction.REBUILD: set(),
    ResearchMapAction.VALIDATE: set(),
}


def _required(args: ParsedArgs, name: str) -> str:
    value = str(args.require(name).value).strip()
    if not value:
        raise ResearchMapError(f"--{name} is required")
    return value


def command_from_args(args: ParsedArgs) -> ResearchMapCommand:
    selected = [
        (name, action)
        for name, action in _ACTION_FLAGS.items()
        if args.require(name).source is ArgSource.CLI
        and str(args.require(name).value).strip()
    ]
    if len(selected) != 1:
        raise ResearchMapError("exactly one research map action is required")
    action_flag, action = selected[0]
    map_name = validate_map_name(_required(args, action_flag))

    owned = _SUPPORTING_FLAGS[action]
    for names in _SUPPORTING_FLAGS.values():
        for name in names:
            argument = args.require(name)
            if argument.source is ArgSource.CLI and name not in owned:
                raise ResearchMapError(f"--{name} is not valid with --{action_flag}")

    builders: dict[ResearchMapAction, Callable[[], ResearchMapCommand]] = {
        ResearchMapAction.INIT: lambda: InitMapCommand(
            map_name=map_name,
            title=_required(args, "research-map-init-title"),
            goal=_required(args, "research-map-init-goal"),
            seed=_required(args, "research-map-init-seed"),
            registry_summary=_required(args, "research-map-init-registry-summary"),
        ),
        ResearchMapAction.REGISTER: lambda: RegisterMapCommand(
            map_name=map_name,
            label=_required(args, "research-map-register-label"),
            summary=_required(args, "research-map-register-summary"),
        ),
        ResearchMapAction.NEW_NODE: lambda: _new_node_command(args, map_name),
        ResearchMapAction.NEW_ARTIFACT: lambda: NewArtifactCommand(
            map_name=map_name,
            title=_required(args, "research-map-artifact-title"),
            body_path=_required(args, "research-map-artifact-body-path"),
            question=str(args.require("research-map-artifact-question").value).strip()
            or None,
        ),
        ResearchMapAction.PUT: lambda: PutCommand(
            map_name=map_name,
            source_path=_required(args, "research-map-put-source-path"),
            target=_required(args, "research-map-put-target"),
        ),
        ResearchMapAction.REBUILD: lambda: RebuildCommand(map_name=map_name),
        ResearchMapAction.VALIDATE: lambda: ValidateCommand(map_name=map_name),
    }
    return builders[action]()


def _new_node_command(args: ParsedArgs, map_name: str) -> NewNodeCommand:
    parent = str(args.require("research-map-node-parent").value).strip() or None
    summary = str(args.require("research-map-node-summary").value).strip() or None
    if parent is None and summary is None:
        raise ResearchMapError("--research-map-node-summary is required for a root node")
    if parent is not None and summary is not None:
        raise ResearchMapError("--research-map-node-summary is not valid for a child node")
    status = args.require("research-map-node-status").value
    if not isinstance(status, ResearchMapStatus):
        raise ResearchMapError("invalid --research-map-node-status")
    return NewNodeCommand(
        map_name=map_name,
        question=_required(args, "research-map-node-question"),
        label=_required(args, "research-map-node-label"),
        parent=parent,
        summary=summary,
        status=status,
    )
