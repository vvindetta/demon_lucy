import os
from dataclasses import replace
from pathlib import Path

import pytest

from demon_lucy.lib.args.models import KnownArg
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.lib.args.sources import _parse_config_args, parse_note_args
from demon_lucy.modules.email.codec import parse_draft
from demon_lucy.modules.email.config import (
    ACCOUNT_FILE,
    TEMPLATE,
    SETTINGS_TEMPLATE,
    load_account,
)
from demon_lucy.modules.email.documents import LITERAL_MARKER
from demon_lucy.modules.email.files import locked_file
from demon_lucy.modules.email.models import Security
from demon_lucy.modules.email.scaffold import (
    CREDENTIALS_FILE,
    WATCHER_FILE,
    FOLDERS,
    initialize,
)
from demon_lucy.modules.email.storage import MailStore


def _args(tokens=()):
    return parse_args(list(tokens), TEMPLATE)


def _read_config(path, template):
    return _parse_config_args(str(path), template)


def test_initialize_is_private_idempotent_and_has_no_separate_runners(tmp_path):
    root = tmp_path / "Email"
    initialize(str(root), _args())
    for folder in FOLDERS:
        assert (root / folder).is_dir()
    assert {p.name for p in (root / "Actions").iterdir()} == {
        "Reply",
        "Mark read",
        "Mark unread",
    }
    assert not (root / "Local-only").exists()
    assert not (root / "setup-systemd").exists()
    assert not (root / WATCHER_FILE).exists()
    assert not (root / CREDENTIALS_FILE).exists()
    assert parse_draft((root / "new email.md").read_text()).body == ""
    assert initialize(str(root), _args()) == {}
    if os.name != "nt":
        assert (root / ACCOUNT_FILE).stat().st_mode & 0o777 == 0o600
        assert (root / ".email").stat().st_mode & 0o777 == 0o700
    welcome = (root / "welcome.md").read_text()
    assert "--sys-ignore-paths " + str(root) in welcome
    assert "Move a draft into `Sent/`" in welcome
    assert "Restart your existing Lucy daemon" in welcome


def test_repairs_missing_config_from_saved_account_not_cli_defaults(tmp_path):
    root = tmp_path / "Email"
    initialize(str(root), _args(["--email-imap-host", "mail.example.test"]))
    content = (root / ACCOUNT_FILE).read_bytes()
    (root / ACCOUNT_FILE).unlink()
    initialize(str(root), _args())
    assert (root / ACCOUNT_FILE).read_bytes() == content


def test_existing_unmanaged_starter_is_never_registered_or_rewritten(tmp_path: Path):
    root = tmp_path / "Email"
    root.mkdir()
    starter = root / "new email.md"
    starter.write_text("my existing notes\n")

    initialize(str(root), _args())

    assert starter.read_text() == "my existing notes\n"
    with MailStore(str(root)) as store:
        assert store.items("draft") == []


def test_missing_starter_gets_new_identity_without_rebinding_previous_draft(
    tmp_path: Path,
):
    root = tmp_path / "Email"
    initialize(str(root), _args())
    old = parse_draft((root / "new email.md").read_text())
    (root / "new email.md").rename(root / ".email/recovery/uncertain.md")

    changed = initialize(str(root), _args())

    assert changed == {str(root / "new email.md"): 1}
    new = parse_draft((root / "new email.md").read_text())
    assert old.identity != new.identity
    with MailStore(str(root)) as store:
        assert store.get("draft", old.identity) == {"path": "new email.md"}
        assert store.get("draft", new.identity) == {"path": "new email.md"}


def test_account_serializes_only_typed_settings_never_action_or_unknown_flags(
    tmp_path: Path,
):
    root = tmp_path / "Email"
    args = parse_args(
        [
            "--email-imap-host",
            "imap.example.test",
            "--email-credential-provider",
            "environment",
            "--email-smtp-security",
            "tls",
            "--email-smtp-port",
            "465",
            "--email-imap-username",
            r"office\Lucy O'Brien",
            "--email-imap-password-env",
            "FIRST_SECRET",
            "--email-smtp-password-env",
            "SECOND_SECRET",
            "--email-send",
            "--email-root",
            "wrong-root",
            "--cmd",
            "echo unsafe",
            "--unknown-flag",
        ],
        [*TEMPLATE, KnownArg(name="cmd", value_type=str)],
    )

    initialize(str(root), args)

    parsed = _read_config(root / ACCOUNT_FILE, SETTINGS_TEMPLATE)
    assert not parsed.unknown
    assert {item.name for item in parsed.known} == {
        item.name for item in SETTINGS_TEMPLATE
    }
    account = load_account(str(root))
    assert account.imap.host == "imap.example.test"
    assert account.imap.username == r"office\Lucy O'Brien"
    assert account.smtp.security is Security.TLS
    assert account.smtp.port == 465
    assert not (root / CREDENTIALS_FILE).exists()


@pytest.mark.parametrize("relative", [".email", "Sent", "new email.md", ACCOUNT_FILE])
def test_initialization_rejects_symlinks(tmp_path: Path, relative: str):
    root = tmp_path / "Email"
    outside = tmp_path / "outside"
    outside.mkdir()
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        initialize(str(root), _args())
    assert list(outside.iterdir()) == []


def test_initializer_respects_account_lock(tmp_path: Path):
    root = tmp_path / "Email"
    (root / ".email").mkdir(parents=True)
    with locked_file(str(root / ".email/.lock")):
        with pytest.raises(OSError):
            initialize(str(root), _args())
    assert not (root / "new email.md").exists()


def test_control_character_in_settings_does_not_create_setup(tmp_path: Path):
    args = _args()
    args = replace(
        args,
        known=tuple(
            (
                replace(arg, value="sender@example.test\n--cmd bad")
                if arg.name == "email-from-address"
                else arg
            )
            for arg in args.known
        ),
    )
    root = tmp_path / "Email"
    with pytest.raises(ValueError, match="control"):
        initialize(str(root), args)
    assert not root.exists()


def test_email_markers_do_not_change_existing_note_argument_parsing(tmp_path: Path):
    note = tmp_path / "ordinary.md"
    note.write_text(LITERAL_MARKER + "\n--example\n")
    template = [KnownArg(name="example", value_type=bool, default=False)]

    assert parse_note_args(str(note), template).require("example").value is True
