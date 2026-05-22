from __future__ import annotations

from typing import List, Optional

from lucy_notes_manager.modules.plasma_widget.model import (
    DocLine,
    _hash_text,
    _segs_has_bold,
    _segs_plain,
)
from lucy_notes_manager.modules.plasma_widget.plasma_html_codec import (
    _doc_to_plasma_html,
    _html_to_doc,
)


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
        bold_fragments = [text for (text, is_bold) in dl.segs if is_bold and text]
        joined = "".join(bold_fragments).strip()
        if joined:
            items.append(joined)

    # Prevent MAIN->mirror from preserving hidden duplicated lines
    return _dedupe_consecutive(items)


def _items_hash(items: List[str]) -> str:
    norm = [it.replace("\r\n", "\n").replace("\r", "\n").strip() for it in items]
    norm = [it for it in norm if it]
    return _hash_text("\n".join(norm))


def _bold_items_to_plasma_html(items: List[str]) -> str:
    doc = [
        DocLine(kind="p", state=None, segs=[(it.strip(), True)])
        for it in items
        if it.strip()
    ]
    # mirror always plain (no checkbox glyphs)
    return _doc_to_plasma_html(doc, css_style=False)


def _mirror_html_to_items(mirror_html: str) -> List[str]:
    doc = _html_to_doc(mirror_html)
    items: List[str] = []
    for dl in doc:
        s = _segs_plain(dl.segs).strip()
        if s:
            items.append(s)

    # IMPORTANT: prevent hidden QTextDocument duplicates from spamming MAIN
    return _dedupe_consecutive(items)


def _apply_mirror_items_to_doc(
    main_doc: List[DocLine], items: List[str]
) -> List[DocLine]:
    """
    Line-safe mapping:
    - every line that contains ANY bold in MAIN consumes exactly 1 item from mirror
    - we replace the whole line content with that item (fully bold), preserving line kind/state
    - if mirror has more items, append them as new bold paragraphs
    - if mirror has fewer, remaining bold lines are removed
    """
    cleaned = [it.strip() for it in items if it.strip()]
    cleaned = _dedupe_consecutive(cleaned)

    out: List[DocLine] = []
    index = 0

    for dl in main_doc:
        if not _segs_has_bold(dl.segs):
            out.append(dl)
            continue

        if index < len(cleaned):
            out.append(
                DocLine(kind=dl.kind, state=dl.state, segs=[(cleaned[index], True)])
            )
            index += 1
        else:
            # mirror source has no item for this bold line anymore:
            # treat this as deletion from mirror and drop the line.
            continue

    # collect all existing bold lines after replacement
    existing_bold_lines = {
        _segs_plain(dl.segs).strip()
        for dl in out
        if _segs_has_bold(dl.segs) and _segs_plain(dl.segs).strip()
    }

    # append only truly new items
    while index < len(cleaned):
        candidate = cleaned[index].strip()
        if candidate and candidate not in existing_bold_lines:
            out.append(DocLine(kind="p", state=None, segs=[(candidate, True)]))
            existing_bold_lines.add(candidate)
        index += 1

    return out
