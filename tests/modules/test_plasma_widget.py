from __future__ import annotations

from pathlib import Path

import pytest

import demon_lucy.modules.plasma_widget as plasma_mod
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.modules.abstract_module import Context
from demon_lucy.modules.plasma_widget import PlasmaWidget
from demon_lucy.modules.plasma_widget.config import PLASMA_WIDGET_TEMPLATE
from demon_lucy.modules.plasma_widget.markdown_codec import (
    _doc_hash,
    _doc_to_md,
    _md_to_doc,
)
from demon_lucy.modules.plasma_widget.mirror_mapper import (
    _apply_mirror_items_to_doc,
    _apply_mirror_lines_to_doc,
    _bold_items_to_plasma_html,
    _bold_lines_to_plasma_html,
    _extract_bold_items_from_doc,
    _items_hash,
    _mirror_html_to_items,
    _mirror_html_to_lines,
)
from demon_lucy.modules.plasma_widget.model import DocLine, _normalize_md
from demon_lucy.modules.plasma_widget.plasma_html_codec import (
    _doc_to_plasma_html,
    _html_to_doc,
)
from demon_lucy.runtime import DEMON_LUCY_STARTUP_TEMPLATE

_TEMPLATE = [*DEMON_LUCY_STARTUP_TEMPLATE, *PLASMA_WIDGET_TEMPLATE]
_NOTIFY_ARGS = parse_args(
    args=[
        "--sys-notification-provider",
        "termuxapi",
        "--sys-notification-min-interval-seconds",
        "10",
    ],
    template=_TEMPLATE,
)


def _context(
    *,
    path: str,
    widget_path: str,
    markdown_path: str,
    bold_widget_path: str | None = None,
    css_style: bool = False,
) -> Context:
    tokens = [
        "--plasma-widget-path",
        widget_path,
        "--plasma-markdown-note-path",
        markdown_path,
    ]
    if bold_widget_path is not None:
        tokens.extend(["--plasma-bold-widget-path", bold_widget_path])
    if css_style:
        tokens.append("--plasma-css-style")
    return Context(
        path=path,
        args=parse_args(args=tokens, template=_TEMPLATE),
        run_mode="daemon",
        event_id="evt-test",
    )


@pytest.fixture(autouse=True)
def _reset_plasma_globals(monkeypatch):
    monkeypatch.setattr(plasma_mod, "_STATE_BY_KEY", {})
    monkeypatch.setattr(plasma_mod, "_INIT_DONE_BY_KEY", {})


def _canonicalize_md(md_text: str) -> str:
    return _doc_to_md(_md_to_doc(_normalize_md(md_text)))


def _roundtrip_once(md_text: str, *, css_style: bool) -> str:
    doc_from_md = _md_to_doc(_normalize_md(md_text))
    html = _doc_to_plasma_html(doc_from_md, css_style=css_style)
    doc_from_html = _html_to_doc(html)
    return _doc_to_md(doc_from_html)


def test_md_doc_roundtrip_preserves_checkbox_and_bold():
    md = "- [ ] **Task**\nPlain line"
    doc = _md_to_doc(md)

    assert doc[0].kind == "li"
    assert doc[0].state == "unchecked"
    assert doc[1].kind == "p"
    assert _doc_to_md(doc) == md


def test_doc_to_plasma_html_mode_switch_changes_structure():
    doc = [DocLine(kind="li", state="unchecked", segs=[("Task", False)])]

    plain_html = _doc_to_plasma_html(doc, css_style=False)
    css_html = _doc_to_plasma_html(doc, css_style=True)

    assert "<ul>" not in plain_html
    assert "- [ ] Task" in plain_html
    assert "<ul>" in css_html
    assert "li.unchecked::marker" in css_html


def test_apply_mirror_items_to_doc_replaces_bold_lines_and_appends_new():
    main_doc = [
        DocLine(kind="p", state=None, segs=[("plain", False)]),
        DocLine(kind="p", state=None, segs=[("old1", True)]),
        DocLine(kind="li", state="checked", segs=[("old2", True)]),
    ]

    updated = _apply_mirror_items_to_doc(main_doc, ["new1", "new2", "new3"])
    rendered = _doc_to_md(updated)

    assert "plain" in rendered
    assert "**new1**" in rendered
    assert "- [x] **new2**" in rendered
    assert "**new3**" in rendered


def test_apply_mirror_items_to_doc_removes_lines_deleted_in_mirror():
    main_doc = [
        DocLine(kind="p", state=None, segs=[("plain", False)]),
        DocLine(kind="p", state=None, segs=[("old1", True)]),
        DocLine(kind="li", state="checked", segs=[("old2", True)]),
        DocLine(kind="p", state=None, segs=[("tail", False)]),
    ]

    updated = _apply_mirror_items_to_doc(main_doc, ["new1"])
    rendered = _doc_to_md(updated)

    assert "plain" in rendered
    assert "**new1**" in rendered
    assert "old1" not in rendered
    assert "old2" not in rendered
    assert "tail" in rendered


