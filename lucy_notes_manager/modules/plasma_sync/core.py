from __future__ import annotations

from lucy_notes_manager.modules.plasma_sync.markdown_codec import (
    _doc_hash,
    _doc_to_md,
    _md_line_to_segs,
    _md_to_doc,
)
from lucy_notes_manager.modules.plasma_sync.mirror_mapper import (
    _apply_mirror_items_to_doc,
    _bold_items_to_plasma_html,
    _dedupe_consecutive,
    _extract_bold_items_from_doc,
    _items_hash,
    _mirror_html_to_items,
)
from lucy_notes_manager.modules.plasma_sync.model import (
    DocLine,
    _hash_text,
    _merge_segs,
    _normalize_md,
    _normalize_newlines,
    _segs_has_bold,
    _segs_plain,
    _trim_trailing_spaces_per_line,
)
from lucy_notes_manager.modules.plasma_sync.plasma_html_codec import (
    _PlasmaDocParser,
    _doc_to_plasma_html,
    _html_to_doc,
    _style_is_bold,
)

__all__ = [
    "DocLine",
    "_PlasmaDocParser",
    "_apply_mirror_items_to_doc",
    "_bold_items_to_plasma_html",
    "_dedupe_consecutive",
    "_doc_hash",
    "_doc_to_md",
    "_doc_to_plasma_html",
    "_extract_bold_items_from_doc",
    "_hash_text",
    "_html_to_doc",
    "_items_hash",
    "_md_line_to_segs",
    "_md_to_doc",
    "_merge_segs",
    "_mirror_html_to_items",
    "_normalize_md",
    "_normalize_newlines",
    "_segs_has_bold",
    "_segs_plain",
    "_style_is_bold",
    "_trim_trailing_spaces_per_line",
]
