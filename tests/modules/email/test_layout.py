from pathlib import Path
import shlex

import pytest

from demon_lucy.lib.args.parser import parse_args
from demon_lucy.modules.email.config import TEMPLATE
from demon_lucy.modules.email.documents import LITERAL_MARKER
from demon_lucy.modules.email.layout import REFRESH_TEXT, upgrade_layout
from demon_lucy.modules.email.scaffold import initialize
from demon_lucy.modules.email.storage import MailStore


def old_layout(root: Path):
    initialize(str(root), parse_args([], TEMPLATE))
    for folder, flag in (
        ("Archive", "email-archive"),
        ("Trash", "email-trash"),
        ("Refresh", "email-fetch"),
    ):
        directory = root / "Actions" / folder
        directory.mkdir()
        command = shlex.join(["--email-root", str(root), "--" + flag])
        (directory / "init.md").write_text(
            shlex.join(["--dropdir-init", command]) + "\n"
        )
        target = root / folder / "init.md"
        if target.exists():
            target.unlink()
    (root / "refresh.md").write_text(
        LITERAL_MARKER + "\nDrop this file into Actions/Refresh/ to fetch mail.\n"
    )


def test_upgrade_removes_only_generated_duplicates_and_preserves_account(tmp_path):
    root = tmp_path / "Mail"
    old_layout(root)
    account = root / ".email/.account.conf"
    account.write_text("unfinished custom account settings\n")
    starter = (root / "new email.md").read_bytes()
    (root / "welcome.md").write_text(
        "Personal setup\n\n"
        "Drop `refresh.md` into `Actions/Refresh/` for an immediate fetch.\n"
        "Personal ending\n"
    )

    initialize(str(root), parse_args([], TEMPLATE))

    for folder in ("Archive", "Trash", "Refresh"):
        assert not (root / "Actions" / folder).exists()
    assert not (root / "Archive/init.md").exists()
    assert not (root / "Trash/init.md").exists()
    assert account.read_text() == "unfinished custom account settings\n"
    assert (root / "new email.md").read_bytes() == starter
    assert (root / "refresh.md").read_text() == REFRESH_TEXT
    welcome = (root / "welcome.md").read_text()
    assert welcome.startswith("Personal setup\n") and welcome.endswith(
        "Personal ending\n"
    )
    assert "Actions/Refresh" not in welcome
    assert initialize(str(root), parse_args([], TEMPLATE)) == {}


def test_upgrade_preserves_user_files_in_obsolete_action_directory(tmp_path):
    root = tmp_path / "Mail"
    old_layout(root)
    note = root / "Actions/Archive/my file.md"
    note.write_text("private content\n")

    with MailStore(str(root)) as store:
        upgrade_layout(store)

    assert note.read_text() == "private content\n"
    assert not (note.parent / "init.md").exists()


def test_upgrade_preserves_custom_action_and_refresh_text(tmp_path):
    root = tmp_path / "Mail"
    old_layout(root)
    custom = root / "Actions/Trash/init.md"
    custom.write_text('--dropdir-init "--custom-flag"\n')
    (root / "refresh.md").write_text(LITERAL_MARKER + "\nMy instructions\n")

    with MailStore(str(root)) as store:
        upgrade_layout(store)

    assert custom.read_text() == '--dropdir-init "--custom-flag"\n'
    assert (root / "refresh.md").read_text().endswith("My instructions\n")


def test_upgrade_does_not_replace_custom_mailbox_action(tmp_path):
    root = tmp_path / "Mail"
    old_layout(root)
    (root / "Archive/init.md").write_text("Custom archive instructions\n")
    legacy = root / "Actions/Archive/init.md"

    with MailStore(str(root)) as store:
        upgrade_layout(store)

    assert (root / "Archive/init.md").read_text() == "Custom archive instructions\n"
    assert not legacy.exists()


def test_upgrade_rejects_legacy_directory_symlink_without_touching_target(tmp_path):
    root = tmp_path / "Mail"
    initialize(str(root), parse_args([], TEMPLATE))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "init.md").write_text("keep\n")
    (root / "Actions/Archive").symlink_to(outside, target_is_directory=True)

    with MailStore(str(root)) as store, pytest.raises(ValueError, match="symlink"):
        upgrade_layout(store)

    assert (outside / "init.md").read_text() == "keep\n"
