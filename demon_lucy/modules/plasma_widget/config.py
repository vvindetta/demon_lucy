from __future__ import annotations

from demon_lucy.lib.args.models import KnownArg, Template

PLASMA_WIDGET_TEMPLATE: Template = [
    KnownArg(
        name="plasma-widget-path",
        value_type=str,
        default=None,
        description="Path to the main Plasma note HTML file (widget file).",
        required=True,
    ),
    KnownArg(
        name="plasma-bold-widget-path",
        value_type=str,
        default=None,
        description="Optional: path to a Plasma widget HTML file used as a 'bold-only mirror'.",
    ),
    KnownArg(
        name="plasma-markdown-note-path",
        value_type=str,
        default=None,
        description="Path to the Markdown note (supports **bold** and - [ ] / - [x]).",
        required=True,
    ),
    KnownArg(
        name="plasma-css-style",
        value_type=bool,
        default=False,
        description="If True: use CSS checkbox markers (☐/☒) via li.*::marker and real UL/LI. "
        "If False (default): render plain text only (no glyphs, no bullets).",
    ),
]