def test_apply_mirror_lines_ignores_blank_lines_in_markdown():
    main_doc = [
        DocLine(kind="p", state=None, segs=[("old1", True)]),
        DocLine(kind="p", state=None, segs=[("old2", True)]),
    ]

    updated = _apply_mirror_lines_to_doc(
        main_doc,
        ["new1", "", "", "", "", "new2"],
    )

    assert _doc_to_md(updated) == "**new1**\n**new2**"


def test_mirror_html_output_preserves_blank_lines_but_items_filter_them():
    html = _bold_lines_to_plasma_html(["a", "", "", "b"])

    assert _mirror_html_to_lines(html) == ["a", "", "", "b"]
    assert _mirror_html_to_items(html) == ["a", "b"]


def test_bold_mirror_blank_only_edit_does_not_update_markdown_spacing():
    main_doc = [
        DocLine(kind="p", state=None, segs=[("a", True)]),
        DocLine(kind="p", state=None, segs=[("b", True)]),
    ]
    state = plasma_mod.SyncState(
        doc_hash=_doc_hash(main_doc),
        bold_items_hash=_items_hash(["a", "b"]),
        css_style=False,
    )

    plan = plasma_mod.plan_from_bold_mirror(
        state=state,
        mirror_html_current=_doc_to_plasma_html(
            [
                DocLine(kind="p", state=None, segs=[("a", True)]),
                DocLine(kind="p", state=None, segs=[]),
                DocLine(kind="p", state=None, segs=[("b", True)]),
            ],
            css_style=False,
        ),
        mirror_exists=True,
        widget_html_current=_doc_to_plasma_html(main_doc, css_style=False),
        markdown_text_current="**a**\n**b**",
        css_style=False,
    )

    assert plan.markdown_text is None
    assert plan.widget_html is None
    assert plan.mirror_html is None
    assert plan.next_state.bold_items_hash == state.bold_items_hash


def test_markdown_sync_preserves_existing_mirror_blank_separators():
    mirror_html = _bold_lines_to_plasma_html(["old-a", "", "old-b"])
    doc = [
        DocLine(kind="p", state=None, segs=[("new-a", True)]),
        DocLine(kind="p", state=None, segs=[("new-b", True)]),
    ]
    state = plasma_mod.SyncState(
        doc_hash="old-doc",
        bold_items_hash="old-items",
        css_style=False,
    )

    plan = plasma_mod.plan_from_markdown(
        state=state,
        markdown_text=_doc_to_md(doc),
        markdown_exists=True,
        widget_html_current=_doc_to_plasma_html(doc, css_style=False),
        mirror_html_current=mirror_html,
        css_style=False,
    )

    assert plan.mirror_html is not None
    assert _mirror_html_to_lines(plan.mirror_html) == [
        "new-a",
        "",
        "new-b",
    ]


def test_apply_mirror_insert_appends_new_bold_line_to_end():
    main_doc = [
        DocLine(kind="p", state=None, segs=[("a", True)]),
        DocLine(kind="p", state=None, segs=[("plain between", False)]),
        DocLine(kind="p", state=None, segs=[("b", True)]),
        DocLine(kind="p", state=None, segs=[("tail", False)]),
    ]

    updated = _apply_mirror_lines_to_doc(main_doc, ["a", "x", "b"])

    assert _doc_to_md(updated) == ("**a**\nplain between\n**b**\ntail\n**x**")


def test_apply_mirror_reorders_existing_bold_lines_without_append():
    main_doc = [
        DocLine(kind="p", state=None, segs=[("read book", True)]),
        DocLine(kind="p", state=None, segs=[("work", True)]),
        DocLine(kind="p", state=None, segs=[("- reply", True)]),
        DocLine(kind="p", state=None, segs=[("- reset account", True)]),
    ]

    updated = _apply_mirror_lines_to_doc(
        main_doc,
        ["work", "read book", "- reply", "- reset account"],
    )
    assert _doc_to_md(updated) == (
        "**work**\n**read book**\n**- reply**\n**- reset account**"
    )

    updated = _apply_mirror_lines_to_doc(
        main_doc,
        ["- reset account", "read book", "work", "- reply"],
    )
    assert _doc_to_md(updated) == (
        "**- reset account**\n**read book**\n**work**\n**- reply**"
    )


def test_cfg_parses_paths_and_boolean_values(tmp_path: Path):
    widget = tmp_path / "widget.html"
    md = tmp_path / "note.md"
    mirror = tmp_path / "mirror.html"

    ctx = _context(
        path=str(md),
        widget_path=str(widget),
        markdown_path=str(md),
        bold_widget_path=str(mirror),
        css_style=True,
    )

    module = PlasmaWidget()
    widget_path, md_path, mirror_path, css_style = module._cfg(ctx)
    assert widget_path == str(widget.resolve())
    assert md_path == str(md.resolve())
    assert mirror_path == str(mirror.resolve())
    assert css_style is True


