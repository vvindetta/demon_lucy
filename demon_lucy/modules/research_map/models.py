from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ResearchMapStatus(StrEnum):
    OPEN = "open"
    PARKED = "parked"
    DONE = "done"


class ResearchMapAction(StrEnum):
    INIT = "init"
    REGISTER = "register"
    NEW_NODE = "new-node"
    NEW_ARTIFACT = "new-artifact"
    PUT = "put"
    REBUILD = "rebuild"
    VALIDATE = "validate"


@dataclass(frozen=True)
class ValidationResult:
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class InitMapCommand:
    map_name: str
    title: str
    goal: str
    seed: str
    registry_summary: str


@dataclass(frozen=True)
class RegisterMapCommand:
    map_name: str
    label: str
    summary: str


@dataclass(frozen=True)
class NewNodeCommand:
    map_name: str
    question: str
    label: str
    parent: str | None
    summary: str | None
    status: ResearchMapStatus


@dataclass(frozen=True)
class NewArtifactCommand:
    map_name: str
    title: str
    body_path: str
    question: str | None


@dataclass(frozen=True)
class PutCommand:
    map_name: str
    source_path: str
    target: str


@dataclass(frozen=True)
class RebuildCommand:
    map_name: str


@dataclass(frozen=True)
class ValidateCommand:
    map_name: str


ResearchMapCommand = (
    InitMapCommand
    | RegisterMapCommand
    | NewNodeCommand
    | NewArtifactCommand
    | PutCommand
    | RebuildCommand
    | ValidateCommand
)
