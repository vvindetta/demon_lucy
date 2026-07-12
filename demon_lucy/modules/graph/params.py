from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


GRAPH_ARGS = {"graph", "graph-regex"}
GRAPH_PERIODS = {"week", "month", "year", "all"}
DEFAULT_GRAPH_PERIOD = "year"
DEFAULT_GRAPH_VIEW = "ascii"
GRAPH_VIEWS = {"ascii", "markdown", "markdown-code"}


@dataclass(frozen=True)
class GraphParams:
    arg: str
    source: str
    pattern: str
    period: str
    view: str

    @property
    def is_regex(self) -> bool:
        return self.arg == "graph-regex"


def normalize_graph_params(
    arg: str,
    values: Mapping[str, str],
) -> GraphParams:
    if arg not in GRAPH_ARGS:
        raise ValueError(f"unsupported graph arg: {arg}")

    unknown = sorted(set(values) - {"source", "pattern", "period", "view"})
    if unknown:
        raise ValueError(f"unknown graph parameter: {unknown[0]}")

    source = str(values.get("source", "")).strip()
    if not source:
        raise ValueError("missing graph source")
    pattern = str(values.get("pattern", "")).strip()
    if not pattern:
        raise ValueError("missing graph pattern")

    period = str(values.get("period", DEFAULT_GRAPH_PERIOD)).strip().lower()
    if period not in GRAPH_PERIODS:
        raise ValueError(f"unsupported graph period: {period}")
    view = str(values.get("view", DEFAULT_GRAPH_VIEW)).strip().lower()
    if view not in GRAPH_VIEWS:
        raise ValueError(f"unsupported graph view: {view}")
    return GraphParams(
        arg=arg,
        source=source,
        pattern=pattern,
        period=period,
        view=view,
    )


def graph_params_from_command(flag: str, values: list[str]) -> GraphParams:
    arg = flag.removeprefix("--")
    if len(values) < 2:
        raise ValueError(f"{flag} requires: file pattern [period]")

    named_values = {"source": str(values[0])}
    pattern_values = values[1:]
    maybe_period = str(values[-1]).strip().lower()
    if len(values) >= 3 and maybe_period in GRAPH_PERIODS:
        named_values["period"] = maybe_period
        pattern_values = values[1:-1]
    named_values["pattern"] = " ".join(pattern_values)
    return normalize_graph_params(arg, named_values)
