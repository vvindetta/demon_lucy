from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from demon_lucy.modules.plasma_widget.markdown_codec import (
    _doc_hash,
    _doc_to_md,
    _md_to_doc,
)
from demon_lucy.modules.plasma_widget.mirror_mapper import (
    _apply_mirror_lines_to_doc,
    _bold_lines_to_plasma_html,
    _extract_bold_items_from_doc,
    _items_hash,
    _merge_items_into_mirror_lines,
    _mirror_html_to_lines,
)
from demon_lucy.modules.plasma_widget.model import (
    DocLine,
    _hash_text,
    _normalize_md,
)
from demon_lucy.modules.plasma_widget.plasma_html_codec import (
    _doc_to_plasma_html,
    _html_to_doc,
)

"""
Three-file Plasma sync contract.

- The Markdown file is the text serialization. Bold is stored as **text**.
- The MAIN Plasma widget is rich Plasma/QTextDocument HTML for the same document,
  not a raw Markdown source view. Markdown **text** and HTML font-weight:bold are
  the same semantic bold segment after parsing into DocLine.
- The optional BOLD mirror widget is an index of only semantic bold text. It may
  be rebuilt from Markdown **segments or from MAIN HTML bold spans, and edits in
  the mirror replace/delete/append bold segments in the Markdown-backed document
  model. MAIN HTML is treated as a target for mirror edits, not as the structural
  source, because Plasma can briefly write incomplete widget snapshots.

All three directions must work:
Markdown -> MAIN + mirror, MAIN -> Markdown + mirror, mirror -> MAIN + Markdown.
Plans compare parsed Plasma documents before deciding writes so equivalent Qt
HTML serializations do not trigger a write loop. SyncState is only remembered
context, not proof that widget files are already in sync. In particular, the
first event after bootstrap must still write a missing or stale MAIN widget even
when state was initialized from the Markdown file.
"""


@dataclass(frozen=True)
class SyncState:
    """Remember the last semantic document state for one Markdown/MAIN/mirror set."""

    doc_hash: Optional[str]
    bold_items_hash: Optional[str]
    css_style: Optional[bool]


@dataclass(frozen=True)
class SyncPlan:
    """Concrete file writes needed to make the other two files match one edit."""

    next_state: SyncState
    widget_html: Optional[str] = None
    markdown_text: Optional[str] = None
    mirror_html: Optional[str] = None
    missing_markdown: bool = False
    blocked_empty_source: Optional[str] = None
    blocked_shrinking_source: Optional[str] = None


def _markdown_text_doc_hash(markdown_text: str) -> str:
    """Hash Markdown by semantic DocLine content, ignoring harmless text formatting."""

    return _doc_hash(_md_to_doc(_normalize_md(markdown_text)))


def _doc_has_semantic_content(doc: list[DocLine]) -> bool:
    return bool(_doc_to_md(doc).strip())


def _markdown_has_semantic_content(markdown_text: str) -> bool:
    return _doc_has_semantic_content(_md_to_doc(_normalize_md(markdown_text)))


def _semantic_content_stats(doc: list[DocLine]) -> tuple[int, int]:
    text = _doc_to_md(doc).strip()
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return len(lines), sum(len(line) for line in lines)


def _source_doc_is_probable_truncated_snapshot(
    source_doc: list[DocLine],
    markdown_text_current: str,
) -> bool:
    current_doc = _md_to_doc(_normalize_md(markdown_text_current))
    current_lines, current_chars = _semantic_content_stats(current_doc)
    source_lines, source_chars = _semantic_content_stats(source_doc)

    if current_lines < 4 or current_chars < 80:
        return False
    if source_lines >= current_lines:
        return False

    return source_lines <= max(1, current_lines // 2) and source_chars <= int(
        current_chars * 0.70
    )


def _plan_restore_from_markdown(
    *,
    state: SyncState,
    markdown_text_current: str,
    widget_html_current: str,
    mirror_html_current: Optional[str],
    css_style: bool,
    blocked_empty_source: Optional[str] = None,
    blocked_shrinking_source: Optional[str] = None,
) -> SyncPlan:
    doc = _md_to_doc(_normalize_md(markdown_text_current))
    doc_hash = _doc_hash(doc)

    widget_html_out = _plan_widget_sync(
        doc=doc,
        widget_html_current=widget_html_current,
        css_style=css_style,
        previous_css_style=state.css_style,
    )

    mirror_html_out, bold_items_hash = _plan_mirror_sync(
        doc=doc,
        mirror_html_current=mirror_html_current,
        previous_bold_items_hash=state.bold_items_hash,
    )

    return SyncPlan(
        next_state=SyncState(
            doc_hash=doc_hash,
            bold_items_hash=bold_items_hash,
            css_style=css_style,
        ),
        widget_html=widget_html_out,
        mirror_html=mirror_html_out,
        blocked_empty_source=blocked_empty_source,
        blocked_shrinking_source=blocked_shrinking_source,
    )


