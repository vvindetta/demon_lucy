from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from watchdog.events import FileModifiedEvent

import demon_lucy.modules.banner as banner_mod
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.lib.text_file import SourceChangedError
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


@pytest.mark.parametrize(
    ("initial", "expected_texts", "expected"),
    [
        (
            "--banner Hello world\nbody\n",
            ["Hello world"],
            "ASCII Hello world\nbody\n",
        ),
        (
            "--banner date\nbody\n",
            ["date"],
            "ASCII date\nbody\n",
        ),
        (
            "--banner-date\nbody\n",
            ["2030-01-02"],
            "ASCII 2030-01-02\nbody\n",
        ),
        (
            "--banner Hello\nmiddle\n--banner-date\nbody\n",
            ["Hello", "2030-01-02"],
            "ASCII Hello\nmiddle\nASCII 2030-01-02\nbody\n",
        ),
        (
            "--banner-date\nmiddle\n--banner Hello\nbody\n",
            ["Hello", "2030-01-02"],
            "ASCII 2030-01-02\nmiddle\nASCII Hello\nbody\n",
        ),
        (
            "--banner Hello --banner-date --formatter-todo\nbody\n",
            ["Hello", "2030-01-02"],
            "ASCII Hello\nASCII 2030-01-02\n--formatter-todo\nbody\n",
        ),
    ],
)
def test_module_manager_renders_text_and_date_banners(
    tmp_path: Path,
    monkeypatch,
    initial: str,
    expected_texts: list[str],
    expected: str,
):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2030, 1, 2)

    monkeypatch.setattr(banner_mod, "date", FixedDate)
    seen_texts: list[str] = []

    def fake_figlet(text: str) -> str:
        seen_texts.append(text)
        return f"ASCII {text}\n"

    monkeypatch.setattr(banner_mod.pyfiglet, "figlet_format", fake_figlet)

    path = tmp_path / "note.md"
    path.write_text(initial, encoding="utf-8")

    manager = ModuleManager(
        modules=[Banner()],
        startup_args=parse_args(
            args=[],
            template=DEMON_LUCY_STARTUP_TEMPLATE,
        ),
    )

    changed = manager.run(str(path), FileModifiedEvent(str(path)), event_id="evt-test")

    assert changed == {str(path.resolve()): 1}
    assert sorted(seen_texts) == sorted(expected_texts)
    assert path.read_text(encoding="utf-8") == expected
    assert manager.run(str(path), FileModifiedEvent(str(path))) is None


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_apply_preserves_line_endings_and_file_mode(
    tmp_path: Path, monkeypatch, newline: str
):
    monkeypatch.setattr(
        banner_mod.pyfiglet, "figlet_format", lambda _text: "\n  \nASCII\nART\n \n"
    )
    note = tmp_path / "note.md"
    note.write_bytes(
        f"--banner-date --formatter-todo{newline}body{newline}".encode("utf-8")
    )
    note.chmod(0o640)

    changed = Banner()._apply(
        path=str(note),
        args=make_args(
            Banner.template,
            {"banner-date": True},
            lines={"banner-date": (1,)},
        ),
    )

    assert changed == {str(note): 1}
    assert note.read_bytes() == newline.join(
        ("ASCII", "ART", "--formatter-todo", "body", "")
    ).encode("utf-8")
    assert note.stat().st_mode & 0o777 == 0o640


@pytest.mark.parametrize("line_numbers", [(), (0,), (3,)])
def test_apply_does_not_insert_date_without_a_valid_command_line(
    tmp_path: Path, line_numbers: tuple[int, ...]
):
    note = tmp_path / "note.md"
    original = "title\nbody\n"
    note.write_text(original, encoding="utf-8")

    assert Banner()._apply(
        path=str(note),
        args=make_args(
            Banner.template,
            {"banner-date": True},
            lines={"banner-date": line_numbers},
        ),
    ) is None
    assert note.read_text(encoding="utf-8") == original


def test_empty_render_keeps_command(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(banner_mod.pyfiglet, "figlet_format", lambda _text: " \n\n")
    note = tmp_path / "note.md"
    original = "--banner-date\nbody\n"
    note.write_text(original, encoding="utf-8")

    assert Banner()._apply(
        path=str(note),
        args=make_args(
            Banner.template,
            {"banner-date": True},
            lines={"banner-date": (1,)},
        ),
    ) is None
    assert note.read_text(encoding="utf-8") == original


def test_apply_preserves_edits_made_during_render(tmp_path: Path, monkeypatch):
    note = tmp_path / "note.md"
    note.write_text("--banner-date\nbody\n", encoding="utf-8")
    edited = "--banner-date\nupdated body\n"

    def render_with_edit(_text):
        note.write_text(edited, encoding="utf-8")
        return "ASCII\n"

    monkeypatch.setattr(banner_mod.pyfiglet, "figlet_format", render_with_edit)
    with pytest.raises(SourceChangedError):
        Banner()._apply(
            path=str(note),
            args=make_args(
                Banner.template,
                {"banner-date": True},
                lines={"banner-date": (1,)},
            ),
        )
    assert note.read_text(encoding="utf-8") == edited
