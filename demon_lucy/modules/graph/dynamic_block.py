from __future__ import annotations

from demon_lucy.lib.path import resolve_file_source_path
from demon_lucy.lib.dynamic_blocks.model import DynamicBlock
from demon_lucy.lib.dynamic_blocks.parser import format_fenced_body
from demon_lucy.modules.graph.data import compile_search_pattern, load_graph_data
from demon_lucy.modules.graph.params import (
    GraphParams,
    GraphView,
    normalize_graph_params,
)
from demon_lucy.modules.graph.render import (
    build_series,
    render_markdown_graph,
    render_text_graph,
)


def render_graph(
    params: GraphParams,
    *,
    target_path: str,
) -> str:
    source_path = resolve_file_source_path(
        source=params.source,
        target_path=target_path,
    )
    try:
        search_pattern = compile_search_pattern(
            params.pattern,
            regex=params.is_regex,
        )
    except Exception as exc:
        raise ValueError(f"invalid graph pattern: {exc}") from exc

    data_result = load_graph_data(source_path, search_pattern)
    if not data_result.ok:
        detail = f": {data_result.error_detail}" if data_result.error_detail else ""
        raise ValueError(f"{data_result.error_reason}{detail}")
    series = build_series(data_result.counts_by_date, params.period)
    if series is None:
        raise ValueError("no graph dates found")

    if params.view is GraphView.ASCII:
        text_graph = render_text_graph(series=series)
        return format_fenced_body(text_graph, info="text")

    markdown_graph = render_markdown_graph(series=series)
    if params.view is GraphView.MD:
        return markdown_graph
    raise ValueError(f"unsupported graph view: {params.view.value}")


def render_graph_dynamic_block(block: DynamicBlock, target_path: str) -> str:
    params = normalize_graph_params(block.arg, block.params)
    return render_graph(params, target_path=target_path)