def bootstrap_state(markdown_text: str, main_html_text: str) -> SyncState:
    """Initialize semantic state from Markdown first, then MAIN HTML if needed."""

    if markdown_text.strip():
        doc = _md_to_doc(_normalize_md(markdown_text))
        return SyncState(
            doc_hash=_doc_hash(doc),
            bold_items_hash=_items_hash(_extract_bold_items_from_doc(doc)),
            css_style=None,
        )

    if main_html_text.strip():
        doc = _html_to_doc(main_html_text)
        return SyncState(
            doc_hash=_doc_hash(doc),
            bold_items_hash=_items_hash(_extract_bold_items_from_doc(doc)),
            css_style=None,
        )

    empty_hash = _hash_text("")
    return SyncState(
        doc_hash=empty_hash,
        bold_items_hash=empty_hash,
        css_style=None,
    )


def _plan_widget_render_mode(
    *,
    widget_html_current: str,
    css_style: bool,
    previous_css_style: Optional[bool],
) -> Optional[str]:
    if previous_css_style is not None and previous_css_style == css_style:
        return None
    if not widget_html_current.strip():
        return None

    doc = _html_to_doc(widget_html_current)
    widget_html_new = _doc_to_plasma_html(doc, css_style=css_style)
    if widget_html_new == widget_html_current:
        return None
    return widget_html_new


def _plan_widget_sync(
    *,
    doc: list[DocLine],
    widget_html_current: str,
    css_style: bool,
    previous_css_style: Optional[bool],
) -> Optional[str]:
    """Update MAIN only for semantic/render-mode drift, not Qt HTML formatting."""

    widget_html_new = _doc_to_plasma_html(doc, css_style=css_style)
    if not widget_html_current.strip():
        return widget_html_new

    current_doc = _html_to_doc(widget_html_current)
    expected_render_doc = _html_to_doc(widget_html_new)
    if current_doc != expected_render_doc:
        return widget_html_new

    return _plan_widget_render_mode(
        widget_html_current=widget_html_current,
        css_style=css_style,
        previous_css_style=previous_css_style,
    )


