from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ArchiveOutputMode(StrEnum):
    TEXT = "text"
    FILE = "file"


@dataclass(frozen=True)
class ArchiveRequest:
    route: str
    output_mode: ArchiveOutputMode
    src_selector: str
    dest_selector: str | None
    idle_hours: float
    force: bool
