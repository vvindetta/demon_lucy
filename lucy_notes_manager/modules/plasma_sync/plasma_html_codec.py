from __future__ import annotations

import html
from html.parser import HTMLParser
from typing import List, Optional, Tuple

from lucy_notes_manager.modules.plasma_sync.model import (
    DocLine,
    _merge_segs,
    _segs_plain,
)


def _style_is_bold(style: str) -> bool:
    s = (style or "").lower().replace(" ", "")
    if "font-weight:bold" in s:
        return True
    if "font-weight:" in s:
        try:
            idx = s.rfind("font-weight:")
            val = s[idx + len("font-weight:") :]
            val = val.split(";")[0]
            return int(val) >= 600
        except Exception:
            return False
    return False


class _PlasmaDocParser(HTMLParser):
    """
    Robust against nested blocks like: <li ...><p ...>text</p></li>
    - top-level <li> produces one DocLine(kind="li")
    - top-level <p> produces one DocLine(kind="p")
    - <p> inside <li> is treated as inline container, not a separate line
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_body = False
        self._in_li_depth = 0

        self._cur: Optional[DocLine] = None

        self._bold_depth = 0
        self._span_bold_stack: List[bool] = []
        self._font_bold_stack: List[bool] = []

        self._doc: List[DocLine] = []

    def _finalize(self) -> None:
        if self._cur is None:
            return
        self._cur.segs = _merge_segs(self._cur.segs)
        self._doc.append(self._cur)
        self._cur = None

    def _ensure_cur(self, kind: str, state: Optional[str]) -> None:
        if self._cur is None:
            self._cur = DocLine(kind=kind, state=state, segs=[])

    def _append(self, text: str) -> None:
        if self._cur is None:
            return
        if not text:
            return
        self._cur.segs.append((text, self._bold_depth > 0))

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        if tag == "body":
            self._in_body = True
            return
        if not self._in_body:
            return

        if tag == "li":
            self._finalize()
            cls = ""
            for k, v in attrs:
                if k.lower() == "class" and isinstance(v, str):
                    cls = v.lower()
                    break
            state = None
            if "unchecked" in cls:
                state = "unchecked"
            elif "checked" in cls:
                state = "checked"
            self._ensure_cur("li", state)
            self._in_li_depth += 1
            return

        if tag == "p":
            if self._in_li_depth == 0:
                self._finalize()
                self._ensure_cur("p", None)
            return

        if tag == "br":
            return

        if tag in ("b", "strong"):
            self._bold_depth += 1
            return

        if tag == "span":
            style = ""
            for k, v in attrs:
                if k.lower() == "style" and isinstance(v, str):
                    style = v
                    break
            is_b = _style_is_bold(style)
            self._span_bold_stack.append(is_b)
            if is_b:
                self._bold_depth += 1
            return

        if tag == "font":
            style = ""
            for k, v in attrs:
                if k.lower() == "style" and isinstance(v, str):
                    style = v
                    break
            is_b = _style_is_bold(style)
            self._font_bold_stack.append(is_b)
            if is_b:
                self._bold_depth += 1
            return

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag == "body":
            self._finalize()
            self._in_body = False
            self._in_li_depth = 0
            return
        if not self._in_body:
            return

        if tag == "li":
            self._in_li_depth = max(0, self._in_li_depth - 1)
            if self._in_li_depth == 0:
                self._finalize()
            return

        if tag == "p":
            if self._in_li_depth == 0:
                self._finalize()
            return

        if tag in ("b", "strong"):
            self._bold_depth = max(0, self._bold_depth - 1)
            return

        if tag == "span":
            if self._span_bold_stack:
                was_bold = self._span_bold_stack.pop()
                if was_bold:
                    self._bold_depth = max(0, self._bold_depth - 1)
            return

        if tag == "font":
            if self._font_bold_stack:
                was_bold = self._font_bold_stack.pop()
                if was_bold:
                    self._bold_depth = max(0, self._bold_depth - 1)
            return

    def handle_data(self, data):
        if not self._in_body or not isinstance(data, str):
            return
        if self._cur is None and data.strip() == "":
            return
        text = html.unescape(data)
        if self._cur is None and text.strip() == "":
            return
        if self._cur is None:
            self._ensure_cur("p", None)
        self._append(text)

    def get_doc(self) -> List[DocLine]:
        self._finalize()

        doc = self._doc[:]
        while doc and doc[0].kind == "p" and _segs_plain(doc[0].segs).strip() == "":
            doc.pop(0)
        while doc and doc[-1].kind == "p" and _segs_plain(doc[-1].segs).strip() == "":
            doc.pop()
        return doc


def _html_to_doc(html_src: str) -> List[DocLine]:
    parser = _PlasmaDocParser()
    parser.feed(html_src)
    return parser.get_doc()


def _doc_to_plasma_html(doc: List[DocLine], css_style: bool = False) -> str:
    """
    css_style=True  -> real UL/LI + CSS marker checkbox glyphs (☐/☒).
    css_style=False -> NO UL/LI. Everything is rendered as plain <p> lines:
                       "- something", "- [ ] something", "- [x] something".
                       This guarantees: no ☒ ☐ and no list bullets.
    """
    header = (
        '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0//EN" '
        '"http://www.w3.org/TR/REC-html40/strict.dtd">\n'
        + '<html><head><meta name="qrichtext" content="1" />'
        + '<meta charset="utf-8" />'
        + '<style type="text/css">\n'
        + "p, li { white-space: pre-wrap; }\n"
        + "hr { height: 1px; border-width: 0; }\n"
        + (
            'li.unchecked::marker { content: "\\2610"; }\n'
            'li.checked::marker { content: "\\2612"; }\n'
            if css_style
            else ""
        )
        + "</style></head>"
        + "<body style=\" font-family:'Noto Sans'; font-size:10pt; "
        + 'font-weight:400; font-style:normal;">\n'
    )

    base_style = (
        " margin-top:0px; margin-bottom:0px; margin-left:0px; "
        "margin-right:0px; -qt-block-indent:0; text-indent:0px;"
    )

    def segs_to_inner(segs: List[Tuple[str, bool]]) -> str:
        inner: List[str] = []
        for text, is_bold in _merge_segs(segs):
            safe_text = html.escape(text, quote=False)
            inner.append(
                f'<span style=" font-weight:700;">{safe_text}</span>'
                if is_bold
                else safe_text
            )
        return "".join(inner)

    parts: List[str] = []

    if not css_style:
        # Plain mode: render list items as text lines, keep "- / - [ ] / - [x]" literally.
        for dl in doc:
            if dl.kind == "li":
                if dl.state == "unchecked":
                    prefix = "- [ ] "
                elif dl.state == "checked":
                    prefix = "- [x] "
                else:
                    prefix = "- "
                inner = html.escape(prefix, quote=False) + segs_to_inner(dl.segs)
                parts.append(f'<p style="{base_style}">{inner}</p>\n')
                continue

            # paragraph
            if _segs_plain(dl.segs).strip() == "":
                parts.append(
                    f'<p style="-qt-paragraph-type:empty;{base_style}"><br /></p>\n'
                )
            else:
                inner = segs_to_inner(dl.segs)
                parts.append(f'<p style="{base_style}">{inner}</p>\n')

        return header + "".join(parts) + "</body></html>\n"

    # CSS mode: real list structure + checkbox marker CSS
    in_ul = False
    for dl in doc:
        if dl.kind == "li":
            if not in_ul:
                parts.append("<ul>\n")
                in_ul = True

            cls = ""
            if dl.state == "unchecked":
                cls = ' class="unchecked"'
            elif dl.state == "checked":
                cls = ' class="checked"'

            inner = segs_to_inner(dl.segs)
            parts.append(f'<li{cls}><p style="{base_style}">{inner}</p></li>\n')
            continue

        if in_ul:
            parts.append("</ul>\n")
            in_ul = False

        if _segs_plain(dl.segs).strip() == "":
            parts.append(
                f'<p style="-qt-paragraph-type:empty;{base_style}"><br /></p>\n'
            )
        else:
            inner = segs_to_inner(dl.segs)
            parts.append(f'<p style="{base_style}">{inner}</p>\n')

    if in_ul:
        parts.append("</ul>\n")

    return header + "".join(parts) + "</body></html>\n"
