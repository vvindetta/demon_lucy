from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from demon_lucy.lib.args.models import KnownArg
from demon_lucy.lib.args.parser import parse_args, split_arg_line
from demon_lucy.lib.args.sources import parse_note_args
from demon_lucy.modules.dropdir import DropDir
from demon_lucy.modules.email.codec import parse_draft, render_draft
from demon_lucy.modules.email.config import (
    ACCOUNT_FILE,
    DROP_ACTION_FOLDERS,
    SETTINGS_TEMPLATE,
    TEMPLATE,
    load_account,
)
from demon_lucy.modules.email.models import Security
from demon_lucy.modules.email.documents import LITERAL_MARKER
from demon_lucy.modules.email.files import locked_file
from demon_lucy.modules.email.scaffold import CREDENTIALS_FILE, WATCHER_FILE, initialize
from demon_lucy.modules.email.storage import MailStore
from demon_lucy.runtime import DEMON_LUCY_STARTUP_TEMPLATE


def _args(tokens: list[str] | None = None):
    return parse_args(tokens or [], TEMPLATE)


def _read_config(path: Path, template):
    tokens = [
        token
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
        for token in split_arg_line(line)
    ]
    return parse_args(tokens, template)


def test_initialization_creates_private_setup_and_registered_blank_draft(
    tmp_path: Path,
):
    root = tmp_path / "Email"
    changed = initialize(str(root), _args())

    assert changed
    for folder in ("Inbox", "Drafts", "Sent", "Archive", "Trash", "Local-only"):
        assert (root / folder).is_dir()
    starter = root / "new email.md"
    draft = parse_draft(starter.read_text())
    assert draft.to == draft.cc == draft.bcc == draft.subject == draft.body == ""
    assert draft.attachments == ()
    assert starter.read_text().endswith("> ")
    with MailStore(str(root)) as store:
        assert store.get("draft", draft.identity) == {"path": "new email.md"}
    assert (root / "refresh.md").read_text().startswith(LITERAL_MARKER + "\n")
    assert (root / "welcome.md").read_text().startswith(LITERAL_MARKER + "\n")
    assert (root / ".gitignore").read_text() == ".email/\n"
    assert (root / ".email/.gitignore").read_text() == "*\n"
    assert not (root / CREDENTIALS_FILE).exists()
    welcome = (root / "welcome.md").read_text()
    assert "--email-credentials-save" in welcome
    assert "demon_lucy/modules/email/requirements.txt" in welcome
    assert "Secret Service or KWallet" in welcome
    assert "export " not in welcome
    assert ".credentials.env" not in welcome
    assert all(
        path.name.startswith(".")
        for path in (root / ".email").iterdir()
        if path.is_file()
    )
    if os.name != "nt":
        for relative in (
            ACCOUNT_FILE,
            WATCHER_FILE,
            ".email/.state.sqlite3",
        ):
            assert (root / relative).stat().st_mode & 0o777 == 0o600
        assert (root / ".email").stat().st_mode & 0o777 == 0o700


def test_initialization_preserves_all_existing_files_and_identity(tmp_path: Path):
    root = tmp_path / "Email"
    initialize(str(root), _args())
    starter = root / "new email.md"
    identity = parse_draft(starter.read_text()).identity
    starter.write_text(
        render_draft(replace(parse_draft(starter.read_text()), body="my draft\n"))
    )
    for relative in (
        ACCOUNT_FILE,
        WATCHER_FILE,
        CREDENTIALS_FILE,
        "Actions/Send/init.md",
        "welcome.md",
        ".gitignore",
    ):
        (root / relative).write_text("custom content\n")
    before = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.name not in {".state.sqlite3", ".lock"}
    }

    assert initialize(str(root), _args(["--email-imap-host", "changed.invalid"])) == {}
    assert parse_draft(starter.read_text()).identity == identity
    assert {relative: (root / relative).read_bytes() for relative in before} == before
    with MailStore(str(root)) as store:
        assert store.items("draft") == [(identity, {"path": "new email.md"})]


