from __future__ import annotations

import stat
from pathlib import Path

from demon_lucy.lib.text_file import write_text_atomic


def test_write_text_atomic_preserves_content_and_mode(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    target.write_text("old\n", encoding="utf-8")
    target.chmod(0o640)

    write_text_atomic(str(target), "new\r\n")

    with open(target, "r", encoding="utf-8", newline="") as handle:
        assert handle.read() == "new\r\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
