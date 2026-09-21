from __future__ import annotations

import stat
from pathlib import Path

import pytest

from demon_lucy.lib.text_file import (
    SourceChangedError,
    write_bytes_atomic,
    write_text_atomic,
)


def test_write_text_atomic_preserves_content_and_mode(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    target.write_text("old\n", encoding="utf-8")
    target.chmod(0o640)

    write_text_atomic(str(target), "new\r\n")

    with open(target, "r", encoding="utf-8", newline="") as handle:
        assert handle.read() == "new\r\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_atomic_bytes_preserve_binary_data_and_mode(tmp_path: Path) -> None:
    target = tmp_path / "packet.patch"
    target.write_bytes(b"old")
    target.chmod(0o640)
    write_bytes_atomic(str(target), b"\0\xff\r\n")
    assert target.read_bytes() == b"\0\xff\r\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_atomic_replace_failure_preserves_old_file_and_cleans_temp(
    tmp_path, monkeypatch
):
    target = tmp_path / "note.md"
    target.write_text("original")
    monkeypatch.setattr(
        "demon_lucy.lib.text_file.os.replace",
        lambda *_: (_ for _ in ()).throw(OSError("failed")),
    )
    with pytest.raises(OSError):
        write_text_atomic(str(target), "updated")
    assert target.read_text() == "original"
    assert list(tmp_path.iterdir()) == [target]


def test_guarded_write_accepts_unchanged_source(tmp_path):
    target = tmp_path / "note.md"
    target.write_bytes(b"original\r\n")

    write_text_atomic(str(target), "updated\r\n", expected_text="original\r\n")

    assert target.read_bytes() == b"updated\r\n"


@pytest.mark.parametrize("mutation", ["edit", "delete"])
def test_guarded_write_rechecks_after_preparing_temporary_file(
    tmp_path, monkeypatch, mutation
):
    target = tmp_path / "note.md"
    target.write_text("original")

    def change_source(_fd):
        if mutation == "edit":
            target.write_text("concurrent edit")
        else:
            target.unlink()

    monkeypatch.setattr("demon_lucy.lib.text_file.os.fsync", change_source)

    with pytest.raises(SourceChangedError):
        write_text_atomic(str(target), "transcript", expected_text="original")

    if mutation == "edit":
        assert target.read_text() == "concurrent edit"
        assert list(tmp_path.iterdir()) == [target]
    else:
        assert list(tmp_path.iterdir()) == []


def test_guarded_write_checks_empty_source(tmp_path):
    target = tmp_path / "note.md"
    target.write_text("concurrent edit")

    with pytest.raises(SourceChangedError):
        write_text_atomic(str(target), "transcript", expected_text="")

    assert target.read_text() == "concurrent edit"
