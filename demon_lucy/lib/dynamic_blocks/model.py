from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TypeAlias


@dataclass(frozen=True)
class DynamicBlock:
    arg: str
    params: Mapping[str, str]
    body: str
    body_start: int
    body_end: int
    line: int
    end_line: int


DynamicBlockRenderer: TypeAlias = Callable[[DynamicBlock, str], str]
