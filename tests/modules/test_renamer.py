from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from demon_lucy.lib.args.parser import parse_args
import demon_lucy.modules.renamer as renamer_mod
from demon_lucy.modules.abstract_module import System
from demon_lucy.modules.renamer import Renamer
from tests.args_support import make_context


def test_rename_auto_format_defaults_to_markdown():
    parsed = parse_args(args=[], template=Renamer.template)

    assert parsed.unknown == ()
    assert parsed.require("rename-auto-format").value == "md"


def test_modified_returns_context_with_renamed_path(tmp_path: Path) -> None:
    old_path = tmp_path / "old.md"
    new_path = tmp_path / "new.md"
    old_path.write_text("x\n", encoding="utf-8")
    module = Renamer()
    ctx = make_context(
        str(old_path),
        module.template,
        {"rename": "new.md"},
    )

    result = module.modified(
        ctx,
        System(global_template=module.template, modules=[module]),
    )

    assert result is not None
    assert result.context.path == str(new_path)
    assert result.changed == {str(old_path): 1, str(new_path): 1}


@pytest.mark.parametrize(
    ("target_exists", "expected_changed"),
    [
        (False, True),
        (True, False),
    ],
)
def test_apply_manual_rename_behaviour(
    tmp_path: Path, target_exists: bool, expected_changed: bool
):
    old_path = tmp_path / "old.md"
    new_path = tmp_path / "new.md"
    old_path.write_text("x\n", encoding="utf-8")
    if target_exists:
        new_path.write_text("y\n", encoding="utf-8")

    module = Renamer()
    changed = module._apply_manual(path=str(old_path), new_name="new.md")

    assert (changed is not None) is expected_changed
    assert new_path.exists()
    if expected_changed:
        assert not old_path.exists()
    else:
        assert old_path.exists()


def test_apply_auto_on_create_renames_any_one_letter_file_with_default_format(
    tmp_path: Path, monkeypatch
):
    class _FakeDatetime:
        @classmethod
        def now(cls):
            return datetime(2026, 4, 21, 10, 30, 0)

    monkeypatch.setattr(renamer_mod, "datetime", _FakeDatetime)

    old_path = tmp_path / "x.md"
    old_path.write_text("x\n", encoding="utf-8")

    module = Renamer()
    changed = module._apply_auto_on_create(
        path=str(old_path),
        enabled=True,
        extension="txt",
    )

    assert changed is not None
    assert (tmp_path / "21-04.txt").exists()
    assert not old_path.exists()


def test_apply_auto_on_create_adds_extension_to_extensionless_file(
    tmp_path: Path, monkeypatch
):
    class _FakeDatetime:
        @classmethod
        def now(cls):
            return datetime(2026, 4, 21, 10, 30, 0)

    monkeypatch.setattr(renamer_mod, "datetime", _FakeDatetime)

    old_path = tmp_path / "m"
    old_path.write_text("x\n", encoding="utf-8")

    module = Renamer()
    changed = module._apply_auto_on_create(
        path=str(old_path),
        enabled=True,
        extension="org",
    )

    assert changed is not None
    assert (tmp_path / "m.org").exists()
    assert not old_path.exists()


def test_apply_auto_on_create_rejects_filename_format(tmp_path: Path, monkeypatch):
    class _FakeDatetime:
        @classmethod
        def now(cls):
            return datetime(2026, 4, 21, 10, 30, 0)

    monkeypatch.setattr(renamer_mod, "datetime", _FakeDatetime)

    old_path = tmp_path / "n"
    old_path.write_text("x\n", encoding="utf-8")

    module = Renamer()
    changed = module._apply_auto_on_create(
        path=str(old_path),
        enabled=True,
        extension="%Y-%m-%d.md",
    )

    assert changed is None
    assert old_path.exists()
    assert not (tmp_path / "2026-04-21.md").exists()


def test_apply_auto_on_create_adds_default_extension_to_any_extensionless_file(
    tmp_path: Path, monkeypatch
):
    class _FakeDatetime:
        @classmethod
        def now(cls):
            return datetime(2026, 4, 21, 10, 30, 0)

    monkeypatch.setattr(renamer_mod, "datetime", _FakeDatetime)

    txt_path = tmp_path / "txt"
    txt_path.write_text("x\n", encoding="utf-8")

    module = Renamer()
    changed = module._apply_auto_on_create(
        path=str(txt_path),
        enabled=True,
        extension="md",
    )

    assert changed is not None
    assert not txt_path.exists()
    assert (tmp_path / "txt.md").exists()
    assert not (tmp_path / "21-04.md").exists()


def test_apply_auto_on_create_adds_suffix_when_extension_target_exists(
    tmp_path: Path, monkeypatch
):
    class _FakeDatetime:
        @classmethod
        def now(cls):
            return datetime(2026, 4, 21, 10, 30, 45, 123456)

    monkeypatch.setattr(renamer_mod, "datetime", _FakeDatetime)

    old_path = tmp_path / "note"
    old_path.write_text("x\n", encoding="utf-8")
    (tmp_path / "note.md").write_text("existing\n", encoding="utf-8")

    module = Renamer()
    changed = module._apply_auto_on_create(
        path=str(old_path),
        enabled=True,
        extension="md",
    )

    assert changed is not None
    assert not old_path.exists()
    assert (tmp_path / "note-1030.md").exists()


def test_apply_auto_on_create_adds_more_time_precision_until_name_is_free(
    tmp_path: Path, monkeypatch
):
    class _FakeDatetime:
        @classmethod
        def now(cls):
            return datetime(2026, 4, 21, 10, 30, 45, 123456)

    monkeypatch.setattr(renamer_mod, "datetime", _FakeDatetime)

    for name in (
        "21-04.txt",
        "21-04-1030.txt",
        "21-04-103045.txt",
        "21-04-103045-123456.txt",
        "21-04-103045-001.txt",
    ):
        (tmp_path / name).write_text("existing\n", encoding="utf-8")

    old_path = tmp_path / "z.md"
    old_path.write_text("x\n", encoding="utf-8")

    module = Renamer()
    changed = module._apply_auto_on_create(
        path=str(old_path),
        enabled=True,
        extension="txt",
    )

    assert changed is not None
    assert (tmp_path / "21-04-103045-002.txt").exists()
    assert not old_path.exists()
