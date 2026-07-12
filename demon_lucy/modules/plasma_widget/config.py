from __future__ import annotations

from demon_lucy.lib.args.parser import ArgTemplate, Template

PLASMA_WIDGET_TEMPLATE: Template = [
    ArgTemplate(
        name="--plasma-widget-path",
        value_type=str,
        default=None,
        description="Path to the main Plasma note HTML file (widget file).",
        required=True,
    ),
    ArgTemplate(
        name="--plasma-bold-widget-path",
        value_type=str,
        default=None,
        description="Optional: path to a Plasma widget HTML file used as a 'bold-only mirror'.",
        required=False,
    ),
    ArgTemplate(
        name="--plasma-markdown-note-path",
        value_type=str,
        default=None,
        description="Path to the Markdown note (supports **bold** and - [ ] / - [x]).",
        required=True,
    ),
    ArgTemplate(
        name="--plasma-css-style",
        value_type=bool,
        default=False,
        description="If True: use CSS checkbox markers (☐/☒) via li.*::marker and real UL/LI. "
        "If False (default): render plain text only (no glyphs, no bullets).",
        required=False,
    ),
]