def test_from_markdown_writes_widget_and_mirror(tmp_path: Path):
    md = tmp_path / "todo.md"
    widget = tmp_path / "widget.html"
    mirror = tmp_path / "mirror.html"
    md.write_text("Line\n**Bold**\n", encoding="utf-8")

    module = PlasmaWidget()
    ignore = module._from_markdown(
        markdown_path=str(md),
        widget_path=str(widget),
        bold_widget_path=str(mirror),
        css_style=False,
        args=_NOTIFY_ARGS,
    )

    assert ignore is not None
    assert str(widget.resolve()) in ignore
    assert str(mirror.resolve()) in ignore
    assert widget.exists()
    assert mirror.exists()


def test_handle_markdown_bootstrap_writes_missing_main_widget(tmp_path: Path):
    md = tmp_path / "todo.md"
    widget = tmp_path / "widget.html"
    mirror = tmp_path / "mirror.html"
    md.write_text("Line\n**Bold**\n", encoding="utf-8")

    module = PlasmaWidget()
    ignore = module._handle(
        _context(
            path=str(md),
            widget_path=str(widget),
            markdown_path=str(md),
            bold_widget_path=str(mirror),
        )
    )

    assert ignore is not None
    assert str(widget.resolve()) in ignore
    assert str(mirror.resolve()) in ignore
    assert (
        _doc_to_md(_html_to_doc(widget.read_text(encoding="utf-8"))) == "Line\n**Bold**"
    )
    assert _mirror_html_to_items(mirror.read_text(encoding="utf-8")) == ["Bold"]


def test_from_main_plasma_updates_markdown(tmp_path: Path):
    widget = tmp_path / "widget.html"
    md = tmp_path / "todo.md"
    doc = [DocLine(kind="p", state=None, segs=[("Hello", True)])]
    widget.write_text(_doc_to_plasma_html(doc, css_style=False), encoding="utf-8")

    module = PlasmaWidget()
    ignore = module._from_main_plasma(
        widget_path=str(widget),
        markdown_path=str(md),
        bold_widget_path=None,
        css_style=False,
        html_path=str(widget),
        args=_NOTIFY_ARGS,
    )

    assert ignore is not None
    assert str(md.resolve()) in ignore
    assert md.read_text(encoding="utf-8") == "**Hello**"


def test_empty_main_plasma_restores_from_markdown_instead_of_clearing():
    markdown = "Line\n**Bold**"
    empty_widget = _doc_to_plasma_html([], css_style=False)
    state = plasma_mod.bootstrap_state(markdown, empty_widget)

    plan = plasma_mod.plan_from_main_plasma(
        state=state,
        widget_html_current=empty_widget,
        widget_exists=True,
        markdown_text_current=markdown,
        mirror_html_current=None,
        css_style=False,
    )

    assert plan.blocked_empty_source == "main_plasma"
    assert plan.markdown_text is None
    assert plan.widget_html is not None
    assert _doc_to_md(_html_to_doc(plan.widget_html)) == markdown


def test_partial_main_plasma_restores_from_markdown_instead_of_truncating():
    markdown = (
        "Section alpha\n"
        "- task alpha should stay\n"
        "- task beta should stay\n"
        "- task gamma should stay\n"
        "plain context should stay\n"
        "more context should stay\n"
        "**Keep bold**\n"
        "plain tail should stay"
    )
    partial_doc = [
        DocLine(kind="p", state=None, segs=[("Keep bold", True)]),
        DocLine(kind="p", state=None, segs=[("plain tail should stay", False)]),
    ]
    partial_widget = _doc_to_plasma_html(partial_doc, css_style=False)
    state = plasma_mod.bootstrap_state(markdown, partial_widget)

    plan = plasma_mod.plan_from_main_plasma(
        state=state,
        widget_html_current=partial_widget,
        widget_exists=True,
        markdown_text_current=markdown,
        mirror_html_current=None,
        css_style=False,
    )

    assert plan.blocked_shrinking_source == "main_plasma"
    assert plan.markdown_text is None
    assert plan.widget_html is not None
    assert _doc_to_md(_html_to_doc(plan.widget_html)) == markdown


