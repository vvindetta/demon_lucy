from __future__ import annotations

from pathlib import Path

import pytest

import demon_lucy.modules.plasma_widget as plasma_mod
from demon_lucy.modules.abstract_module import Context
from demon_lucy.modules.plasma_widget.mirror_mapper import (
    _bold_items_to_plasma_html,
)
from demon_lucy.modules.plasma_widget import DocLine, PlasmaWidget

_NOTIFY_CFG = {
    "sys_notification_provider": "termuxapi",
    "sys_notification_min_interval_seconds": 10.0,
}


@pytest.fixture(autouse=True)
def _reset_plasma_globals(monkeypatch):
    monkeypatch.setattr(plasma_mod, "_INIT_DONE", False)
    monkeypatch.setattr(
        plasma_mod,
        "_STATE",
        plasma_mod.SyncState(doc_hash=None, bold_items_hash=None, css_style=None),
    )
    monkeypatch.setattr(plasma_mod, "_STATE_BY_KEY", {})
    monkeypatch.setattr(plasma_mod, "_INIT_DONE_BY_KEY", {})


def _canonicalize_md(md_text: str) -> str:
    return plasma_mod._doc_to_md(
        plasma_mod._md_to_doc(plasma_mod._normalize_md(md_text))
    )


def _roundtrip_once(md_text: str, *, css_style: bool) -> str:
    doc_from_md = plasma_mod._md_to_doc(plasma_mod._normalize_md(md_text))
    html = plasma_mod._doc_to_plasma_html(doc_from_md, css_style=css_style)
    doc_from_html = plasma_mod._html_to_doc(html)
    return plasma_mod._doc_to_md(doc_from_html)


def test_md_doc_roundtrip_preserves_checkbox_and_bold():
    md = "- [ ] **Task**\nPlain line"
    doc = plasma_mod._md_to_doc(md)

    assert doc[0].kind == "li"
    assert doc[0].state == "unchecked"
    assert doc[1].kind == "p"
    assert plasma_mod._doc_to_md(doc) == md


def test_doc_to_plasma_html_mode_switch_changes_structure():
    doc = [DocLine(kind="li", state="unchecked", segs=[("Task", False)])]

    plain_html = plasma_mod._doc_to_plasma_html(doc, css_style=False)
    css_html = plasma_mod._doc_to_plasma_html(doc, css_style=True)

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

    updated = plasma_mod._apply_mirror_items_to_doc(main_doc, ["new1", "new2", "new3"])
    rendered = plasma_mod._doc_to_md(updated)

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

    updated = plasma_mod._apply_mirror_items_to_doc(main_doc, ["new1"])
    rendered = plasma_mod._doc_to_md(updated)

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

    updated = plasma_mod._apply_mirror_lines_to_doc(
        main_doc,
        ["new1", "", "", "", "", "new2"],
    )

    assert plasma_mod._doc_to_md(updated) == "**new1**\n**new2**"


def test_mirror_html_output_preserves_blank_lines_but_items_filter_them():
    html = plasma_mod._bold_lines_to_plasma_html(["a", "", "", "b"])

    assert plasma_mod._mirror_html_to_lines(html) == ["a", "", "", "b"]
    assert plasma_mod._mirror_html_to_items(html) == ["a", "b"]


def test_bold_mirror_blank_only_edit_does_not_update_markdown_spacing():
    main_doc = [
        DocLine(kind="p", state=None, segs=[("a", True)]),
        DocLine(kind="p", state=None, segs=[("b", True)]),
    ]
    state = plasma_mod.SyncState(
        doc_hash=plasma_mod._doc_hash(main_doc),
        bold_items_hash=plasma_mod._items_hash(["a", "b"]),
        css_style=False,
    )

    plan = plasma_mod.plan_from_bold_mirror(
        state=state,
        mirror_html_current=plasma_mod._doc_to_plasma_html(
            [
                DocLine(kind="p", state=None, segs=[("a", True)]),
                DocLine(kind="p", state=None, segs=[]),
                DocLine(kind="p", state=None, segs=[("b", True)]),
            ],
            css_style=False,
        ),
        mirror_exists=True,
        widget_html_current=plasma_mod._doc_to_plasma_html(
            main_doc, css_style=False
        ),
        markdown_text_current="**a**\n**b**",
        css_style=False,
    )

    assert plan.markdown_text is None
    assert plan.widget_html is None
    assert plan.mirror_html is None
    assert plan.next_state.bold_items_hash == state.bold_items_hash


