from __future__ import annotations

from demon_lucy.lib.args.parser import Template

PLASMA_WIDGET_TEMPLATE: Template = [
    (
        "--plasma-widget-path",
        str,
        None,
        "Path to the main Plasma note HTML file (widget file).",
        True,
    ),
    (
        "--plasma-bold-widget-path",
        str,
        None,
        "Optional: path to a Plasma widget HTML file used as a 'bold-only mirror'.",
        False,
    ),
    (
        "--plasma-markdown-note-path",
        str,
        None,
        "Path to the Markdown note (supports **bold** and - [ ] / - [x]).",
        True,
    ),
    (
        "--plasma-css-style",
        bool,
        False,
        "If True: use CSS checkbox markers (☐/☒) via li.*::marker and real UL/LI. "
        "If False (default): render plain text only (no glyphs, no bullets).",
        False,
    ),
]