def test_from_main_plasma_empty_source_preserves_markdown_and_restores_targets(
    tmp_path: Path,
    monkeypatch,
):
    widget = tmp_path / "widget.html"
    md = tmp_path / "todo.md"
    mirror = tmp_path / "mirror.html"
    md.write_text("Line\n**Bold**", encoding="utf-8")
    widget.write_text(
        _doc_to_plasma_html([], css_style=False),
        encoding="utf-8",
    )
    mirror.write_text(_bold_lines_to_plasma_html([]), encoding="utf-8")

    notifications = []

    def fake_safe_notify(name, message, **kwargs):
        notifications.append((name, message, kwargs))

    monkeypatch.setattr(plasma_mod, "safe_notify", fake_safe_notify)

    ignore = PlasmaWidget()._from_main_plasma(
        widget_path=str(widget),
        markdown_path=str(md),
        bold_widget_path=str(mirror),
        css_style=False,
        html_path=str(widget),
        args=_NOTIFY_ARGS,
    )

    assert ignore is not None
    assert str(widget.resolve()) in ignore
    assert str(mirror.resolve()) in ignore
    assert str(md.resolve()) not in ignore
    assert md.read_text(encoding="utf-8") == "Line\n**Bold**"
    assert (
        _doc_to_md(_html_to_doc(widget.read_text(encoding="utf-8"))) == "Line\n**Bold**"
    )
    assert _mirror_html_to_items(mirror.read_text(encoding="utf-8")) == ["Bold"]
    assert notifications
    assert notifications[0][0] == f"plasma-empty-source:{md.resolve()}"


def test_empty_bold_mirror_with_empty_main_restores_from_markdown():
    markdown = "**Keep**\nplain"
    empty_widget = _doc_to_plasma_html([], css_style=False)
    empty_mirror = _bold_lines_to_plasma_html([])
    state = plasma_mod.bootstrap_state(markdown, empty_widget)

    plan = plasma_mod.plan_from_bold_mirror(
        state=state,
        mirror_html_current=empty_mirror,
        mirror_exists=True,
        widget_html_current=empty_widget,
        markdown_text_current=markdown,
        css_style=False,
    )

    assert plan.blocked_empty_source == "bold_mirror"
    assert plan.markdown_text is None
    assert plan.widget_html is not None
    assert _doc_to_md(_html_to_doc(plan.widget_html)) == markdown
    assert plan.mirror_html is not None
    assert _mirror_html_to_items(plan.mirror_html) == ["Keep"]


