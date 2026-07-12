from __future__ import annotations

import os
from pathlib import Path

from demon_lucy.lib.file_time import (
    content_change_timestamp,
    format_local_timestamp,
    format_timestamp_age,
)
from demon_lucy.lib.path import (
    canonical_path,
    find_parent_git_repo,
    path_is_inside,
)
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


def resolve_graph_source_path(*, source: str, target_path: str) -> str:
    if source.startswith("~"):
        raise ValueError("graph source must not use '~'")

    target_dir = os.path.dirname(canonical_path(target_path))
    repo_root = find_parent_git_repo(target_path)
    allowed_root = repo_root or target_dir
    source_path = Path(source)
    if not source_path.is_absolute():
        source_path = Path(target_dir) / source_path
    resolved = canonical_path(str(source_path))
    if not path_is_inside(resolved, allowed_root):
        raise ValueError("graph source is outside the allowed root")
    return resolved


def render_graph(
    params: GraphParams,
    *,
    target_path: str,
) -> str:
    source_path = resolve_graph_source_path(
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

    timestamp = content_change_timestamp(source_path)
    if timestamp is None:
        raise OSError(f"cannot read graph source timestamp: {source_path}")
    updated_at = format_local_timestamp(timestamp)
    updated_ago = format_timestamp_age(timestamp)

    if params.view is GraphView.ASCII:
        text_graph = render_text_graph(
            series=series,
            updated_at=updated_at,
            updated_ago=updated_ago,
        )
        return format_fenced_body(text_graph, info="text")

    markdown_graph = render_markdown_graph(
        series=series,
        updated_at=updated_at,
        updated_ago=updated_ago,
    )
    if params.view is GraphView.MARKDOWN:
        return markdown_graph
    if params.view is GraphView.MARKDOWN_CODE:
        return format_fenced_body(markdown_graph, info="markdown")
    raise ValueError(f"unsupported graph view: {params.view.value}")


def render_graph_dynamic_block(block: DynamicBlock, target_path: str) -> str:
    params = normalize_graph_params(block.arg, block.params)
    return render_graph(params, target_path=target_path)
