from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from demon_lucy.lib.args.parser import (
    ArgTemplate,
    Template,
    normalize_template_params,
    template_allowed_values,
)

GRAPH_ARG_REGEX = {
    "graph": False,
    "graph-regex": True,
}


class GraphPeriod(StrEnum):
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"
    ALL = "all"


class GraphView(StrEnum):
    ASCII = "ascii"
    MD = "md"


GRAPH_PARAMS = (
    ArgTemplate(name="source", required=True),
    ArgTemplate(name="pattern", required=True),
    ArgTemplate(
        name="period",
        value_type=GraphPeriod,
        default=GraphPeriod.YEAR,
    ),
    ArgTemplate(
        name="view",
        value_type=GraphView,
        default=GraphView.ASCII,
    ),
)
GRAPH_TEMPLATE: Template = [
    ArgTemplate(
        name="--graph",
        value_type=str,
        default=[],
        description=(
            "Build a text graph for a literal search in a file. Format: "
            "--graph file pattern [week|month|year|all]. Default period: year."
        ),
        params=GRAPH_PARAMS,
    ),
    ArgTemplate(
        name="--graph-regex",
        value_type=str,
        default=[],
        description=(
            "Build a text graph for a regular expression search in a file. "
            "Format: --graph-regex file regex [week|month|year|all]. "
            "Default period: year."
        ),
        params=GRAPH_PARAMS,
    ),
]


def graph_arg_template(arg: str) -> ArgTemplate:
    flag = f"--{arg}"
    for item in GRAPH_TEMPLATE:
        if item.name == flag:
            return item
    raise ValueError(f"unsupported graph arg: {arg}")


@dataclass(frozen=True)
class GraphParams:
    arg: str
    source: str
    pattern: str
    period: GraphPeriod
    view: GraphView

    @property
    def is_regex(self) -> bool:
        return GRAPH_ARG_REGEX[self.arg]


def normalize_graph_params(
    arg: str,
    values: Mapping[str, str],
) -> GraphParams:
    if arg not in GRAPH_ARG_REGEX:
        raise ValueError(f"unsupported graph arg: {arg}")

    arg_template = graph_arg_template(arg)
    normalized = normalize_template_params(values, arg_template.params)
    return GraphParams(
        arg=arg,
        source=cast(str, normalized["source"]),
        pattern=cast(str, normalized["pattern"]),
        period=cast(GraphPeriod, normalized["period"]),
        view=cast(GraphView, normalized["view"]),
    )


def graph_params_from_command(flag: str, values: list[str]) -> GraphParams:
    arg = flag.removeprefix("--")
    if len(values) < 2:
        raise ValueError(f"{flag} requires: file pattern [period]")

    named_values = {"source": str(values[0])}
    pattern_values = values[1:]
    arg_template = graph_arg_template(arg)
    period_param = next(
        param for param in arg_template.params if param.name == "period"
    )
    allowed_periods = template_allowed_values(period_param)
    maybe_period = str(values[-1]).strip().lower()
    if len(values) >= 3 and maybe_period in allowed_periods:
        named_values["period"] = maybe_period
        pattern_values = values[1:-1]
    named_values["pattern"] = " ".join(pattern_values)
    return normalize_graph_params(arg, named_values)
