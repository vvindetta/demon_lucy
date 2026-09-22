from __future__ import annotations

import uuid
import threading
import errno
from contextlib import contextmanager
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
from demon_lucy.file_handler import FileHandler
from demon_lucy.modules.dropdir import DropDir
from demon_lucy.modules.email import Email
from demon_lucy.modules.email.codec import decode_message, parse_draft, render_draft
from demon_lucy.modules.email.config import ACTION_FOLDERS, TEMPLATE, load_account
from demon_lucy.modules.email.errors import EmailError
from demon_lucy.modules.email.models import (
    DeliveryResult,
    MailboxInfo,
    MessageInfo,
    MoveResult,
)
from demon_lucy.modules.email.scaffold import WATCHER_FILE, initialize
from demon_lucy.modules.email.storage import MailStore, MessageRecord, fingerprint
from demon_lucy.modules.email.sync import fetch_mail
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
    relative = next(value for value in ACTION_FOLDERS if Path(value).name == folder)
    destination = root / relative / path.name
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
    source = root / "refresh.md"
    destination = root / "Drafts" / "refresh.md"
    source.rename(destination)
    manager.run(str(destination), FileMovedEvent(str(source), str(destination)))
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


@pytest.mark.parametrize(
    "relative",
    [
        "Drafts/refresh.md",
        "Archive/refresh.md",
        "Trash/refresh.md",
        "Actions/Send/refresh.md",
        "Actions/Reply/refresh.md",
        "Actions/Mark read/refresh.md",
        "elsewhere/moved.md",
        "renamed.md",
        "../outside.md",
    ],
)
def test_refresh_move_returns_before_fetch_and_deduplicates(
    account, monkeypatch, relative
):
    root, manager = account
    source = root / "refresh.md"
    original = source.read_bytes()
    destination = (root / relative).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fetched = []

    def fetch(config, store, **kwargs):
        assert source.read_bytes() == original
        assert not destination.exists()
        fetched.append(config.root)

    monkeypatch.setattr(email_module, "fetch_mail", fetch)
    monkeypatch.setattr(email_module, "recover_sent", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        email_module,
        "send_draft",
        lambda *args, **kwargs: pytest.fail("refresh sent mail"),
    )
    monkeypatch.setattr(
        email_module,
        "change_message",
        lambda *args, **kwargs: pytest.fail("refresh changed a message"),
    )
    monkeypatch.setattr(
        email_module,
        "reply_draft",
        lambda *args, **kwargs: pytest.fail("refresh replied"),
    )
    source.rename(destination)
    event = FileMovedEvent(str(source), str(destination))
    changed = manager.run(str(destination), event, event_id="refresh-drop")
    assert changed[str(source)] >= 1
    assert changed[str(destination)] >= 1
    manager.run(str(destination), event, event_id="refresh-drop")
    manager.run(str(destination), event, event_id="duplicate-event")
    manager.run(str(source), FileMovedEvent(str(destination), str(source)))
    assert fetched == [str(root)]
    assert source.read_bytes() == original


def test_refresh_return_from_another_account_does_not_fetch_second_account(
    account, monkeypatch
):
    root, manager = account
    other = root.parent / "other-account"
    initialize(str(other), parse_args([], TEMPLATE))
    (other / "refresh.md").unlink()
    fetched = []
    monkeypatch.setattr(
        email_module,
        "fetch_mail",
        lambda config, store, **kwargs: fetched.append(config.root),
    )
    monkeypatch.setattr(email_module, "recover_sent", lambda *args, **kwargs: None)
    source, destination = root / "refresh.md", other / "refresh.md"
    source.rename(destination)
    manager.run(str(destination), FileMovedEvent(str(source), str(destination)))
    manager.run(str(source), FileMovedEvent(str(destination), str(source)))
    assert fetched == [str(root)]
    assert source.exists() and not destination.exists()


