from __future__ import annotations

from pathlib import Path

import pytest

import demon_lucy.modules.banner as banner_mod
from demon_lucy.modules.banner import Banner


@pytest.mark.parametrize(
    ("figlet_text", "initial", "arg_lines", "required_fragments"),
    [
        (
            "ASCII\n",
            "--banner Hello\nbody\n",
            {"banner": [1]},
            ["---\nASCII\n", "body\n"],
        ),
        # (
        #     "B\n",
        #     "head\n--banner X tail\n",
        #     {"banner": [2]},
        #     ["head\n", "B\n", "tail\n"],
        # ),
    ],
)
def test_apply_inserts_or_replaces_banner_block(
    tmp_path: Path,
    monkeypatch,
    figlet_text: str,
    initial: str,
    arg_lines: dict[str, list[int]],
    required_fragments: list[str],
):
    monkeypatch.setattr(banner_mod.pyfiglet, "figlet_format", lambda _txt: figlet_text)

    path = tmp_path / "note.md"
    path.write_text(initial, encoding="utf-8")

    module = Banner()
    changed = module._apply(
        path=str(path),
        config={"banner": "Hello", "banner_separator": "---"},
        arg_lines=arg_lines,
    )

    content = path.read_text(encoding="utf-8")
    assert changed == {str(path): 1}
    for fragment in required_fragments:
        assert fragment in content


def test_apply_returns_none_when_banner_is_not_configured(tmp_path: Path):
    path = tmp_path / "note.md"
    path.write_text("text\n", encoding="utf-8")

    module = Banner()
    changed = module._apply(
        path=str(path),
        config={"banner": None, "banner_separator": "---"},
        arg_lines={},
    )
    assert changed is None


def test_apply_returns_none_when_banner_line_is_missing(tmp_path: Path):
    path = tmp_path / "note.md"
    original = "text\n"
    path.write_text(original, encoding="utf-8")

    module = Banner()
    changed = module._apply(
        path=str(path),
        config={"banner": "Hello", "banner_separator": "---"},
        arg_lines={},
    )

    assert changed is None
    assert path.read_text(encoding="utf-8") == original