def _plan_mirror_sync(
    *,
    doc: list[DocLine],
    mirror_html_current: Optional[str],
    previous_bold_items_hash: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Build the BOLD mirror from semantic bold items while preserving separators."""

    if mirror_html_current is None:
        return None, previous_bold_items_hash

    items = _extract_bold_items_from_doc(doc)
    items_hash = _items_hash(items)
    current_lines = _mirror_html_to_lines(mirror_html_current)
    mirror_lines = _merge_items_into_mirror_lines(
        current_lines,
        items,
    )
    if mirror_lines == current_lines and mirror_html_current.strip():
        return None, items_hash

    mirror_html_new = _bold_lines_to_plasma_html(mirror_lines)
    if mirror_html_new == mirror_html_current:
        return None, items_hash
    return mirror_html_new, items_hash


def plan_from_markdown(
    *,
    state: SyncState,
    markdown_text: str,
    markdown_exists: bool,
    widget_html_current: str,
    mirror_html_current: Optional[str],
    css_style: bool,
) -> SyncPlan:
    """Plan Markdown -> MAIN + mirror.

    MAIN is generated as rich Plasma HTML from Markdown, not as literal source
    text. Compare parsed documents and the configured render mode so a
    missing/stale widget is repaired without rewriting equivalent Qt HTML.
    """

    if markdown_text == "" and not markdown_exists:
        return SyncPlan(next_state=state, missing_markdown=True)

    doc = _md_to_doc(_normalize_md(markdown_text))
    doc_hash = _doc_hash(doc)

    widget_html_out = _plan_widget_sync(
        doc=doc,
        widget_html_current=widget_html_current,
        css_style=css_style,
        previous_css_style=state.css_style,
    )

    mirror_html_out, bold_items_hash = _plan_mirror_sync(
        doc=doc,
        mirror_html_current=mirror_html_current,
        previous_bold_items_hash=state.bold_items_hash,
    )

    return SyncPlan(
        next_state=SyncState(
            doc_hash=doc_hash,
            bold_items_hash=bold_items_hash,
            css_style=css_style,
        ),
        widget_html=widget_html_out,
        mirror_html=mirror_html_out,
    )


def plan_from_main_plasma(
    *,
    state: SyncState,
    widget_html_current: str,
    widget_exists: bool,
    markdown_text_current: str,
    mirror_html_current: Optional[str],
    css_style: bool,
) -> SyncPlan:
    """Plan MAIN -> Markdown + mirror.

    MAIN HTML bold and Markdown **bold** are treated as the same semantic bold
    segments. Markdown is only rewritten when the parsed MAIN document differs
    semantically, so a MAIN event does not churn the file just to canonicalize
    whitespace such as a trailing newline.
    """

    if not widget_exists:
        return SyncPlan(next_state=state)

    doc = _html_to_doc(widget_html_current)
    doc_hash = _doc_hash(doc)

    if not _doc_has_semantic_content(doc) and _markdown_has_semantic_content(
        markdown_text_current
    ):
        return _plan_restore_from_markdown(
            state=state,
            markdown_text_current=markdown_text_current,
            widget_html_current=widget_html_current,
            mirror_html_current=mirror_html_current,
            css_style=css_style,
            blocked_empty_source="main_plasma",
        )

    if _source_doc_is_probable_truncated_snapshot(doc, markdown_text_current):
        return _plan_restore_from_markdown(
            state=state,
            markdown_text_current=markdown_text_current,
            widget_html_current=widget_html_current,
            mirror_html_current=mirror_html_current,
            css_style=css_style,
            blocked_shrinking_source="main_plasma",
        )

    markdown_out: Optional[str] = None
    candidate = _doc_to_md(doc)
    if _markdown_text_doc_hash(markdown_text_current) != doc_hash:
        markdown_out = candidate

    mirror_html_out, bold_items_hash = _plan_mirror_sync(
        doc=doc,
        mirror_html_current=mirror_html_current,
        previous_bold_items_hash=state.bold_items_hash,
    )

    widget_html_out = _plan_widget_render_mode(
        widget_html_current=widget_html_current,
        css_style=css_style,
        previous_css_style=state.css_style,
    )

    return SyncPlan(
        next_state=SyncState(
            doc_hash=doc_hash,
            bold_items_hash=bold_items_hash,
            css_style=css_style,
        ),
        widget_html=widget_html_out,
        markdown_text=markdown_out,
        mirror_html=mirror_html_out,
    )


def plan_from_bold_mirror(
    *,
    state: SyncState,
    mirror_html_current: Optional[str],
    mirror_exists: bool,
    widget_html_current: str,
    markdown_text_current: str,
    css_style: bool,
) -> SyncPlan:
    """Plan mirror -> MAIN + Markdown.

    The mirror contains only semantic bold text. Editing it updates matching
    bold entries in the current Markdown document; missing old entries are
    deleted and new mirror rows append new bold paragraphs. MAIN HTML is only a
    target here, so a transient partial Plasma HTML snapshot cannot truncate the
    Markdown note.
    """

    if mirror_html_current is None or not mirror_exists:
        return SyncPlan(next_state=state)

    mirror_lines = _mirror_html_to_lines(mirror_html_current)
    if not mirror_lines and _markdown_has_semantic_content(markdown_text_current):
        return _plan_restore_from_markdown(
            state=state,
            markdown_text_current=markdown_text_current,
            widget_html_current=widget_html_current,
            mirror_html_current=mirror_html_current,
            css_style=css_style,
            blocked_empty_source="bold_mirror",
        )

    markdown_doc = _md_to_doc(_normalize_md(markdown_text_current))
    new_doc = _apply_mirror_lines_to_doc(markdown_doc, mirror_lines)
    new_doc_hash = _doc_hash(new_doc)

    if not _doc_has_semantic_content(new_doc) and _markdown_has_semantic_content(
        markdown_text_current
    ):
        return _plan_restore_from_markdown(
            state=state,
            markdown_text_current=markdown_text_current,
            widget_html_current=widget_html_current,
            mirror_html_current=mirror_html_current,
            css_style=css_style,
            blocked_empty_source="bold_mirror",
        )

    if _source_doc_is_probable_truncated_snapshot(new_doc, markdown_text_current):
        return _plan_restore_from_markdown(
            state=state,
            markdown_text_current=markdown_text_current,
            widget_html_current=widget_html_current,
            mirror_html_current=mirror_html_current,
            css_style=css_style,
            blocked_shrinking_source="bold_mirror",
        )

    markdown_out: Optional[str] = None

    widget_html_out = _plan_widget_sync(
        doc=new_doc,
        widget_html_current=widget_html_current,
        css_style=css_style,
        previous_css_style=state.css_style,
    )

    candidate_markdown = _doc_to_md(new_doc)
    if _markdown_text_doc_hash(markdown_text_current) != new_doc_hash:
        markdown_out = candidate_markdown

    new_items = _extract_bold_items_from_doc(new_doc)
    next_bold_items_hash = _items_hash(new_items)

    next_doc_hash = state.doc_hash
    if state.doc_hash != new_doc_hash:
        next_doc_hash = new_doc_hash

    return SyncPlan(
        next_state=SyncState(
            doc_hash=next_doc_hash,
            bold_items_hash=next_bold_items_hash,
            css_style=css_style,
        ),
        widget_html=widget_html_out,
        markdown_text=markdown_out,
    )
