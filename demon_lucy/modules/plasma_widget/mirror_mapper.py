from __future__ import annotations

from difflib import SequenceMatcher
from typing import List, Optional

from demon_lucy.modules.plasma_widget.model import (
    DocLine,
    _hash_text,
    _segs_has_bold,
    _segs_plain,
)
from demon_lucy.modules.plasma_widget.plasma_html_codec import (
    _doc_to_plasma_html,
    _html_to_doc,
)

_MAX_MIRROR_BLANK_LINES = 3


def _dedupe_consecutive(items: List[str]) -> List[str]:
    """
    Plasma/QTextDocument sometimes keeps duplicated <p> blocks that may be rendered
    as a single visible line. If we apply mirror->main mapping without de-duping,
    duplicates spam MAIN with repeated identical lines.

    Rule: remove empty strings and consecutive duplicates after normalize+strip.
    """
    out: List[str] = []
    prev: Optional[str] = None

    for raw in items:
        normalized = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            continue
        if prev is not None and normalized == prev:
            continue
        out.append(normalized)
        prev = normalized

    return out


def _normalize_mirror_lines(
    lines: List[str], *, max_blank_lines: int = _MAX_MIRROR_BLANK_LINES
) -> List[str]:
    out: List[str] = []
    prev_text: Optional[str] = None
    blank_run = 0

    for raw in lines:
        normalized = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            blank_run += 1
            if blank_run <= max_blank_lines:
                out.append("")
            prev_text = None
            continue

        blank_run = 0
        if prev_text is not None and normalized == prev_text:
            continue
        out.append(normalized)
        prev_text = normalized

    return out


def _items_from_mirror_lines(lines: List[str]) -> List[str]:
    return [line for line in lines if line.strip()]


def _line_bold_text(dl: DocLine) -> str:
    return "".join(text for text, is_bold in dl.segs if is_bold).strip()


def _replace_line_bold_text(dl: DocLine, text: str) -> DocLine:
    segs: List[tuple[str, bool]] = []
    replaced = False
    for seg_text, is_bold in dl.segs:
        if is_bold:
            if not replaced:
                segs.append((text, True))
                replaced = True
            continue
        segs.append((seg_text, False))

    if not replaced:
        segs.append((text, True))
    return DocLine(kind=dl.kind, state=dl.state, segs=segs)


def _extract_bold_items_from_doc(doc: List[DocLine]) -> List[str]:
    """
    Mirror rule:
    - DO NOT cut "-", "- [ ]", "- [x]".
    - Mirror shows the visible bold text as-is.

    How it works:
    - For paragraph lines: take bold fragments.
    - For list-item lines: also take bold fragments (without adding/removing prefixes).
      (If you made the whole line bold in plain mode, prefix is inside the text already,
       because it was edited as text inside <p>, so it will appear in mirror.)
    """
    items: List[str] = []
    for dl in doc:
        joined = _line_bold_text(dl)
        if joined:
            items.append(joined)

    # Prevent MAIN->mirror from preserving hidden duplicated lines
    return _dedupe_consecutive(items)


def _items_hash(items: List[str]) -> str:
    norm = [it.replace("\r\n", "\n").replace("\r", "\n").strip() for it in items]
    norm = [it for it in norm if it]
    return _hash_text("\n".join(norm))


def _bold_items_to_plasma_html(items: List[str]) -> str:
    return _bold_lines_to_plasma_html(items)


def _bold_lines_to_plasma_html(lines: List[str]) -> str:
    doc: List[DocLine] = []
    for line in _normalize_mirror_lines(lines):
        if line:
            doc.append(DocLine(kind="p", state=None, segs=[(line, True)]))
        else:
            doc.append(DocLine(kind="p", state=None, segs=[]))
    # mirror always plain (no checkbox glyphs)
    return _doc_to_plasma_html(doc, css_style=False)


def _mirror_html_to_lines(mirror_html: str) -> List[str]:
    doc = _html_to_doc(mirror_html, trim_empty_edges=False)
    lines: List[str] = []
    for dl in doc:
        lines.append(_segs_plain(dl.segs).strip())
    return _normalize_mirror_lines(lines)


def _mirror_html_to_items(mirror_html: str) -> List[str]:
    return _items_from_mirror_lines(_mirror_html_to_lines(mirror_html))


