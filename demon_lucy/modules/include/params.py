from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from demon_lucy.lib.args.parser import (
    ArgTemplate,
    Template,
    is_valid_flag_token,
    normalize_template_params,
    split_arg_line,
)


INCLUDE_TEMPLATE: Template = [
    ArgTemplate(
        name="--include",
        value_type=str,
        default=[],
        description="Render a complete file inside a dynamic block. Format: --include file.",
        params=(
            ArgTemplate(name="source", required=True),
        ),
    ),
    ArgTemplate(
        name="--include-find",
        value_type=str,
        default=[],
        description=(
            "Collect paragraphs whose first line starts with a keyword. Format: "
            "--include-find file-or-directory keyword."
        ),
        params=(
            ArgTemplate(name="source", required=True),
            ArgTemplate(name="keyword", required=True),
        ),
    ),
    ArgTemplate(
        name="--include-depth",
        value_type=int,
        default=3,
        description="Maximum nested include render depth. Default: 3.",
    ),
]
INCLUDE_DYNAMIC_ARGS = {"include", "include-find"}


@dataclass(frozen=True)
class IncludeParams:
    arg: str
    source: str
    keyword: str | None = None


def include_arg_template(arg: str) -> ArgTemplate:
    if arg not in INCLUDE_DYNAMIC_ARGS:
        raise ValueError(f"unsupported include arg: {arg}")
    flag = f"--{arg}"
    for item in INCLUDE_TEMPLATE:
        if item.name == flag:
            return item
    raise ValueError(f"unsupported include arg: {arg}")


def normalize_include_params(
    arg: str,
    values: Mapping[str, object],
) -> IncludeParams:
    arg_template = include_arg_template(arg)
    normalized = normalize_template_params(values, arg_template.params)
    keyword = cast(str | None, normalized.get("keyword"))
    if keyword is not None and not keyword:
        raise ValueError("include find keyword must not be empty")
    return IncludeParams(
        arg=arg,
        source=cast(str, normalized["source"]),
        keyword=keyword,
    )


def include_block_params(params: IncludeParams) -> dict[str, str]:
    values = {"source": params.source}
    if params.keyword is not None:
        values["keyword"] = params.keyword
    return values


def include_params_from_command(flag: str, values: list[str]) -> IncludeParams:
    arg = flag.removeprefix("--")
    if arg == "include":
        if len(values) != 1:
            raise ValueError("--include requires exactly one file path")
        return normalize_include_params(arg, {"source": values[0]})
    if arg == "include-find":
        if len(values) < 2:
            raise ValueError("--include-find requires: file-or-directory keyword")
        return normalize_include_params(
            arg,
            {
                "source": values[0],
                "keyword": " ".join(values[1:]),
            },
        )
    raise ValueError(f"unsupported include flag: {flag}")


def include_params_from_line(line: str) -> list[IncludeParams]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return []
    tokens = split_arg_line(stripped)
    if not tokens or not is_valid_flag_token(tokens[0]):
        return []

    commands: list[IncludeParams] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        flag, separator, inline_value = token.partition("=")
        if flag.removeprefix("--") not in INCLUDE_DYNAMIC_ARGS:
            index += 1
            continue

        values: list[str] = []
        if separator:
            values.append(inline_value)
        index += 1
        while index < len(tokens) and not is_valid_flag_token(tokens[index]):
            values.append(tokens[index])
            index += 1
        commands.append(include_params_from_command(flag, values))
    return commands
