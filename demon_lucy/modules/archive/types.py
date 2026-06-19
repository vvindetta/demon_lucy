from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArchiveRequest:
    route: str
    output_mode: str
    src_selector: str
    dest_selector: str | None
    idle_hours: float
    force: bool
