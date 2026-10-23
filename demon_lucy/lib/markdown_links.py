from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import quote


def encode_markdown_path(path: str) -> str:
    # Keep Unicode readable; encode characters with Markdown or URL meaning.
    return "".join(
        (
            quote(character, safe="")
            if character in "%#?()<>\\\"'" or character.isspace()
            else character
        )
        for character in path.replace(os.sep, "/")
    )


def format_file_link(source_path: str, target_path: str) -> str:
    label = re.sub(r"([\\\[\]])", r"\\\1", Path(target_path).stem)
    relative_path = os.path.relpath(target_path, os.path.dirname(source_path))
    if ":" in relative_path.split(os.sep)[0]:
        relative_path = "." + os.sep + relative_path
    return f"[{label}]({encode_markdown_path(relative_path)})"
