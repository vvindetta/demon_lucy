from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from demon_lucy.lib.args.parser import (
    ArgTemplate,
    Template,
    normalize_template_params,
)


INCLUDE_PARAMS = (ArgTemplate(name="source", required=True),)
INCLUDE_TEMPLATE: Template = [
    ArgTemplate(
        name="--include",
        value_type=str,
        default=[],
        description="Render a complete file inside a dynamic block. Format: --include file.",
        params=INCLUDE_PARAMS,
    )
]


def normalize_include_source(values: Mapping[str, object]) -> str:
    normalized = normalize_template_params(values, INCLUDE_PARAMS)
    return cast(str, normalized["source"])


def include_source_from_command(values: list[str]) -> str:
    if len(values) != 1:
        raise ValueError("--include requires exactly one file path")
    return normalize_include_source({"source": values[0]})
