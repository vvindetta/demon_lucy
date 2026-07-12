from __future__ import annotations

from demon_lucy.lib.dynamic_blocks.model import DynamicBlock
from demon_lucy.lib.path import canonical_path, resolve_file_source_path
from demon_lucy.modules.include.params import normalize_include_source


def render_file(source: str, *, target_path: str) -> str:
    source_path = resolve_file_source_path(
        source=source,
        target_path=target_path,
    )
    if source_path == canonical_path(target_path):
        raise ValueError("include source must differ from the target file")

    try:
        with open(source_path, "r", encoding="utf-8", newline="") as handle:
            text = handle.read()
    except UnicodeDecodeError as exc:
        raise ValueError("include source is not UTF-8") from exc

    return "".join(f"\t{line}" for line in text.splitlines(keepends=True))


def render_include_dynamic_block(block: DynamicBlock, target_path: str) -> str:
    source = normalize_include_source(block.params)
    return render_file(source, target_path=target_path)
