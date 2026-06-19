from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional

from watchdog.events import FileSystemEvent

from demon_lucy.lib.args.parser import Template

IgnoreMap = Dict[str, int]
RunMode = Literal["daemon", "oneshot"]


@dataclass(frozen=True)
class System:
    """
    Runtime system info.

    - event: watchdog event that triggered the run
    - event_id: short id shared by logs from the same event block
    - global_template: full args template used by ModuleManager
    - modules: ordered module instances in the pipeline
    - run_mode: runtime mode ("daemon" or "oneshot")
    """

    event: FileSystemEvent
    global_template: Template
    modules: List["AbstractModule"]
    run_mode: RunMode = "daemon"
    event_id: str = ""


@dataclass(frozen=True)
class Context:
    """
    Module input for one run.

    - path: absolute file path (event.src_path; for moved = event.dest_path)
    - config: resolved args for this file (global + file flags; includes defaults)
    - arg_lines: arg -> 1-based line numbers where it appeared in the file
    - system: runtime info (see class System)
    """

    path: str
    config: dict
    arg_lines: dict


class AbstractModule(ABC):
    """
    Base interface for all processing modules.

    Every module optionally handles events:
    - created, modified, moved, deleted, opened

    Return value
    - None:
        No filesystem changes were made.

    - {'path1': 1, 'path2', 3, ...}:
        Filesystem paths WAS changed N times by this module.
        The daemon will ignore the next events for these paths to prevent loops.

    Prioriry
    - 'priority': lower runs earlier.

    Template:
    - 'template': flags this module adds to the global argument template.

    - example:
        [
            ("--flag", type, "default value", "manual string", False),
            ("--rename", str, None, "Will rename file", False),
            ("--banner", str, "date", "Draws ASCII banner", False),
            ("--tags", str, [], "Multi-value argument", False),
        ]
    """

    name: str
    priority: int = 15
    template: Template = []

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return None

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return None

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return None

    def deleted(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return None

    def opened(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return None
