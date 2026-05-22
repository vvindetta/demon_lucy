from __future__ import annotations

from typing import List, Tuple

from lucy_notes_manager.modules.plasma_widget.model import (
    DocLine,
    _hash_text,
    _merge_segs,
    _normalize_md,
    _normalize_newlines,
    _segs_plain,
)


def _find_unescaped_double_stars(line: str) -> List[int]:
    positions: List[int] = []
    index = 0
    while index < len(line) - 1:
        if line[index] == "\\":
            index += 2
            continue
        if line[index : index + 2] == "**":
            positions.append(index)
            index += 2
            continue
        index += 1
    # if odd, last one is treated as literal
    if len(positions) % 2 == 1:
        positions = positions[:-1]
    return positions


def _md_line_to_segs(line: str) -> List[Tuple[str, bool]]:
    line = _normalize_newlines(line)
    stars = _find_unescaped_double_stars(line)
    if not stars:
        return [(line.replace("\\*", "*").replace("\\\\", "\\"), False)]

    cut = set(stars)
    segs: List[Tuple[str, bool]] = []
    buf: List[str] = []
    bold = False
    index = 0

    while index < len(line):
        if index in cut and line[index : index + 2] == "**":
            txt = "".join(buf)
            if txt:
                txt = txt.replace("\\*", "*").replace("\\\\", "\\")
                segs.append((txt, bold))
            buf = []
            bold = not bold
            index += 2
            continue

        if line[index] == "\\" and index + 1 < len(line):
            # keep escaped char literally
            buf.append(line[index + 1])
            index += 2
            continue

        buf.append(line[index])
        index += 1

    txt = "".join(buf)
    if txt:
        segs.append((txt, bold))

    return _merge_segs(segs)


def _escape_md_text(text: str) -> str:
    # escape backslash first, then asterisk
    text = text.replace("\\", "\\\\")
    text = text.replace("*", "\\*")
    return text


def _segs_to_md(segs: List[Tuple[str, bool]]) -> str:
    out: List[str] = []
    for text, is_bold in segs:
        safe = _escape_md_text(text)
        if is_bold and safe:
            out.append(f"**{safe}**")
        else:
            out.append(safe)
    return "".join(out)


def _md_to_doc(md_text: str) -> List[DocLine]:
    md_text = _normalize_newlines(md_text)
    lines: List[DocLine] = []
    for raw in md_text.split("\n"):
        line = raw.rstrip("\n")
        if line.strip() == "":
            lines.append(DocLine(kind="p", state=None, segs=[]))
            continue

        low = line.lstrip()

        # checkbox list item
        if low.startswith("- [ ] "):
            content = low[len("- [ ] ") :]
            segs = _md_line_to_segs(content)
            lines.append(DocLine(kind="li", state="unchecked", segs=segs))
            continue

        if low.lower().startswith("- [x] "):
            content = low[6:]
            segs = _md_line_to_segs(content)
            lines.append(DocLine(kind="li", state="checked", segs=segs))
            continue

        # normal paragraph
        segs = _md_line_to_segs(line)
        lines.append(DocLine(kind="p", state=None, segs=segs))

    # trim leading/trailing empty paragraphs
    while lines and lines[0].kind == "p" and _segs_plain(lines[0].segs).strip() == "":
        lines.pop(0)
    while lines and lines[-1].kind == "p" and _segs_plain(lines[-1].segs).strip() == "":
        lines.pop()

    return lines


def _doc_to_md(doc: List[DocLine]) -> str:
    """
    Important: in PLAIN widget mode, list-like text stays as text in paragraphs.
    So we only prepend "- [ ] / - [x]" when kind == "li".
    """
    out_lines: List[str] = []
    for dl in doc:
        if dl.kind == "p":
            out_lines.append(_segs_to_md(dl.segs) if dl.segs else "")
            continue

        prefix = "- "
        if dl.state == "unchecked":
            prefix = "- [ ] "
        elif dl.state == "checked":
            prefix = "- [x] "
        out_lines.append(prefix + _segs_to_md(dl.segs))

    return _normalize_md("\n".join(out_lines))


def _doc_hash(doc: List[DocLine]) -> str:
    return _hash_text(_doc_to_md(doc))
