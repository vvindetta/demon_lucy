from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from watchdog.events import (
    FileCreatedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileOpenedEvent,
)

import main_oneshot
import demon_lucy.modules.email.module as email_module
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.lib.args.sources import _parse_config_args
from demon_lucy.modules.email.files import locked_file
from demon_lucy.modules.email.documents import LITERAL_MARKER
from demon_lucy.module_manager import ModuleManager
from demon_lucy.modules.dropdir import DropDir
from demon_lucy.modules.email import Email
from demon_lucy.modules.email.codec import decode_message, parse_draft, render_draft
from demon_lucy.modules.email.config import TEMPLATE
from demon_lucy.modules.email.errors import EmailError
from demon_lucy.modules.email.models import DeliveryResult
from demon_lucy.modules.email.scaffold import WATCHER_FILE, initialize
from demon_lucy.modules.email.storage import MailStore, MessageRecord, fingerprint
from demon_lucy.runtime import DEMON_LUCY_STARTUP_TEMPLATE, select_demon_lucy_modules


@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.setattr(email_module, "safe_notify", lambda *args, **kwargs: None)
    root = tmp_path / "Email"
    initialize(
        str(root),
        parse_args(
            [
                "--email-imap-host",
                "imap.example.test",
                "--email-imap-username",
                "me",
                "--email-smtp-host",
                "smtp.example.test",
                "--email-smtp-username",
                "me",
                "--email-from-address",
                "me@example.test",
                "--email-sent-copy-mode",
                "server",
            ],
            TEMPLATE,
        ),
    )
    startup = _parse_config_args(str(root / WATCHER_FILE), DEMON_LUCY_STARTUP_TEMPLATE)
    manager = ModuleManager([DropDir(), Email()], startup)
    return root, manager


def _drop(root, manager, path, folder):
    destination = root / "Actions" / folder / path.name
    path.rename(destination)
    return manager.run(str(destination), FileMovedEvent(str(path), str(destination)))


def _ready(root):
    path = root / "new email.md"
    draft = parse_draft(path.read_text())
    path.write_text(
        render_draft(
            replace(
                draft,
                to="you@example.test",
                subject="Hello",
                body="Body\n--email-send\n",
            )
        )
    )
    return path, draft.identity


def _message(root):
    raw = b"From: Other <other@example.test>\r\nTo: me@example.test\r\nSubject: Received\r\nMessage-ID: <received@example.test>\r\n\r\n--email-send\r\n"
    with MailStore(str(root)) as store:
        identity = uuid.uuid4().hex
        record = MessageRecord(
            identity,
            "Inbox",
            "INBOX",
            12,
            5,
            "",
            f".email/raw/.{identity}.eml",
            False,
            fingerprint(raw),
            raw_bytes=len(raw),
        )
        store.write_blob(record.raw_path, raw)
        store.display(record, decode_message(raw))
        return Path(store.path(record.path))


def test_email_is_optional_and_available():
    assert (
        "email"
        not in parse_args([], DEMON_LUCY_STARTUP_TEMPLATE).require("sys-modules").value
    )
    assert [
        module.name
        for module in select_demon_lucy_modules(
            include_names=["email"], exclude_names=[]
        )
    ] == ["email"]


def test_real_oneshot_initializes_without_a_target(tmp_path, monkeypatch):
    monkeypatch.setattr(email_module, "safe_notify", lambda *args, **kwargs: None)
    root = tmp_path / "mail"
    args = parse_args(
        ["--sys-modules", "email", "--email-init", str(root)],
        main_oneshot.ONESHOT_STARTUP_TEMPLATE,
    )
    assert main_oneshot.run_oneshot(args) == 0
    assert parse_draft((root / "new email.md").read_text()).body == ""


def test_initialization_from_note_consumes_flag(tmp_path):
    path = tmp_path / "setup.md"
    path.write_text('--email-init "Email"\n')
    manager = ModuleManager([Email()], parse_args([], DEMON_LUCY_STARTUP_TEMPLATE))
    manager.run(str(path), FileModifiedEvent(str(path)))
    assert "--email-init" not in path.read_text()
    assert (tmp_path / "Email/new email.md").exists()


def test_send_drop_returns_starter_sends_once_and_resets(account, monkeypatch):
    root, manager = account
    path, original = _ready(root)
    sent = []

    def send(settings, sender, recipients, payload, *, before_data):
        assert path.exists()
        assert not (root / "Actions/Send" / path.name).exists()
        before_data()
        sent.append(payload)
        return DeliveryResult(recipients, ())

    monkeypatch.setattr("demon_lucy.modules.email.smtp.send", send)
    changed = _drop(root, manager, path, "Send")
    assert len(sent) == 1
    assert changed[str(path)] >= 1
    assert parse_draft(path.read_text()).identity != original
    assert parse_draft(path.read_text()).to == ""
    assert len(list((root / "Sent").glob("*.md"))) == 1
    assert "Delivery: sent" in (root / "status.md").read_text()


@pytest.mark.parametrize(
    "factory", [FileCreatedEvent, FileModifiedEvent, FileOpenedEvent]
)
def test_saving_opening_importing_draft_never_sends(account, monkeypatch, factory):
    root, manager = account
    path, _ = _ready(root)
    monkeypatch.setattr(
        email_module,
        "send_draft",
        lambda *args, **kwargs: pytest.fail("unexpected send"),
    )
    manager.run(str(path), factory(str(path)))


