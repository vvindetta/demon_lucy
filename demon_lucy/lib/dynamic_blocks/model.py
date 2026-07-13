from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TypeAlias


@dataclass(frozen=True)
class DynamicBlock:
    arg: str
    params: Mapping[str, str]
    raw_params: tuple[str, ...]
    body: str
    updated_timestamp: float | None
    content_start: int
    content_end: int
    body_start: int
    body_end: int
    line: int
    end_line: int


DynamicBlockRenderer: TypeAlias = Callable[
    [DynamicBlock, str, Mapping[str, object]],
    str,
]