@pytest.mark.parametrize("failure", ["occupied", "foreign", "symlink"])
def test_invalid_refresh_move_preserves_both_paths_without_fetch(
    account, monkeypatch, failure
):
    root, manager = account
    source = root / "refresh.md"
    original = source.read_bytes()
    destination = root / "Drafts/refresh.md"
    source.rename(destination)
    if failure == "occupied":
        source.write_text("replacement token")
    elif failure == "foreign":
        destination.write_text("not a Lucy email token")
    else:
        target = root / "target.md"
        destination.rename(target)
        destination.symlink_to(target)
    monkeypatch.setattr(
        email_module,
        "fetch_mail",
        lambda *args, **kwargs: pytest.fail("invalid refresh fetched"),
    )
    manager.run(str(destination), FileMovedEvent(str(source), str(destination)))
    assert destination.exists()
    if failure == "occupied":
        assert source.read_text() == "replacement token"
        assert destination.read_bytes() == original
    else:
        assert not source.exists()


def test_refresh_cross_filesystem_return_and_failed_fetch_keep_token(
    account, monkeypatch
):
    root, manager = account
    source, destination = root / "refresh.md", root.parent / "outside.md"
    original = source.read_bytes()
    source.rename(destination)

    def cross_filesystem(*args, **kwargs):
        raise OSError(errno.EXDEV, "different devices")

    def unavailable(*args, **kwargs):
        assert source.read_bytes() == original
        raise EmailError(
            "Refresh failed; reconnect.", reason="imap_unavailable", retryable=True
        )

    monkeypatch.setattr(email_module.os, "link", cross_filesystem)
    monkeypatch.setattr(email_module, "fetch_mail", unavailable)
    monkeypatch.setattr(email_module, "recover_sent", lambda *args, **kwargs: None)
    manager.run(str(destination), FileMovedEvent(str(source), str(destination)))
    assert source.read_bytes() == original
    assert not destination.exists()
    assert "Refresh failed; reconnect." in (root / "status.md").read_text()


def _mailbox_session(monkeypatch, source, destination, *, failure=False):
    moves = []

    class Session:
        def __init__(self, settings):
            pass

        def __enter__(self):
            assert source.exists()
            assert not destination.exists()
            if failure:
                raise EmailError(
                    "Mailbox authentication failed.", reason="imap_authentication"
                )
            return self

        def __exit__(self, *args):
            pass

        def select(self, mailbox):
            assert mailbox == "INBOX"
            return 12

        def uids(self):
            return [5]

        def mailboxes(self):
            return [
                MailboxInfo(name, frozenset({flag}))
                for name, flag in (
                    ("INBOX", ""),
                    ("Archive", "\\Archive"),
                    ("Trash", "\\Trash"),
                    ("Sent", "\\Sent"),
                )
            ]

        def move(self, uid, mailbox, *, checkpoint):
            moves.append((uid, mailbox))
            checkpoint("moving", {})
            return MoveResult(12, 9)

    monkeypatch.setattr("demon_lucy.modules.email.sync.ImapSession", Session)
    return moves


@pytest.mark.parametrize("folder", ["Archive", "Trash"])
def test_direct_mailbox_drop_returns_source_then_moves_remote(
    account, monkeypatch, folder
):
    root, manager = account
    source = _message(root)
    destination = root / folder / source.name
    assert not (root / folder / "init.md").exists()
    moves = _mailbox_session(monkeypatch, source, destination)
    changed = _drop(root, manager, source, folder)
    assert moves == [(5, folder)]
    assert not source.exists()
    with MailStore(str(root)) as store:
        (record,) = store.records()
        assert record.folder == folder and record.mailbox == folder and record.uid == 9
        assert Path(store.path(record.path)).exists()
        assert changed[store.path(record.path)] >= 1
    # A fresh watcher has no in-memory ignore counts or dedup history.
    observer = ModuleManager([DropDir(), Email()], manager.args)
    handler = FileHandler(observer, open_cooldown_seconds=0)
    handler.on_moved(FileMovedEvent(str(source), str(destination)))
    assert moves == [(5, folder)]


@pytest.mark.parametrize("folder", ["Archive", "Trash"])
def test_direct_mailbox_auth_failure_leaves_original_source(
    account, monkeypatch, folder
):
    root, manager = account
    source = _message(root)
    original = source.read_bytes()
    destination = root / folder / source.name
    moves = _mailbox_session(monkeypatch, source, destination, failure=True)
    _drop(root, manager, source, folder)
    assert source.read_bytes() == original
    assert not destination.exists()
    assert moves == []
    assert "Mailbox authentication failed." in (root / "status.md").read_text()