def test_recreated_setup_uses_existing_account_credentials(tmp_path: Path):
    root = tmp_path / "Mail"
    initialize(
        str(root),
        _args(
            [
                "--email-credential-provider",
                "environment",
                "--email-imap-password-env",
                "OLD_IMAP_PASSWORD",
                "--email-smtp-password-env",
                "OLD_SMTP_PASSWORD",
            ]
        ),
    )
    account_path = root / ACCOUNT_FILE
    saved_account = (
        account_path.read_text()
        .replace("OLD_IMAP", "CURRENT_IMAP")
        .replace("OLD_SMTP", "CURRENT_SMTP")
    )
    account_path.write_text(saved_account)
    service = next((root / "setup-systemd").glob("*-fetch.service"))
    for path in (service, root / "welcome.md", root / CREDENTIALS_FILE):
        path.unlink()

    changed = initialize(str(root), _args())

    assert set(changed) == {
        str(service),
        str(root / "welcome.md"),
        str(root / CREDENTIALS_FILE),
    }
    assert account_path.read_text() == saved_account
    assert f"EnvironmentFile={root / CREDENTIALS_FILE}" in service.read_text()
    for path in (root / "welcome.md", root / CREDENTIALS_FILE):
        assert "CURRENT_IMAP_PASSWORD" in path.read_text()
        assert "CURRENT_SMTP_PASSWORD" in path.read_text()
        assert "OLD_IMAP_PASSWORD" not in path.read_text()


def test_recreated_environment_file_uses_existing_account_with_default_cli(
    tmp_path: Path,
):
    root = tmp_path / "Mail"
    initialize(
        str(root),
        _args(
            [
                "--email-credential-provider",
                "environment",
                "--email-imap-password-env",
                "EXISTING_PASSWORD",
            ]
        ),
    )
    credentials = root / CREDENTIALS_FILE
    credentials.unlink()

    assert initialize(str(root), _args()) == {str(credentials): 1}
    assert "EXISTING_PASSWORD" in credentials.read_text()