def _merge_items_into_mirror_lines(
    existing_lines: List[str],
    items: List[str],
) -> List[str]:
    cleaned_items = _dedupe_consecutive([it.strip() for it in items if it.strip()])
    if not existing_lines:
        return cleaned_items

    out: List[str] = []
    item_index = 0
    for line in existing_lines:
        if not line:
            out.append("")
            continue
        if item_index < len(cleaned_items):
            out.append(cleaned_items[item_index])
            item_index += 1

    while item_index < len(cleaned_items):
        out.append(cleaned_items[item_index])
        item_index += 1

    return _normalize_mirror_lines(out)


def _map_target_items_to_old_positions(
    old_items: List[str],
    target_items: List[str],
) -> List[Optional[int]]:
    mapping: List[Optional[int]] = [None] * len(target_items)
    matcher = SequenceMatcher(a=old_items, b=target_items, autojunk=False)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(j2 - j1):
                mapping[j1 + offset] = i1 + offset
            continue

        if tag == "replace":
            paired = min(i2 - i1, j2 - j1)
            for offset in range(paired):
                mapping[j1 + offset] = i1 + offset

    return mapping


def _append_nonbold_between(
    out: List[DocLine],
    doc: List[DocLine],
    start: int,
    stop: int,
) -> None:
    for dl in doc[start:stop]:
        if not _segs_has_bold(dl.segs):
            out.append(dl)


def _next_mapped_doc_position(
    mapping: List[Optional[int]],
    bold_positions: List[int],
    start_item_index: int,
) -> Optional[int]:
    for old_item_index in mapping[start_item_index:]:
        if old_item_index is not None:
            return bold_positions[old_item_index]
    return None


def _append_pending_nonbold(
    out: List[DocLine],
    doc: List[DocLine],
    old_cursor: int,
    stop: int,
) -> int:
    _append_nonbold_between(out, doc, old_cursor, stop)
    return stop


def _apply_mirror_items_to_doc(
    main_doc: List[DocLine], items: List[str]
) -> List[DocLine]:
    return _apply_mirror_lines_to_doc(main_doc, items)


def _apply_mirror_lines_to_doc(
    main_doc: List[DocLine], lines: List[str]
) -> List[DocLine]:
    """
    Line-safe mapping:
    - existing bold lines are matched by content when possible
    - changed lines reuse the old slot by order
    - inserted lines stay where they were inserted in mirror
    - blank lines from mirror are preserved, capped to small runs
    - deleted mirror items remove only the corresponding bold line, not nearby plain text
    """
    mirror_lines = _normalize_mirror_lines(lines)
    target_items = _items_from_mirror_lines(mirror_lines)

    bold_positions: List[int] = []
    old_items: List[str] = []
    for index, dl in enumerate(main_doc):
        if not _segs_has_bold(dl.segs):
            continue
        bold_positions.append(index)
        old_items.append(_line_bold_text(dl))

    mapping = _map_target_items_to_old_positions(old_items, target_items)

    out: List[DocLine] = []
    old_cursor = 0
    item_index = 0
    for line in mirror_lines:
        if not line:
            next_position = _next_mapped_doc_position(
                mapping, bold_positions, item_index
            )
            old_cursor = _append_pending_nonbold(
                out,
                main_doc,
                old_cursor,
                next_position if next_position is not None else len(main_doc),
            )
            out.append(DocLine(kind="p", state=None, segs=[]))
            continue

        old_item_index = mapping[item_index]
        if old_item_index is None:
            next_position = _next_mapped_doc_position(
                mapping, bold_positions, item_index + 1
            )
            old_cursor = _append_pending_nonbold(
                out,
                main_doc,
                old_cursor,
                next_position if next_position is not None else len(main_doc),
            )
            out.append(DocLine(kind="p", state=None, segs=[(line, True)]))
            item_index += 1
            continue

        position = bold_positions[old_item_index]
        _append_nonbold_between(out, main_doc, old_cursor, position)
        old_line = main_doc[position]
        out.append(_replace_line_bold_text(old_line, line))
        old_cursor = position + 1
        item_index += 1

    _append_nonbold_between(out, main_doc, old_cursor, len(main_doc))
    return out
