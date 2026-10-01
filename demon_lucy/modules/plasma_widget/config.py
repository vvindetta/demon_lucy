from __future__ import annotations

from demon_lucy.lib.args.models import KnownArg, Template

PLASMA_WIDGET_TEMPLATE: Template = [
    KnownArg(
        name="plasma-widget-path",
        value_type=str,
        default=None,
        description="Path to the main Plasma note HTML file.",
        required=True,
    ),
    KnownArg(
        name="plasma-bold-widget-path",
        value_type=str,
        default=None,
        description="Optional path to a Plasma widget that mirrors only bold text.",
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
        description="Render checkboxes as CSS markers in HTML lists. When disabled, render plain text without checkbox glyphs or bullets.",
    ),
]
