from __future__ import annotations

import time
from abc import ABC
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from watchdog.events import FileSystemEvent

from demon_lucy.lib.args.models import ParsedArgs, Template
from demon_lucy.lib.dynamic_blocks.model import DynamicBlockRenderer
from demon_lucy.lib.operating_system import (
    OperatingSystem,
    detect_operating_system,
)

RunMode = Literal["daemon", "oneshot", "cli"]


@dataclass(frozen=True)
class System:
    """
    Shared Lucy runtime services and state.

    - global_template: full args template used by ModuleManager
    - modules: ordered module instances in the pipeline
    - operating_system: detected OS ("linux", "macos", "windows", or "other")
    - runtime_started_at_monotonic: monotonic time when this Lucy runtime started
    """

    global_template: Template
    modules: list["AbstractModule"]
    operating_system: OperatingSystem = field(default_factory=detect_operating_system)
    runtime_started_at_monotonic: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class Context:
    """
    Module input for one run.

    - path: event file path, or working directory for a direct CLI run
    - args: resolved arguments with their values, sources, and line numbers
    - run_mode: runtime mode ("daemon", "oneshot", or "cli")
    - event_id: short id shared by logs from the same run
    - event: watchdog event that triggered the run; absent for direct CLI runs
    """

    path: str
    args: ParsedArgs
    run_mode: RunMode
    event_id: str
    event: FileSystemEvent | None = None


@dataclass(frozen=True)
class ModuleResult:
    context: Context
    changed: dict[str, int] = field(default_factory=dict)


class AbstractModule(ABC):
    """
    Base interface for all processing modules.

    Every module optionally handles events and direct CLI runs:
    - created, modified, moved, deleted, opened, cli

    Handlers return ModuleResult with the context for the next module and the
    filesystem changes made by this module. None means no changes.

    Priority
    - 'priority': integer; lower runs earlier.

    Template:
    - 'template': flags this module adds to the global argument template.

    - example:
        [
            KnownArg(
                name="rename",
                value_type=str,
                description="Will rename file",
            ),
            KnownArg(
                name="banner",
                value_type=str,
                default="date",
                description="Draws ASCII banner",
            ),
            KnownArg(
                name="tags",
                value_type=str,
                default=[],
                description="Multi-value argument",
            ),
        ]
    """

    name: str
    priority: int = 15
    template: Template = []
    dynamic_block_renderers: Mapping[str, DynamicBlockRenderer] = {}

    def created(self, ctx: Context, system: System) -> ModuleResult | None:
        return None

    def modified(self, ctx: Context, system: System) -> ModuleResult | None:
        return None

    def moved(self, ctx: Context, system: System) -> ModuleResult | None:
        return None

    def deleted(self, ctx: Context, system: System) -> ModuleResult | None:
        return None

    def opened(self, ctx: Context, system: System) -> ModuleResult | None:
        return None

    def cli(self, ctx: Context, system: System) -> ModuleResult | None:
        return None
