from __future__ import annotations

import stat
from pathlib import Path

import pytest

from demon_lucy.lib.text_file import write_bytes_atomic, write_text_atomic


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