def test_bold_mirror_uses_markdown_structure_when_main_plasma_is_truncated(
    tmp_path: Path,
):
    md_path = tmp_path / "todo.md"
    widget_path = tmp_path / "widget.html"
    mirror_path = tmp_path / "mirror.html"
    markdown = (
        "Section alpha\n"
        "- task alpha should stay\n"
        "- task beta should stay\n"
        "- task gamma should stay\n"
        "plain context should stay\n"
        "more context should stay\n"
        "**Keep bold**\n"
        "plain tail should stay"
    )
    truncated_doc = [
        DocLine(kind="p", state=None, segs=[("Keep bold", True)]),
        DocLine(kind="p", state=None, segs=[("plain tail should stay", False)]),
    ]

    md_path.write_text(markdown, encoding="utf-8")
    widget_path.write_text(
        _doc_to_plasma_html(truncated_doc, css_style=False),
        encoding="utf-8",
    )
    mirror_path.write_text(_bold_items_to_plasma_html(["Keep bold"]), encoding="utf-8")

    ignore = PlasmaWidget()._from_bold_mirror(
        widget_path=str(widget_path),
        markdown_path=str(md_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        args=_NOTIFY_ARGS,
    )

    assert ignore is not None
    assert str(widget_path.resolve()) in ignore
    assert str(md_path.resolve()) not in ignore
    assert md_path.read_text(encoding="utf-8") == markdown
    assert _doc_to_md(_html_to_doc(widget_path.read_text(encoding="utf-8"))) == markdown


@pytest.mark.parametrize(
    ("source_md", "mode_sequence", "rounds"),
    [
        (
            "- [ ] **Task A**\nline with **mid** bold and tail\n- [x] done",
            [False],
            40,
        ),
        ("a\\*b\\*c\n**bold**\n\n- [ ] item", [False, True, False], 24),
        (
            "plain\n\n- [x] **Checked** and plain suffix\n- [ ] second",
            [True, False],
            26,
        ),
        ("**one** **two**\n- [ ] mix **x** y **z**", [False, True, False, True], 20),
        ("- [ ] first\n- [x] **second**", [True], 35),
        ("para **bold** text\n\n- [ ] list", [True, False, True], 22),
        ("**A**\n**B**\n- [ ] **C**", [True], 35),
    ],
)
def test_roundtrip_mode_sequences_remain_stable(
    source_md: str, mode_sequence: list[bool], rounds: int
):
    expected = _canonicalize_md(source_md)
    current = source_md

    for _ in range(rounds):
        for css_style in mode_sequence:
            current = _roundtrip_once(current, css_style=css_style)

    assert current == expected

    # One extra pass in each mode should keep exactly the same canonical text.
    for css_style in mode_sequence:
        assert _roundtrip_once(current, css_style=css_style) == current


def test_sync_ring_many_texts_keeps_final_state_deterministic(tmp_path: Path):
    md_path = tmp_path / "todo.md"
    widget_path = tmp_path / "widget.html"
    mirror_path = tmp_path / "mirror.html"
    module = PlasmaWidget()

    texts = [
        "- [ ] **Task 1**\nline\n- [x] done",
        "plain **bold** text\n\n- [ ] item A\n- [ ] item B",
        "**Header**\nparagraph\n- [x] **Finish**",
    ]

    last_expected_md = ""
    for _ in range(4):
        for text in texts:
            md_path.write_text(text, encoding="utf-8")
            last_expected_md = _canonicalize_md(text)

            module._from_markdown(
                markdown_path=str(md_path),
                widget_path=str(widget_path),
                bold_widget_path=str(mirror_path),
                css_style=False,
                args=_NOTIFY_ARGS,
            )
            module._from_main_plasma(
                widget_path=str(widget_path),
                markdown_path=str(md_path),
                bold_widget_path=str(mirror_path),
                css_style=False,
                html_path=str(widget_path),
                args=_NOTIFY_ARGS,
            )
            module._from_bold_mirror(
                widget_path=str(widget_path),
                markdown_path=str(md_path),
                bold_widget_path=str(mirror_path),
                css_style=False,
                args=_NOTIFY_ARGS,
            )

            current_md = md_path.read_text(encoding="utf-8")
            assert current_md == last_expected_md

            widget_doc = _html_to_doc(widget_path.read_text(encoding="utf-8"))
            expected_items = _extract_bold_items_from_doc(widget_doc)
            mirror_items = _mirror_html_to_items(
                mirror_path.read_text(encoding="utf-8")
            )
            assert mirror_items == expected_items

    final_md = md_path.read_text(encoding="utf-8")
    final_widget = widget_path.read_text(encoding="utf-8")
    final_mirror = mirror_path.read_text(encoding="utf-8")

    # Final idempotence check: one more full ring must not change outputs.
    module._from_markdown(
        markdown_path=str(md_path),
        widget_path=str(widget_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        args=_NOTIFY_ARGS,
    )
    module._from_main_plasma(
        widget_path=str(widget_path),
        markdown_path=str(md_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        html_path=str(widget_path),
        args=_NOTIFY_ARGS,
    )
    module._from_bold_mirror(
        widget_path=str(widget_path),
        markdown_path=str(md_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        args=_NOTIFY_ARGS,
    )

    assert md_path.read_text(encoding="utf-8") == final_md == last_expected_md
    assert widget_path.read_text(encoding="utf-8") == final_widget
    assert mirror_path.read_text(encoding="utf-8") == final_mirror


def test_css_toggle_rewrites_widget_structure_on_same_doc(tmp_path: Path):
    md_path = tmp_path / "todo.md"
    widget_path = tmp_path / "widget.html"
    mirror_path = tmp_path / "mirror.html"
    module = PlasmaWidget()

    md_path.write_text("- [ ] **Task**\n", encoding="utf-8")

    module._from_markdown(
        markdown_path=str(md_path),
        widget_path=str(widget_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        args=_NOTIFY_ARGS,
    )
    plain_html = widget_path.read_text(encoding="utf-8")
    assert "<ul>" not in plain_html
    assert "- [ ] " in plain_html

    ignore = module._from_markdown(
        markdown_path=str(md_path),
        widget_path=str(widget_path),
        bold_widget_path=str(mirror_path),
        css_style=True,
        args=_NOTIFY_ARGS,
    )
    css_html = widget_path.read_text(encoding="utf-8")

    assert ignore is not None
    assert str(widget_path.resolve()) in ignore
    assert "li.unchecked::marker" not in plain_html
    assert "li.unchecked::marker" in css_html


def test_last_event_wins_between_main_and_mirror(tmp_path: Path):
    md_path = tmp_path / "todo.md"
    widget_path = tmp_path / "widget.html"
    mirror_path = tmp_path / "mirror.html"
    module = PlasmaWidget()

    # bootstrap files
    md_path.write_text("**seed**\n", encoding="utf-8")
    module._from_markdown(
        markdown_path=str(md_path),
        widget_path=str(widget_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        args=_NOTIFY_ARGS,
    )

    # Main edit wins when main event is processed last.
    widget_path.write_text(
        _doc_to_plasma_html(
            [DocLine(kind="p", state=None, segs=[("from main", True)])],
            css_style=False,
        ),
        encoding="utf-8",
    )
    module._from_main_plasma(
        widget_path=str(widget_path),
        markdown_path=str(md_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        html_path=str(widget_path),
        args=_NOTIFY_ARGS,
    )
    assert md_path.read_text(encoding="utf-8") == "**from main**"

    # Mirror edit wins when mirror event is processed last.
    mirror_path.write_text(
        _bold_items_to_plasma_html(["from mirror"]),
        encoding="utf-8",
    )
    module._from_bold_mirror(
        widget_path=str(widget_path),
        markdown_path=str(md_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        args=_NOTIFY_ARGS,
    )
    assert md_path.read_text(encoding="utf-8") == "**from mirror**"

    # Main edit wins again if processed last.
    widget_path.write_text(
        _doc_to_plasma_html(
            [DocLine(kind="p", state=None, segs=[("from main again", True)])],
            css_style=False,
        ),
        encoding="utf-8",
    )
    module._from_main_plasma(
        widget_path=str(widget_path),
        markdown_path=str(md_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        html_path=str(widget_path),
        args=_NOTIFY_ARGS,
    )
    assert md_path.read_text(encoding="utf-8") == "**from main again**"


def test_mirror_deleted_line_does_not_reappear_after_sync(tmp_path: Path):
    md_path = tmp_path / "todo.md"
    widget_path = tmp_path / "widget.html"
    mirror_path = tmp_path / "mirror.html"
    module = PlasmaWidget()

    md_path.write_text("**one**\n**two**\n", encoding="utf-8")
    module._from_markdown(
        markdown_path=str(md_path),
        widget_path=str(widget_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        args=_NOTIFY_ARGS,
    )

    mirror_path.write_text(
        _bold_items_to_plasma_html(["one"]),
        encoding="utf-8",
    )
    module._from_bold_mirror(
        widget_path=str(widget_path),
        markdown_path=str(md_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        args=_NOTIFY_ARGS,
    )
    assert md_path.read_text(encoding="utf-8") == "**one**"

    module._from_main_plasma(
        widget_path=str(widget_path),
        markdown_path=str(md_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        html_path=str(widget_path),
        args=_NOTIFY_ARGS,
    )
    mirror_items = _mirror_html_to_items(mirror_path.read_text(encoding="utf-8"))
    assert mirror_items == ["one"]


def test_bidirectional_add_delete_sync_consistency(tmp_path: Path):
    md_path = tmp_path / "todo.md"
    widget_path = tmp_path / "widget.html"
    mirror_path = tmp_path / "mirror.html"
    module = PlasmaWidget()

    # bootstrap from markdown
    md_path.write_text("**a**\n**b**\n", encoding="utf-8")
    module._from_markdown(
        markdown_path=str(md_path),
        widget_path=str(widget_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        args=_NOTIFY_ARGS,
    )

    # mirror -> main/md: remove "b", add "c"
    mirror_path.write_text(
        _bold_items_to_plasma_html(["a", "c"]),
        encoding="utf-8",
    )
    module._from_bold_mirror(
        widget_path=str(widget_path),
        markdown_path=str(md_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        args=_NOTIFY_ARGS,
    )
    assert md_path.read_text(encoding="utf-8") == "**a**\n**c**"

    widget_doc = _html_to_doc(widget_path.read_text(encoding="utf-8"))
    assert _extract_bold_items_from_doc(widget_doc) == ["a", "c"]
    assert _mirror_html_to_items(mirror_path.read_text(encoding="utf-8")) == [
        "a",
        "c",
    ]

    # main -> md/mirror: replace all bold lines with "z"
    widget_path.write_text(
        _doc_to_plasma_html(
            [DocLine(kind="p", state=None, segs=[("z", True)])],
            css_style=False,
        ),
        encoding="utf-8",
    )
    module._from_main_plasma(
        widget_path=str(widget_path),
        markdown_path=str(md_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        html_path=str(widget_path),
        args=_NOTIFY_ARGS,
    )
    assert md_path.read_text(encoding="utf-8") == "**z**"
    assert _mirror_html_to_items(mirror_path.read_text(encoding="utf-8")) == ["z"]

    # markdown -> main/mirror: remove "z", add "x","y"
    md_path.write_text("**x**\n**y**\n", encoding="utf-8")
    module._from_markdown(
        markdown_path=str(md_path),
        widget_path=str(widget_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        args=_NOTIFY_ARGS,
    )
    widget_doc = _html_to_doc(widget_path.read_text(encoding="utf-8"))
    assert _extract_bold_items_from_doc(widget_doc) == ["x", "y"]
    assert _mirror_html_to_items(mirror_path.read_text(encoding="utf-8")) == [
        "x",
        "y",
    ]

    # one extra full ring should not change any file
    md_before = md_path.read_text(encoding="utf-8")
    widget_before = widget_path.read_text(encoding="utf-8")
    mirror_before = mirror_path.read_text(encoding="utf-8")

    module._from_markdown(
        markdown_path=str(md_path),
        widget_path=str(widget_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        args=_NOTIFY_ARGS,
    )
    module._from_main_plasma(
        widget_path=str(widget_path),
        markdown_path=str(md_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        html_path=str(widget_path),
        args=_NOTIFY_ARGS,
    )
    module._from_bold_mirror(
        widget_path=str(widget_path),
        markdown_path=str(md_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        args=_NOTIFY_ARGS,
    )

    assert md_path.read_text(encoding="utf-8") == md_before
    assert widget_path.read_text(encoding="utf-8") == widget_before
    assert mirror_path.read_text(encoding="utf-8") == mirror_before


def test_engine_state_is_isolated_per_sync_context():
    state_a = plasma_mod.bootstrap_state("**A**", "")
    state_b = plasma_mod.bootstrap_state("**B**", "")

    plan_a = plasma_mod.plan_from_markdown(
        state=state_a,
        markdown_text="**A1**",
        markdown_exists=True,
        widget_html_current="",
        mirror_html_current=None,
        css_style=False,
    )
    plan_b = plasma_mod.plan_from_markdown(
        state=state_b,
        markdown_text="**B1**",
        markdown_exists=True,
        widget_html_current="",
        mirror_html_current=None,
        css_style=False,
    )

    assert plan_a.next_state.doc_hash != state_b.doc_hash
    assert plan_b.next_state.doc_hash != state_a.doc_hash


def test_module_state_is_isolated_per_file_pair_with_same_content(tmp_path: Path):
    md_a = tmp_path / "a.md"
    widget_a = tmp_path / "a.html"
    md_b = tmp_path / "b.md"
    widget_b = tmp_path / "b.html"

    md_a.write_text("**same**\n", encoding="utf-8")
    md_b.write_text("**same**\n", encoding="utf-8")

    module = PlasmaWidget()
    ignore_a = module._from_markdown(
        sync_key=(str(widget_a.resolve()), str(md_a.resolve()), None),
        markdown_path=str(md_a),
        widget_path=str(widget_a),
        bold_widget_path=None,
        css_style=False,
        args=_NOTIFY_ARGS,
    )
    ignore_b = module._from_markdown(
        sync_key=(str(widget_b.resolve()), str(md_b.resolve()), None),
        markdown_path=str(md_b),
        widget_path=str(widget_b),
        bold_widget_path=None,
        css_style=False,
        args=_NOTIFY_ARGS,
    )

    assert ignore_a is not None
    assert ignore_b is not None
    assert widget_a.exists()
    assert widget_b.exists()


def test_state_does_not_advance_when_write_fails(tmp_path: Path, monkeypatch):
    md_path = tmp_path / "todo.md"
    widget_path = tmp_path / "widget.html"
    md_path.write_text("**new**\n", encoding="utf-8")
    widget_path.write_text("old-widget", encoding="utf-8")
    notifications = []

    def fake_safe_notify(name, message, **kwargs):
        notifications.append((name, message, kwargs))

    monkeypatch.setattr(plasma_mod, "safe_notify", fake_safe_notify)

    initial_state = plasma_mod.SyncState(
        doc_hash="doc-before",
        bold_items_hash="bold-before",
        css_style=False,
    )
    sync_key = (str(widget_path.resolve()), str(md_path.resolve()), None)
    monkeypatch.setattr(plasma_mod, "_STATE_BY_KEY", {sync_key: initial_state})

    real_write = plasma_mod._write_text_atomic

    def fail_widget_write(
        path: str,
        content: str,
        *,
        notify_errors: bool = True,
    ) -> bool:
        if (
            plasma_mod.canonical_path(path) == str(widget_path.resolve())
            and notify_errors
        ):
            return False
        return real_write(
            path,
            content,
            notify_errors=notify_errors,
        )

    monkeypatch.setattr(plasma_mod, "_write_text_atomic", fail_widget_write)

    ignore = PlasmaWidget()._from_markdown(
        sync_key=sync_key,
        markdown_path=str(md_path),
        widget_path=str(widget_path),
        bold_widget_path=None,
        css_style=False,
        args=_NOTIFY_ARGS,
    )

    assert ignore is None
    assert plasma_mod._STATE_BY_KEY.get(sync_key) == initial_state
    assert widget_path.read_text(encoding="utf-8") == "old-widget"
    assert len(notifications) == 1
    assert notifications[0][0] == f"plasma-write:{widget_path.resolve()}"
    assert str(widget_path.resolve()) in notifications[0][1]


def test_read_error_is_not_treated_as_empty_input(tmp_path: Path, monkeypatch):
    md_path = tmp_path / "todo.md"
    widget_path = tmp_path / "widget.html"
    md_path.write_text("**seed**\n", encoding="utf-8")
    widget_path.write_text("old-widget", encoding="utf-8")

    initial_state = plasma_mod.SyncState(
        doc_hash="doc-before",
        bold_items_hash="bold-before",
        css_style=False,
    )
    sync_key = (str(widget_path.resolve()), str(md_path.resolve()), None)
    monkeypatch.setattr(plasma_mod, "_STATE_BY_KEY", {sync_key: initial_state})

    real_open = open

    def fail_markdown_read(path, mode="r", *args, **kwargs):
        if "r" in mode and plasma_mod.canonical_path(str(path)) == str(
            md_path.resolve()
        ):
            raise PermissionError("denied")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(plasma_mod, "open", fail_markdown_read, raising=False)

    ignore = PlasmaWidget()._from_markdown(
        sync_key=sync_key,
        markdown_path=str(md_path),
        widget_path=str(widget_path),
        bold_widget_path=None,
        css_style=False,
        args=_NOTIFY_ARGS,
    )

    assert ignore is None
    assert plasma_mod._STATE_BY_KEY.get(sync_key) == initial_state
    assert widget_path.read_text(encoding="utf-8") == "old-widget"


def test_multi_file_write_failure_rolls_back_previous_file(tmp_path: Path, monkeypatch):
    md_path = tmp_path / "todo.md"
    widget_path = tmp_path / "widget.html"
    mirror_path = tmp_path / "mirror.html"

    md_path.write_text("**new-main**\n", encoding="utf-8")
    widget_path.write_text("old-widget", encoding="utf-8")
    mirror_path.write_text("old-mirror", encoding="utf-8")
    notifications = []

    def fake_safe_notify(name, message, **kwargs):
        notifications.append((name, message, kwargs))

    monkeypatch.setattr(plasma_mod, "safe_notify", fake_safe_notify)

    initial_state = plasma_mod.SyncState(
        doc_hash="doc-before",
        bold_items_hash="bold-before",
        css_style=False,
    )
    sync_key = (
        str(widget_path.resolve()),
        str(md_path.resolve()),
        str(mirror_path.resolve()),
    )
    monkeypatch.setattr(plasma_mod, "_STATE_BY_KEY", {sync_key: initial_state})

    mirror_old = mirror_path.read_text(encoding="utf-8")
    real_write = plasma_mod._write_text_atomic

    def fail_mirror_write(
        path: str,
        content: str,
    ) -> bool:
        if (
            plasma_mod.canonical_path(path) == str(mirror_path.resolve())
            and content != mirror_old
        ):
            return False
        return real_write(path, content)

    monkeypatch.setattr(plasma_mod, "_write_text_atomic", fail_mirror_write)

    ignore = PlasmaWidget()._from_markdown(
        sync_key=sync_key,
        markdown_path=str(md_path),
        widget_path=str(widget_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        args=_NOTIFY_ARGS,
    )

    assert ignore is None
    assert plasma_mod._STATE_BY_KEY.get(sync_key) == initial_state
    assert widget_path.read_text(encoding="utf-8") == "old-widget"
    assert mirror_path.read_text(encoding="utf-8") == "old-mirror"
    assert len(notifications) == 1
    assert notifications[0][0] == f"plasma-write:{mirror_path.resolve()}"
    assert "Rollback also failed" not in notifications[0][1]


def test_multi_file_write_failure_reports_failed_rollback_once(
    tmp_path: Path, monkeypatch
):
    md_path = tmp_path / "todo.md"
    widget_path = tmp_path / "widget.html"
    mirror_path = tmp_path / "mirror.html"

    md_path.write_text("**new-main**\n", encoding="utf-8")
    widget_path.write_text("old-widget", encoding="utf-8")
    mirror_path.write_text("old-mirror", encoding="utf-8")
    notifications = []

    def fake_safe_notify(name, message, **kwargs):
        notifications.append((name, message, kwargs))

    monkeypatch.setattr(plasma_mod, "safe_notify", fake_safe_notify)

    sync_key = (
        str(widget_path.resolve()),
        str(md_path.resolve()),
        str(mirror_path.resolve()),
    )
    monkeypatch.setattr(
        plasma_mod,
        "_STATE_BY_KEY",
        {
            sync_key: plasma_mod.SyncState(
                doc_hash="doc-before",
                bold_items_hash="bold-before",
                css_style=False,
            )
        },
    )

    mirror_old = mirror_path.read_text(encoding="utf-8")
    real_write = plasma_mod._write_text_atomic

    def fail_mirror_write_and_widget_rollback(
        path: str,
        content: str,
    ) -> bool:
        if (
            plasma_mod.canonical_path(path) == str(mirror_path.resolve())
            and content != mirror_old
        ):
            return False
        if (
            plasma_mod.canonical_path(path) == str(widget_path.resolve())
            and content == "old-widget"
        ):
            return False
        return real_write(path, content)

    monkeypatch.setattr(
        plasma_mod, "_write_text_atomic", fail_mirror_write_and_widget_rollback
    )

    ignore = PlasmaWidget()._from_markdown(
        sync_key=sync_key,
        markdown_path=str(md_path),
        widget_path=str(widget_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        args=_NOTIFY_ARGS,
    )

    assert ignore is None
    assert len(notifications) == 1
    assert notifications[0][0] == f"plasma-write:{mirror_path.resolve()}"
    assert "Rollback also failed" in notifications[0][1]
    assert str(widget_path.resolve()) in notifications[0][1]
