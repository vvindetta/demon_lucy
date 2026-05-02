from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from lucy_notes_manager.modules.plasma_sync.markdown_codec import (
    _doc_hash,
    _doc_to_md,
    _md_to_doc,
)
from lucy_notes_manager.modules.plasma_sync.mirror_mapper import (
    _apply_mirror_items_to_doc,
    _bold_items_to_plasma_html,
    _extract_bold_items_from_doc,
    _items_hash,
    _mirror_html_to_items,
)
from lucy_notes_manager.modules.plasma_sync.model import DocLine, _hash_text, _normalize_md
from lucy_notes_manager.modules.plasma_sync.plasma_html_codec import (
    _doc_to_plasma_html,
    _html_to_doc,
)


@dataclass(frozen=True)
class SyncState:
    doc_hash: Optional[str]
    bold_items_hash: Optional[str]
    css_style: Optional[bool]


@dataclass(frozen=True)
class SyncPlan:
    next_state: SyncState
    widget_html: Optional[str] = None
    markdown_text: Optional[str] = None
    mirror_html: Optional[str] = None
    missing_markdown: bool = False


def bootstrap_state(markdown_text: str, main_html_text: str) -> SyncState:
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


def _plan_mirror_sync(
    *,
    doc: list[DocLine],
    mirror_html_current: Optional[str],
    previous_bold_items_hash: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    if mirror_html_current is None:
        return None, previous_bold_items_hash

    items = _extract_bold_items_from_doc(doc)
    items_hash = _items_hash(items)
    if previous_bold_items_hash == items_hash:
        return None, previous_bold_items_hash

    mirror_html_new = _bold_items_to_plasma_html(items)
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
    if markdown_text == "" and not markdown_exists:
        return SyncPlan(next_state=state, missing_markdown=True)

    doc = _md_to_doc(_normalize_md(markdown_text))
    doc_hash = _doc_hash(doc)

    widget_html_out: Optional[str] = None
    if state.doc_hash != doc_hash:
        candidate = _doc_to_plasma_html(doc, css_style=css_style)
        if candidate != widget_html_current:
            widget_html_out = candidate
    else:
        widget_html_out = _plan_widget_render_mode(
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
    if not widget_exists:
        return SyncPlan(next_state=state)

    doc = _html_to_doc(widget_html_current)
    doc_hash = _doc_hash(doc)

    markdown_out: Optional[str] = None
    if state.doc_hash != doc_hash:
        candidate = _doc_to_md(doc)
        if candidate != markdown_text_current:
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
    if mirror_html_current is None or not mirror_exists:
        return SyncPlan(next_state=state)

    items = _mirror_html_to_items(mirror_html_current)
    items_hash = _items_hash(items)
    if state.bold_items_hash == items_hash:
        return SyncPlan(next_state=state)

    main_doc = _html_to_doc(widget_html_current)
    new_doc = _apply_mirror_items_to_doc(main_doc, items)
    new_doc_hash = _doc_hash(new_doc)

    widget_html_out: Optional[str] = None
    markdown_out: Optional[str] = None

    if state.doc_hash != new_doc_hash:
        candidate_widget = _doc_to_plasma_html(new_doc, css_style=css_style)
        if candidate_widget != widget_html_current:
            widget_html_out = candidate_widget

        candidate_markdown = _doc_to_md(new_doc)
        if candidate_markdown != markdown_text_current:
            markdown_out = candidate_markdown

    mirror_norm = _bold_items_to_plasma_html(items)
    mirror_out: Optional[str] = None
    if mirror_norm != mirror_html_current:
        mirror_out = mirror_norm

    widget_baseline = widget_html_out if widget_html_out is not None else widget_html_current
    widget_render_out = _plan_widget_render_mode(
        widget_html_current=widget_baseline,
        css_style=css_style,
        previous_css_style=state.css_style,
    )
    if widget_render_out is not None:
        widget_html_out = widget_render_out

    next_doc_hash = state.doc_hash
    if state.doc_hash != new_doc_hash:
        next_doc_hash = new_doc_hash

    return SyncPlan(
        next_state=SyncState(
            doc_hash=next_doc_hash,
            bold_items_hash=items_hash,
            css_style=css_style,
        ),
        widget_html=widget_html_out,
        markdown_text=markdown_out,
        mirror_html=mirror_out,
    )
