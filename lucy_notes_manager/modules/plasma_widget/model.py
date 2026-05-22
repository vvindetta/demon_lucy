from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import List, Optional, Tuple


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _trim_trailing_spaces_per_line(text: str) -> str:
    return "\n".join([line.rstrip() for line in _normalize_newlines(text).split("\n")])


def _normalize_md(text: str) -> str:
    # keep user formatting, just normalize newlines + trailing spaces
    return _trim_trailing_spaces_per_line(text).strip("\n")


@dataclass
class DocLine:
    kind: str  # "p" or "li"
    state: Optional[str]  # for li: "unchecked" / "checked" / None
    segs: List[Tuple[str, bool]]  # (text, is_bold)


def _segs_plain(segs: List[Tuple[str, bool]]) -> str:
    return "".join(text for text, _is_bold in segs)


def _segs_has_bold(segs: List[Tuple[str, bool]]) -> bool:
    return any(is_bold for _text, is_bold in segs)


def _merge_segs(segs: List[Tuple[str, bool]]) -> List[Tuple[str, bool]]:
    out: List[Tuple[str, bool]] = []
    for text, is_bold in segs:
        if not text:
            continue
        if out and out[-1][1] == is_bold:
            out[-1] = (out[-1][0] + text, is_bold)
        else:
            out.append((text, is_bold))
    return out