def test_direct_mailbox_drop_does_not_overwrite_recreated_source(account, monkeypatch):
    root, manager = account
    source = _message(root)
    original = source.read_bytes()
    destination = root / "Archive" / source.name
    source.rename(destination)
    source.write_text("local replacement")
    monkeypatch.setattr(
        email_module,
        "change_message",
        lambda *args, **kwargs: pytest.fail("conflicting drop reached IMAP"),
    )
    manager.run(str(destination), FileMovedEvent(str(source), str(destination)))
    assert source.read_text() == "local replacement"
    assert destination.read_bytes() == original


def test_mailbox_same_folder_rename_and_missing_drop_are_noops(account, monkeypatch):
    root, manager = account
    original = root / "Archive/before.md"
    destination = root / "Archive/after.md"
    original.write_text("ordinary file")
    original.rename(destination)
    monkeypatch.setattr(
        email_module,
        "change_message",
        lambda *args, **kwargs: pytest.fail("rename reached IMAP"),
    )
    monkeypatch.setattr(
        email_module,
        "safe_notify",
        lambda *args, **kwargs: pytest.fail("stale event popup"),
    )
    manager.run(str(destination), FileMovedEvent(str(original), str(destination)))
    manager.run(
        str(root / "Trash/missing.md"),
        FileMovedEvent(str(root / "Inbox/missing.md"), str(root / "Trash/missing.md")),
    )
    assert destination.read_text() == "ordinary file"


@pytest.mark.parametrize("internal", [False, True])
@pytest.mark.parametrize("folder", ["Archive", "Trash"])
def test_separate_watcher_waits_for_account_lock_before_classifying_move(
    account, monkeypatch, internal, folder
):
    root, manager = account
    source = _message(root)
    destination = root / folder / source.name
    observer = ModuleManager([DropDir(), Email()], manager.args)
    handler = FileHandler(observer, open_cooldown_seconds=0)
    waiting = threading.Event()
    finished = threading.Event()
    errors = []

    @contextmanager
    def observed_lock(path, *, blocking=False):
        if blocking:
            waiting.set()
        with locked_file(path, blocking=blocking):
            yield

    def dispatch():
        try:
            handler.on_moved(FileMovedEvent(str(source), str(destination)))
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()

    monkeypatch.setattr(email_module, "locked_file", observed_lock)
    if internal:
        moves = []
        monkeypatch.setattr(
            email_module,
            "change_message",
            lambda *args, **kwargs: pytest.fail("generated move reached IMAP"),
        )
    else:
        moves = _mailbox_session(monkeypatch, source, destination)
    with locked_file(str(root / ".email/.lock")):
        source.rename(destination)
        worker = threading.Thread(target=dispatch, daemon=True)
        worker.start()
        assert waiting.wait(5)
        assert not finished.is_set()
        assert destination.exists() and not source.exists()
        if internal:
            # Another process commits this binding only after the filesystem move.
            with MailStore(str(root)) as store:
                (record,) = store.records()
                record.folder = folder
                record.mailbox = folder
                record.path = store.relative(str(destination))
                store.save_record(record)
    worker.join(5)
    assert not worker.is_alive()
    assert errors == []
    assert moves == ([] if internal else [(5, folder)])
    assert destination.exists() and not source.exists()