def test_quoted_body_cannot_delay_or_add_dropdir_actions(account, monkeypatch):
    root, manager = account
    path, _ = _ready(root)
    draft = parse_draft(path.read_text())
    body = '--dropdir-action-delay-milliseconds 60000\n--dropdir-action "--email-send" "Send"\n--email-init unwanted\n'
    path.write_text(render_draft(replace(draft, body=body)))
    sent = []

    def send(settings, sender, recipients, payload, *, before_data):
        before_data()
        sent.append(payload)
        return DeliveryResult(recipients, ())

    monkeypatch.setattr(
        "demon_lucy.modules.dropdir.module.time.sleep",
        lambda _: pytest.fail("draft body became a delay command"),
    )
    monkeypatch.setattr("demon_lucy.modules.email.smtp.send", send)
    _drop(root, manager, path, "Send")
    assert len(sent) == 1
    assert decode_message(sent[0]).body == body
    assert not (root / "unwanted").exists()


def test_credentials_save_is_explicit_cli_only(account, monkeypatch):
    from demon_lucy.modules.email import credentials

    root, manager = account
    saved = []
    monkeypatch.setattr(
        credentials, "save_credentials", lambda config: saved.append(config.root)
    )
    path = root / "request.md"
    path.write_text(f'--email-credentials-save --email-root "{root}"\n')
    manager.run(str(path), FileModifiedEvent(str(path)))
    assert saved == []
    args = parse_args(
        ["--email-root", str(root), "--email-credentials-save"],
        [*DEMON_LUCY_STARTUP_TEMPLATE, *TEMPLATE],
    )
    ModuleManager([Email()], args, run_mode="cli").run_cli()
    assert saved == [str(root)]


def test_startup_action_flag_cannot_send_without_drop_provenance(account, monkeypatch):
    root, _ = account
    path, _ = _ready(root)
    args = parse_args(
        ["--email-root", str(root), "--email-send"],
        [*DEMON_LUCY_STARTUP_TEMPLATE, *TEMPLATE],
    )
    manager = ModuleManager([Email()], args)
    monkeypatch.setattr(
        email_module,
        "send_draft",
        lambda *args, **kwargs: pytest.fail("unexpected send"),
    )
    manager.run(str(path), FileMovedEvent(str(root / "previous.md"), str(path)))


def test_reply_drop_creates_registered_threaded_draft(account):
    root, manager = account
    message = _message(root)
    _drop(root, manager, message, "Reply")
    assert message.exists()
    drafts = list((root / "Drafts").glob("*.md"))
    assert len(drafts) == 1
    draft = parse_draft(drafts[0].read_text())
    assert draft.to == "Other <other@example.test>"
    assert draft.in_reply_to == "<received@example.test>"
    with MailStore(str(root)) as store:
        assert store.read_draft(str(drafts[0]), 10000)[0] == draft


def test_refresh_drop_and_cli_use_same_fetch(account, monkeypatch):
    root, manager = account
    fetched = []
    monkeypatch.setattr(
        email_module,
        "fetch_mail",
        lambda config, store, **kwargs: fetched.append(config.root),
    )
    monkeypatch.setattr(email_module, "recover_sent", lambda *args, **kwargs: None)
    _drop(root, manager, root / "refresh.md", "Refresh")
    args = parse_args(
        ["--email-root", str(root), "--email-fetch"],
        [*DEMON_LUCY_STARTUP_TEMPLATE, *TEMPLATE],
    )
    cli = ModuleManager([Email()], args, run_mode="cli")
    cli.run_cli()
    assert fetched == [str(root), str(root)]


def test_failed_move_back_does_not_send(account, monkeypatch):
    root, manager = account
    original, _ = _ready(root)
    destination = root / "Actions/Send" / original.name
    destination.write_text(original.read_text())
    monkeypatch.setattr(
        email_module,
        "send_draft",
        lambda *args, **kwargs: pytest.fail("unexpected send"),
    )
    manager.run(str(destination), FileMovedEvent(str(original), str(destination)))
    assert original.exists() and destination.exists()


def test_cross_account_drop_does_not_send(account, monkeypatch):
    root, manager = account
    outside = root.parent / "other.md"
    outside.write_text(LITERAL_MARKER + "\n")
    monkeypatch.setattr(
        email_module,
        "send_draft",
        lambda *args, **kwargs: pytest.fail("unexpected send"),
    )
    _drop(root, manager, outside, "Send")
    assert outside.exists()


def test_failed_send_preserves_draft_and_reports_error(account, monkeypatch):
    root, manager = account
    path, _ = _ready(root)
    before = path.read_text()

    def failure(*args, **kwargs):
        raise EmailError(
            "Try again after reconnecting.", reason="smtp_unavailable", retryable=True
        )

    monkeypatch.setattr(email_module, "send_draft", failure)
    _drop(root, manager, path, "Send")
    assert path.read_text() == before
    assert "Try again after reconnecting." in (root / "status.md").read_text()


def test_busy_account_drop_returns_file_without_popup_or_network(account, monkeypatch):
    root, manager = account
    path, _ = _ready(root)
    monkeypatch.setattr(
        email_module, "safe_notify", lambda *args, **kwargs: pytest.fail("busy popup")
    )
    monkeypatch.setattr(
        email_module,
        "send_draft",
        lambda *args, **kwargs: pytest.fail("unexpected send"),
    )
    with locked_file(str(root / ".email/.lock")):
        _drop(root, manager, path, "Send")
    assert path.exists()
