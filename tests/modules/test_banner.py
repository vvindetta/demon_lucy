from __future__ import annotations

from pathlib import Path

import pytest
from watchdog.events import FileModifiedEvent

import demon_lucy.modules.banner as banner_mod
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.module_manager import ModuleManager
from demon_lucy.modules.banner import Banner
from demon_lucy.runtime import DEMON_LUCY_STARTUP_TEMPLATE
from tests.args_support import make_args


@pytest.mark.parametrize(
    ("initial", "banner_line", "expected"),
    [
        (
            "--banner Hello\nbody\n",
            1,
            "ASCII\nbody\n",
        ),
        (
            "--formatter-todo --banner Hello\nbody\n",
            1,
            "ASCII\n--formatter-todo\nbody\n",
        ),
        (
            "title\n--banner Hello\nbody\n",
            2,
            "title\nASCII\nbody\n",
        ),
        (
            "title\n--formatter-todo --banner Hello\nbody\n",
            2,
            "title\nASCII\n--formatter-todo\nbody\n",
        ),
    ],
)
def test_apply_inserts_or_replaces_banner_block(
    tmp_path: Path,
    monkeypatch,
    initial: str,
    banner_line: int,
    expected: str,
):
    monkeypatch.setattr(banner_mod.pyfiglet, "figlet_format", lambda _txt: "ASCII\n")

    path = tmp_path / "note.md"
    path.write_text(initial, encoding="utf-8")

    module = Banner()
    changed = module._apply(
        path=str(path),
        args=make_args(
            Banner.template,
            {"banner": ["Hello"]},
            lines={"banner": (banner_line,)},
        ),
    )

    content = path.read_text(encoding="utf-8")
    assert changed == {str(path): 1}
    assert content == expected


def test_apply_returns_none_when_banner_is_not_configured(tmp_path: Path):
    path = tmp_path / "note.md"
    path.write_text("text\n", encoding="utf-8")

    module = Banner()
    changed = module._apply(
        path=str(path),
        args=make_args(Banner.template),
    )
    assert changed is None


def test_apply_returns_none_when_banner_line_is_missing(tmp_path: Path):
    path = tmp_path / "note.md"
    original = "text\n"
    path.write_text(original, encoding="utf-8")

    module = Banner()
    changed = module._apply(
        path=str(path),
        args=make_args(
            Banner.template,
            {"banner": ["Hello"]},
        ),
    )

    assert changed is None
    assert path.read_text(encoding="utf-8") == original


def test_banner_text_joins_values_from_first_banner_line():
    text = Banner._banner_text(
        make_args(
            Banner.template,
            {"banner": ["Hello", "world", "Second"]},
            lines={"banner": (1, 1, 3)},
        )
    )

    assert text == "Hello world"


def test_module_manager_parses_unquoted_multi_word_banner(
    tmp_path: Path,
    monkeypatch,
):
    seen_texts: list[str] = []

    def fake_figlet(text: str) -> str:
        seen_texts.append(text)
        return "ASCII\n"

    monkeypatch.setattr(banner_mod.pyfiglet, "figlet_format", fake_figlet)

    path = tmp_path / "note.md"
    path.write_text("--banner Hello world\nbody\n", encoding="utf-8")

    manager = ModuleManager(
        modules=[Banner()],
        startup_args=parse_args(
            args=[],
            template=DEMON_LUCY_STARTUP_TEMPLATE,
        ),
    )

    changed = manager.run(str(path), FileModifiedEvent(str(path)), event_id="evt-test")

    assert changed == {str(path.resolve()): 1}
    assert seen_texts == ["Hello world"]
    assert path.read_text(encoding="utf-8") == "ASCII\nbody\n"
