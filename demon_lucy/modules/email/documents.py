"""Recognize the module's own email documents; this is not an engine directive."""

import os

LITERAL_MARKER = "<!-- lucy-email -->"


def is_literal_text(text: str) -> bool:
    return text.partition("\n")[0].removesuffix("\r") == LITERAL_MARKER


def is_literal_document(path: str | os.PathLike[str]) -> bool:
    try:
        if not os.path.isfile(path):
            return False
        with open(path, "rb") as handle:
            first_line = handle.readline(len(LITERAL_MARKER) + 3)
        return is_literal_text(first_line.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return False
