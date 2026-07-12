from __future__ import annotations

import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from demon_lucy.lib.args.parser import (
    ArgTemplate,
    Template,
    is_valid_flag_token,
    normalize_template_params,
)


INCLUDE_TEMPLATE: Template = [
    ArgTemplate(
        name="--include",
        value_type=str,
        default=[],
        description="Render a complete file inside a dynamic block. Format: --include file.",
        params=(
            ArgTemplate(name="source", required=True),
            ArgTemplate(name="depth", value_type=int, default=3),
        ),
    ),
    ArgTemplate(
        name="--include-depth",
        value_type=int,
        default=3,
        description="Maximum nested include render depth. Default: 3.",
    )
]


@dataclass(frozen=True)
class IncludeParams:
    source: str
    depth: int


def normalize_include_params(values: Mapping[str, object]) -> IncludeParams:
    normalized = normalize_template_params(values, INCLUDE_TEMPLATE[0].params)
    depth = cast(int, normalized["depth"])
    if depth < 1:
        raise ValueError("include depth must be at least 1")
    return IncludeParams(
        source=cast(str, normalized["source"]),
        depth=depth,
    )


def include_params_from_command(values: list[str], *, depth: int) -> IncludeParams:
    if len(values) != 1:
        raise ValueError("--include requires exactly one file path")
    return normalize_include_params({"source": values[0], "depth": depth})


def include_sources_from_line(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return []
    tokens = shlex.split(stripped, comments=False, posix=True)
    if not tokens or not is_valid_flag_token(tokens[0]):
        return []

    sources: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        flag, separator, inline_value = token.partition("=")
        if flag != "--include":
            index += 1
            continue

        values: list[str] = []
        if separator:
            values.append(inline_value)
        index += 1
        while index < len(tokens) and not is_valid_flag_token(tokens[index]):
            values.append(tokens[index])
            index += 1
        sources.append(include_params_from_command(values, depth=1).source)
    return sources