@pytest.mark.parametrize("folder", ["Archive", "Trash"])
def test_cli_timer_display_move_cannot_be_undone_by_separate_watcher(
    account, monkeypatch, folder
):
    root, watcher = account
    source = _message(root)
    destination = root / folder / source.name
    handler = FileHandler(watcher, open_cooldown_seconds=0)
    waiting = threading.Event()
    failures = []
    threads = []
    save_record = MailStore.save_record

    @contextmanager
    def observed_lock(path, *, blocking=False):
        if blocking:
            waiting.set()
        with locked_file(path, blocking=blocking):
            yield

    def dispatch():
        try:
            handler.on_moved(FileMovedEvent(str(source), str(destination)))
        except BaseException as error:
            failures.append(error)

    def save_after_watcher_starts(store, record):
        assert destination.exists() and not source.exists()
        thread = threading.Thread(target=dispatch, daemon=True)
        threads.append(thread)
        thread.start()
        assert waiting.wait(5)
        assert thread.is_alive()
        # A generic DropDir init would already have moved this back before Email.
        assert destination.exists() and not source.exists()
        save_record(store, record)

    def remote_move(config, store, **kwargs):
        (record,) = store.records()
        raw = store.raw(record, record.raw_bytes)
        record.folder = folder
        record.mailbox = folder
        store.display(record, decode_message(raw))

    monkeypatch.setattr(email_module, "locked_file", observed_lock)
    monkeypatch.setattr(MailStore, "save_record", save_after_watcher_starts)
    monkeypatch.setattr(email_module, "fetch_mail", remote_move)
    monkeypatch.setattr(email_module, "recover_sent", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        email_module,
        "change_message",
        lambda *args, **kwargs: pytest.fail("timer relocation reached IMAP"),
    )
    args = parse_args(
        ["--email-root", str(root), "--email-fetch"],
        [*DEMON_LUCY_STARTUP_TEMPLATE, *TEMPLATE],
    )
    timer = ModuleManager([Email()], args, run_mode="cli")
    timer.run_cli()
    assert len(threads) == 1
    threads[0].join(5)
    assert not threads[0].is_alive()
    assert failures == []
    assert destination.exists() and not source.exists()
    with MailStore(str(root)) as store:
        (record,) = store.records()
        assert store.path(record.path) == str(destination)


@pytest.mark.parametrize("folder", ["Archive", "Trash"])
@pytest.mark.parametrize("edited", [None, "source", "both"])
def test_waiting_mailbox_drop_reconciles_only_exact_source_recreated_by_fetch(
    account, monkeypatch, folder, edited
):
    root, manager = account
    source = _message(root)
    original = source.read_bytes()
    destination = root / folder / source.name
    handler = FileHandler(manager, open_cooldown_seconds=0)
    waiting = threading.Event()
    failures = []
    moves = []

    class Session:
        def __init__(self, settings):
            self.mailbox = ""

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def mailboxes(self):
            return [
                MailboxInfo("INBOX", frozenset()),
                MailboxInfo(folder, frozenset({"\\" + folder})),
            ]

        def select(self, mailbox, readonly=False):
            self.mailbox = mailbox
            return 12

        def uids(self):
            return [5] if self.mailbox == "INBOX" else []

        def metadata(self, uids):
            return {uid: MessageInfo(uid, frozenset(), 100) for uid in uids}

        def fetch(self, *args):
            pytest.fail("cached message was downloaded again")

        def move(self, uid, mailbox, *, checkpoint):
            assert source.read_bytes() == original
            assert not destination.exists()
            checkpoint("moving", {})
            moves.append((uid, mailbox))
            return MoveResult(12, 9)

    @contextmanager
    def observed_lock(path, *, blocking=False):
        if blocking:
            waiting.set()
        with locked_file(path, blocking=blocking):
            yield

    def dispatch():
        try:
            handler.on_moved(FileMovedEvent(str(source), str(destination)))
        except BaseException as error:
            failures.append(error)

    monkeypatch.setattr("demon_lucy.modules.email.sync.ImapSession", Session)
    monkeypatch.setattr(email_module, "locked_file", observed_lock)
    with locked_file(str(root / ".email/.lock")):
        source.rename(destination)
        worker = threading.Thread(target=dispatch, daemon=True)
        worker.start()
        assert waiting.wait(5)
        assert not source.exists()
        with MailStore(str(root)) as store:
            fetch_mail(load_account(str(root)), store, event_id="timer-fetch")
        assert source.read_bytes() == original
        if edited:
            # Equal markers are insufficient; local edits must never be discarded.
            source.write_bytes(original + b"\nAn annotation added during refresh.\n")
            if edited == "both":
                destination.write_bytes(source.read_bytes())
    worker.join(5)
    assert not worker.is_alive()
    assert failures == []
    if edited:
        assert moves == []
        assert (
            source.read_bytes() == original + b"\nAn annotation added during refresh.\n"
        )
        assert destination.read_bytes() == (
            source.read_bytes() if edited == "both" else original
        )
    else:
        assert moves == [(5, folder)]
        assert not source.exists()
        with MailStore(str(root)) as store:
            (record,) = store.records()
            assert record.folder == folder and record.uid == 9
            assert store.path(record.path) == str(destination)
        assert destination.exists()