def test_markdown_sync_preserves_existing_mirror_blank_separators():
    mirror_html = plasma_mod._bold_lines_to_plasma_html(["old-a", "", "old-b"])
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
        markdown_text=plasma_mod._doc_to_md(doc),
        markdown_exists=True,
        widget_html_current=plasma_mod._doc_to_plasma_html(doc, css_style=False),
        mirror_html_current=mirror_html,
        css_style=False,
    )

    assert plan.mirror_html is not None
    assert plasma_mod._mirror_html_to_lines(plan.mirror_html) == [
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

    updated = plasma_mod._apply_mirror_lines_to_doc(main_doc, ["a", "x", "b"])

    assert plasma_mod._doc_to_md(updated) == (
        "**a**\nplain between\n**b**\ntail\n**x**"
    )


def test_cfg_parses_paths_and_boolean_values(tmp_path: Path):
    widget = tmp_path / "widget.html"
    md = tmp_path / "note.md"
    mirror = tmp_path / "mirror.html"

    ctx = Context(
        path=str(md),
        config={
            "plasma_widget_path": str(widget),
            "plasma_markdown_note_path": str(md),
            "plasma_bold_widget_path": str(mirror),
            "plasma_css_style": True,
        },
        arg_lines={},
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
        config=_NOTIFY_CFG,
    )

    assert ignore is not None
    assert str(widget.resolve()) in ignore
    assert str(mirror.resolve()) in ignore
    assert widget.exists()
    assert mirror.exists()


def test_from_main_plasma_updates_markdown(tmp_path: Path):
    widget = tmp_path / "widget.html"
    md = tmp_path / "todo.md"
    doc = [DocLine(kind="p", state=None, segs=[("Hello", True)])]
    widget.write_text(
        plasma_mod._doc_to_plasma_html(doc, css_style=False), encoding="utf-8"
    )

    module = PlasmaWidget()
    ignore = module._from_main_plasma(
        widget_path=str(widget),
        markdown_path=str(md),
        bold_widget_path=None,
        css_style=False,
        html_path=str(widget),
        config=_NOTIFY_CFG,
    )

    assert ignore is not None
    assert str(md.resolve()) in ignore
    assert md.read_text(encoding="utf-8") == "**Hello**"


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
                config=_NOTIFY_CFG,
            )
            module._from_main_plasma(
                widget_path=str(widget_path),
                markdown_path=str(md_path),
                bold_widget_path=str(mirror_path),
                css_style=False,
                html_path=str(widget_path),
                config=_NOTIFY_CFG,
            )
            module._from_bold_mirror(
                widget_path=str(widget_path),
                markdown_path=str(md_path),
                bold_widget_path=str(mirror_path),
                css_style=False,
                config=_NOTIFY_CFG,
            )

            current_md = md_path.read_text(encoding="utf-8")
            assert current_md == last_expected_md

            widget_doc = plasma_mod._html_to_doc(
                widget_path.read_text(encoding="utf-8")
            )
            expected_items = plasma_mod._extract_bold_items_from_doc(widget_doc)
            mirror_items = plasma_mod._mirror_html_to_items(
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
        config=_NOTIFY_CFG,
    )
    module._from_main_plasma(
        widget_path=str(widget_path),
        markdown_path=str(md_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        html_path=str(widget_path),
        config=_NOTIFY_CFG,
    )
    module._from_bold_mirror(
        widget_path=str(widget_path),
        markdown_path=str(md_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        config=_NOTIFY_CFG,
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
        config=_NOTIFY_CFG,
    )
    plain_html = widget_path.read_text(encoding="utf-8")
    assert "<ul>" not in plain_html
    assert "- [ ] " in plain_html

    ignore = module._from_markdown(
        markdown_path=str(md_path),
        widget_path=str(widget_path),
        bold_widget_path=str(mirror_path),
        css_style=True,
        config=_NOTIFY_CFG,
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
        config=_NOTIFY_CFG,
    )

    # Main edit wins when main event is processed last.
    widget_path.write_text(
        plasma_mod._doc_to_plasma_html(
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
        config=_NOTIFY_CFG,
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
        config=_NOTIFY_CFG,
    )
    assert md_path.read_text(encoding="utf-8") == "**from mirror**"

    # Main edit wins again if processed last.
    widget_path.write_text(
        plasma_mod._doc_to_plasma_html(
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
        config=_NOTIFY_CFG,
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
        config=_NOTIFY_CFG,
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
        config=_NOTIFY_CFG,
    )
    assert md_path.read_text(encoding="utf-8") == "**one**"

    module._from_main_plasma(
        widget_path=str(widget_path),
        markdown_path=str(md_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        html_path=str(widget_path),
        config=_NOTIFY_CFG,
    )
    mirror_items = plasma_mod._mirror_html_to_items(
        mirror_path.read_text(encoding="utf-8")
    )
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
        config=_NOTIFY_CFG,
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
        config=_NOTIFY_CFG,
    )
    assert md_path.read_text(encoding="utf-8") == "**a**\n**c**"

    widget_doc = plasma_mod._html_to_doc(widget_path.read_text(encoding="utf-8"))
    assert plasma_mod._extract_bold_items_from_doc(widget_doc) == ["a", "c"]
    assert plasma_mod._mirror_html_to_items(
        mirror_path.read_text(encoding="utf-8")
    ) == [
        "a",
        "c",
    ]

    # main -> md/mirror: replace all bold lines with "z"
    widget_path.write_text(
        plasma_mod._doc_to_plasma_html(
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
        config=_NOTIFY_CFG,
    )
    assert md_path.read_text(encoding="utf-8") == "**z**"
    assert plasma_mod._mirror_html_to_items(
        mirror_path.read_text(encoding="utf-8")
    ) == ["z"]

    # markdown -> main/mirror: remove "z", add "x","y"
    md_path.write_text("**x**\n**y**\n", encoding="utf-8")
    module._from_markdown(
        markdown_path=str(md_path),
        widget_path=str(widget_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        config=_NOTIFY_CFG,
    )
    widget_doc = plasma_mod._html_to_doc(widget_path.read_text(encoding="utf-8"))
    assert plasma_mod._extract_bold_items_from_doc(widget_doc) == ["x", "y"]
    assert plasma_mod._mirror_html_to_items(
        mirror_path.read_text(encoding="utf-8")
    ) == [
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
        config=_NOTIFY_CFG,
    )
    module._from_main_plasma(
        widget_path=str(widget_path),
        markdown_path=str(md_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        html_path=str(widget_path),
        config=_NOTIFY_CFG,
    )
    module._from_bold_mirror(
        widget_path=str(widget_path),
        markdown_path=str(md_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        config=_NOTIFY_CFG,
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
        config=_NOTIFY_CFG,
    )
    ignore_b = module._from_markdown(
        sync_key=(str(widget_b.resolve()), str(md_b.resolve()), None),
        markdown_path=str(md_b),
        widget_path=str(widget_b),
        bold_widget_path=None,
        css_style=False,
        config=_NOTIFY_CFG,
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
        config=_NOTIFY_CFG,
    )

    assert ignore is None
    assert plasma_mod._STATE_BY_KEY.get(sync_key) == initial_state
    assert widget_path.read_text(encoding="utf-8") == "old-widget"


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
        config=_NOTIFY_CFG,
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
        *,
        notify_errors: bool = True,
    ) -> bool:
        if (
            plasma_mod.canonical_path(path) == str(mirror_path.resolve())
            and content != mirror_old
            and notify_errors
        ):
            return False
        return real_write(
            path,
            content,
            notify_errors=notify_errors,
        )

    monkeypatch.setattr(plasma_mod, "_write_text_atomic", fail_mirror_write)

    ignore = PlasmaWidget()._from_markdown(
        sync_key=sync_key,
        markdown_path=str(md_path),
        widget_path=str(widget_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        config=_NOTIFY_CFG,
    )

    assert ignore is None
    assert plasma_mod._STATE_BY_KEY.get(sync_key) == initial_state
    assert widget_path.read_text(encoding="utf-8") == "old-widget"
    assert mirror_path.read_text(encoding="utf-8") == "old-mirror"
