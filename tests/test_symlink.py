import os

import pytest

from demon_lucy.lib import symlink as symlink_lib
from demon_lucy.lib.symlink import replace_symlink_atomic


def test_symlink_replacement_failure_keeps_original_link(tmp_path, monkeypatch):
    link = tmp_path / "note.md"
    link.symlink_to("missing.md")

    def fail_replace(*_args):
        raise PermissionError("read only")

    monkeypatch.setattr(symlink_lib.os, "replace", fail_replace)
    with pytest.raises(PermissionError):
        replace_symlink_atomic(str(link), "new.md", expected_target="missing.md")

    assert os.readlink(link) == "missing.md"
    assert list(tmp_path.iterdir()) == [link]


@pytest.mark.parametrize("replacement", ["file", "symlink", "missing"])
def test_symlink_replacement_preserves_concurrent_edits(
    tmp_path, monkeypatch, replacement
):
    link = tmp_path / "note.md"
    link.symlink_to("missing.md")
    real_symlink = os.symlink

    def concurrent_edit(target, temporary):
        real_symlink(target, temporary)
        link.unlink()
        if replacement == "file":
            link.write_text("user edit")
        elif replacement == "symlink":
            real_symlink("user-choice.md", link)

    monkeypatch.setattr(symlink_lib.os, "symlink", concurrent_edit)

    assert (
        replace_symlink_atomic(str(link), "new.md", expected_target="missing.md")
        is False
    )
    if replacement == "file":
        assert not link.is_symlink()
        assert link.read_text() == "user edit"
    elif replacement == "symlink":
        assert os.readlink(link) == "user-choice.md"
    else:
        assert not os.path.lexists(link)
    assert not list(tmp_path.glob(".lucy-link-*"))