def test_noop_keyring_initialization_does_not_load_edited_account(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "Mail"
    initialize(str(root), _args())
    account = root / ACCOUNT_FILE
    account.write_text("unfinished custom content\n")
    monkeypatch.setattr(
        "demon_lucy.modules.email.scaffold.load_account",
        lambda *_args: pytest.fail("no-op initialization loaded account settings"),
    )

    assert initialize(str(root), _args()) == {}
    assert account.read_text() == "unfinished custom content\n"
    assert not (root / CREDENTIALS_FILE).exists()


def test_existing_keyring_config_wins_over_environment_cli_during_repair(
    tmp_path: Path,
):
    root = tmp_path / "Mail"
    initialize(str(root), _args())
    service = next((root / "setup-systemd").glob("*-fetch.service"))
    service.unlink()

    assert initialize(
        str(root), _args(["--email-credential-provider", "environment"])
    ) == {str(service): 1}
    assert "EnvironmentFile=" not in service.read_text()
    assert not (root / CREDENTIALS_FILE).exists()


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
    (root / "new email.md").rename(root / "Local-only/uncertain.md")

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
    credentials = (root / CREDENTIALS_FILE).read_text()
    assert "FIRST_SECRET" in credentials and "SECOND_SECRET" in credentials
    assert all(
        not line.strip() or line.startswith("#") for line in credentials.splitlines()
    )


def test_action_and_watcher_config_roundtrip_spaces_quotes_backslashes(tmp_path: Path):
    root = tmp_path / 'Email\'s "quoted" \\ account % $'
    initialize(str(root), _args())
    for folder, action in DROP_ACTION_FOLDERS.items():
        parsed = parse_note_args(str(root / folder / "init.md"), DropDir.template)
        command = parsed.require("dropdir-init").value[0]
        tokens = split_arg_line(command)
        assert tokens == ["--email-root", str(root), "--" + action]
        action_args = parse_args(tokens, TEMPLATE)
        assert action_args.require("email-root").value == str(root)
        assert action_args.require(action).value is True
        assert not action_args.unknown
    watcher = _read_config(root / WATCHER_FILE, DEMON_LUCY_STARTUP_TEMPLATE)
    assert not watcher.unknown
    assert watcher.require("sys-watch-paths").value == [str(root)]
    assert watcher.require("sys-modules").value == ["dropdir", "email"]
    assert watcher.require("sys-log-level").value == "info"
    service = next((root / "setup-systemd").glob("*-watcher.service")).read_text()
    assert "%%" in service and "$$" in service
    assert 'Email\'s \\"quoted\\" \\\\ account %% $$' in service


@pytest.mark.parametrize("provider", ["keyring", "environment"])
def test_generated_units_are_account_specific_and_fetch_every_five_minutes(
    tmp_path: Path,
    provider: str,
):
    roots = [tmp_path / "one", tmp_path / "two"]
    for root in roots:
        initialize(str(root), _args(["--email-credential-provider", provider]))
    names = [
        {path.name for path in (root / "setup-systemd").iterdir()} for root in roots
    ]
    assert len(names[0]) == len(names[1]) == 3
    assert names[0].isdisjoint(names[1])
    for path in (roots[0] / "setup-systemd").iterdir():
        content = path.read_text()
        if path.suffix == ".timer":
            assert "OnUnitActiveSec=5min" in content
            assert "Unit=" + path.stem + ".service" in content
        else:
            if provider == "environment":
                assert f"EnvironmentFile={roots[0] / CREDENTIALS_FILE}" in content
            else:
                assert "EnvironmentFile=" not in content
            assert "UMask=0077" in content
            assert '"--sys-config-path"' in content
        if path.name.endswith("-fetch.service"):
            assert "main_oneshot.py" in content
            assert '"--email-fetch"' in content
            assert '"--email-root"' in content
        elif path.name.endswith("-watcher.service"):
            assert "main_daemon.py" in content
            assert "WantedBy=default.target" in content


def test_environment_credentials_are_explicit_private_and_documented(tmp_path: Path):
    root = tmp_path / "Mail"
    initialize(str(root), _args(["--email-credential-provider", "environment"]))
    credentials = root / CREDENTIALS_FILE

    assert credentials.exists()
    if os.name != "nt":
        assert credentials.stat().st_mode & 0o777 == 0o600
    assert all(line.startswith("#") for line in credentials.read_text().splitlines())
    welcome = (root / "welcome.md").read_text()
    assert "explicitly selects `--email-credential-provider environment`" in welcome
    assert "plaintext secrets" in welcome
    assert "--email-credentials-save" not in welcome


@pytest.mark.parametrize(
    "relative", [".email", "Actions/Send", "new email.md", ACCOUNT_FILE]
)
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
            replace(arg, value="sender@example.test\n--cmd bad")
            if arg.name == "email-from-address"
            else arg
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


def test_welcome_requires_account_exclusion_from_general_watcher(tmp_path: Path):
    root = tmp_path / "Email"
    initialize(str(root), _args())
    welcome = (root / "welcome.md").read_text()

    assert "--sys-ignore-paths " + str(root) in welcome
    assert "only `dropdir` and `email`" in welcome
    assert "do not disable Lucy's normal" in welcome


def test_account_uses_mailboxes_for_moves_and_refresh_file_for_fetch(tmp_path):
    root = tmp_path / "Email"
    initialize(str(root), _args())
    assert {path.name for path in (root / "Actions").iterdir()} == {
        "Reply",
        "Send",
        "Mark read",
        "Mark unread",
    }
    assert not (root / "Archive/init.md").exists()
    assert not (root / "Trash/init.md").exists()
    assert "Move this file" in (root / "refresh.md").read_text()
    welcome = (root / "welcome.md").read_text()
    assert "Actions/Refresh" not in welcome
    assert "Move messages directly into `Archive/`" in welcome
