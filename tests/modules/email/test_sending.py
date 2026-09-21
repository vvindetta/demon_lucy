from dataclasses import replace
from pathlib import Path
import uuid

import pytest

from demon_lucy.modules.email import sending
from demon_lucy.modules.email.codec import (
    decode_message,
    new_draft,
    parse_draft,
    render_draft,
)
from demon_lucy.modules.email.errors import EmailError
from demon_lucy.modules.email.models import (
    AccountConfig,
    CredentialProvider,
    DeliveryResult,
    MailboxInfo,
    MoveResult,
    Security,
    SentCopyMode,
    ServerSettings,
)
from demon_lucy.modules.email.storage import MailStore, MessageRecord, fingerprint


class SentServer:
    def __init__(self):
        self.messages = {}
        self.calls = []
        self.append_error = None
        self.save_before_error = False

    def __call__(self, settings):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def mailboxes(self):
        return [
            MailboxInfo("Sent", frozenset({r"\Sent"})),
            MailboxInfo("INBOX", frozenset()),
        ]

    def find_message(self, mailbox, message_id):
        self.calls.append(("find", mailbox, message_id))
        return [
            uid
            for uid, raw in self.messages.items()
            if decode_message(raw).message_id == message_id
        ]

    def select(self, mailbox, readonly=False):
        return 7

    def fetch(self, uid, max_bytes):
        return self.messages[uid]

    def append(self, mailbox, payload):
        self.calls.append(("append", mailbox))
        if self.append_error and not self.save_before_error:
            raise self.append_error
        self.messages[81] = payload
        if self.append_error:
            raise self.append_error
        return MoveResult(7, 81)


@pytest.fixture
def account(tmp_path, monkeypatch):
    root = tmp_path / "Email"
    root.mkdir()
    settings = ServerSettings(
        "example.test",
        993,
        Security.TLS,
        "alice",
        "LUCY_MAIL_PASSWORD",
        30,
        CredentialProvider.ENVIRONMENT,
    )
    config = AccountConfig(
        str(root),
        "alice@example.test",
        settings,
        settings,
        {"Inbox": "INBOX", "Sent": "Sent"},
        100,
        1024 * 1024,
        SentCopyMode.SERVER,
    )
    server = SentServer()
    monkeypatch.setattr(sending, "ImapSession", server)
    with MailStore(str(root)) as store:
        yield config, store, server


def draft_in(store, relative="new email.md", **changes):
    draft = replace(
        new_draft(uuid.uuid4().hex),
        to="bob@example.test",
        subject="A test",
        body="Hello Bob\n",
        **changes,
    )
    store.create_draft(draft, relative)
    return draft, store.path(relative)


def fake_delivery(monkeypatch, store, *, result=None, error=None, after_data=None):
    calls = []

    def send(settings, sender, recipients, payload, *, before_data):
        calls.append((sender, recipients, payload))
        journal = next(
            value for _, value in store.items("send") if value["state"] == "prepared"
        )
        assert Path(store.path(journal["payload_path"])).read_bytes() == payload
        before_data()
        assert any(value["state"] == "sending" for _, value in store.items("send"))
        if after_data:
            after_data()
        if error:
            raise error
        return result or DeliveryResult(tuple(recipients), ())

    monkeypatch.setattr(sending.smtp, "send", send)
    return calls


def submit(config, store, path):
    return sending.send_draft(config, store, path, event_id="email-test")


def test_successful_starter_reset_keeps_sent_generation_journal(account, monkeypatch):
    config, store, _ = account
    draft, path = draft_in(store)
    calls = fake_delivery(monkeypatch, store)
    assert submit(config, store, path) == path
    fresh, relative, _ = store.read_draft(path, config.max_message_bytes)
    assert fresh.identity != draft.identity and fresh.to == "" and fresh.body == ""
    assert relative == "new email.md"
    journal = store.get("send", draft.identity)
    assert journal["state"] == "sent" and journal["local_done"] and journal["copy_done"]
    assert journal["accepted"] == ["bob@example.test"]
    assert len(calls) == 1
    saved = store.records()[0]
    assert saved.folder == "Sent"
    assert "Hello Bob" in Path(store.path(saved.path)).read_text()
    assert decode_message(calls[0][2]).message_id == journal["message_id"]


def test_successful_reply_draft_is_preserved_privately_and_returns_sent_path(
    account, monkeypatch
):
    config, store, _ = account
    draft, path = draft_in(store, "Drafts/reply.md")
    original = Path(path).read_text()
    fake_delivery(monkeypatch, store)
    result = submit(config, store, path)
    assert not Path(path).exists()
    assert result == store.path(store.records()[0].path)
    assert (
        Path(store.path(f".email/outgoing/.{draft.identity}.sent-draft")).read_text()
        == original
    )


def test_replaying_old_generation_never_submits_twice(account, monkeypatch):
    config, store, _ = account
    draft, path = draft_in(store)
    original = Path(path).read_text()
    calls = fake_delivery(monkeypatch, store)
    submit(config, store, path)
    # A duplicate watchdog event or restored copy can contain the old identity.
    Path(path).write_text(original)
    with pytest.raises(EmailError) as raised:
        submit(config, store, path)
    assert raised.value.reason == "draft_already_submitted"
    assert len(calls) == 1 and store.get("send", draft.identity)["state"] == "sent"


def test_partial_delivery_preserves_starter_and_accepted_recipients(
    account, monkeypatch
):
    config, store, _ = account
    draft, path = draft_in(store, cc="rejected@example.test")
    original = Path(path).read_text()
    calls = fake_delivery(
        monkeypatch,
        store,
        result=DeliveryResult(("bob@example.test",), ("rejected@example.test",)),
    )
    with pytest.raises(EmailError) as raised:
        submit(config, store, path)
    assert raised.value.reason == "partial_delivery"
    journal = store.get("send", draft.identity)
    assert journal["state"] == "partial"
    assert journal["accepted"] == ["bob@example.test"] and journal["refused"] == [
        "rejected@example.test"
    ]
    assert Path(path).read_text() == original
    with pytest.raises(EmailError) as raised:
        submit(config, store, path)
    assert raised.value.reason == "draft_already_submitted" and len(calls) == 1
    sending.recover_sent(config, store, event_id="retry")
    assert len(calls) == 1 and Path(path).read_text() == original


def test_uncertain_data_disconnect_blocks_all_future_submissions(account, monkeypatch):
    config, store, _ = account
    draft, path = draft_in(store)
    original = Path(path).read_text()
    error = EmailError(
        "No final SMTP reply.",
        reason="smtp_delivery_uncertain",
        delivery_uncertain=True,
        accepted=("bob@example.test",),
    )
    calls = fake_delivery(monkeypatch, store, error=error)
    with pytest.raises(EmailError) as raised:
        submit(config, store, path)
    assert raised.value.delivery_uncertain
    journal = store.get("send", draft.identity)
    assert journal["state"] == "uncertain" and journal["accepted"] == [
        "bob@example.test"
    ]
    with pytest.raises(EmailError) as raised:
        submit(config, store, path)
    assert raised.value.reason == "draft_already_submitted"
    sending.recover_sent(config, store, event_id="retry")
    assert len(calls) == 1 and Path(path).read_text() == original


def test_process_crash_after_data_boundary_becomes_uncertain(account, monkeypatch):
    config, store, _ = account
    draft, path = draft_in(store)

    def crash():
        raise KeyboardInterrupt

    calls = fake_delivery(monkeypatch, store, after_data=crash)
    with pytest.raises(KeyboardInterrupt):
        submit(config, store, path)
    assert store.get("send", draft.identity)["state"] == "sending"
    sending.recover_sent(config, store, event_id="restart")
    assert store.get("send", draft.identity)["state"] == "uncertain"
    with pytest.raises(EmailError) as raised:
        submit(config, store, path)
    assert raised.value.reason == "draft_already_submitted" and len(calls) == 1


def test_definitive_rejection_can_be_retried_without_losing_draft(account, monkeypatch):
    config, store, _ = account
    draft, path = draft_in(store)
    error = EmailError(
        "Message rejected.", reason="smtp_data_rejected", refused=("bob@example.test",)
    )
    first = fake_delivery(monkeypatch, store, error=error)
    with pytest.raises(EmailError):
        submit(config, store, path)
    assert store.get("send", draft.identity)["state"] == "failed"
    second = fake_delivery(monkeypatch, store)
    submit(config, store, path)
    assert len(first) == len(second) == 1
    assert store.get("send", draft.identity)["state"] == "sent"


def test_edits_during_smtp_are_preserved_with_new_identity(account, monkeypatch):
    config, store, _ = account
    draft, path = draft_in(store)

    def edit():
        current = parse_draft(Path(path).read_text())
        Path(path).write_text(
            render_draft(replace(current, body="Edited while sending\n"))
        )

    calls = fake_delivery(monkeypatch, store, after_data=edit)
    submit(config, store, path)
    fresh, _, _ = store.read_draft(path, config.max_message_bytes)
    assert fresh.identity != draft.identity and fresh.body == "Edited while sending\n"
    assert fresh.to == "bob@example.test"
    assert decode_message(calls[0][2]).body == "Hello Bob\n"
    assert store.get("send", draft.identity)["state"] == "sent"


def test_edit_between_snapshot_and_retirement_remains_visible(account, monkeypatch):
    config, store, _ = account
    draft, path = draft_in(store, "Drafts/reply.md")
    calls = fake_delivery(monkeypatch, store)
    rename = sending.os.rename
    edited = False

    def edit_before_retirement(source, destination, *args, **kwargs):
        nonlocal edited
        if str(source) == path and str(destination).endswith(".sent-draft"):
            edited = True
            current = parse_draft(Path(path).read_text())
            Path(path).write_text(
                render_draft(replace(current, body="Late editor change\n"))
            )
        return rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(sending.os, "rename", edit_before_retirement)
    submit(config, store, path)
    assert edited
    assert Path(path).is_file(), "The unsent edit must remain visible in Drafts"
    current, _, _ = store.read_draft(path, config.max_message_bytes)
    assert current.identity != draft.identity and current.body == "Late editor change\n"
    assert decode_message(calls[0][2]).body == "Hello Bob\n"


@pytest.mark.parametrize("late_edit", [False, True])
def test_retirement_crash_recovers_snapshot_without_resending(
    account, monkeypatch, late_edit
):
    config, store, _ = account
    draft, path = draft_in(store, "Drafts/reply.md")
    calls = fake_delivery(monkeypatch, store)
    rename = sending.os.rename

    def crash_after_retirement(source, destination, *args, **kwargs):
        if str(source) == path and str(destination).endswith(".sent-draft"):
            if late_edit:
                current = parse_draft(Path(path).read_text())
                Path(path).write_text(
                    render_draft(replace(current, body="Unsent late edit\n"))
                )
            rename(source, destination, *args, **kwargs)
            raise OSError("process stopped after retirement")
        return rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(sending.os, "rename", crash_after_retirement)
    with pytest.raises((OSError, EmailError)):
        submit(config, store, path)
    journal = store.get("send", draft.identity)
    assert journal["state"] == "sent" and journal.get("retirement_path")
    assert not journal.get("local_done")
    monkeypatch.setattr(sending.os, "rename", rename)
    sending.recover_sent(config, store, event_id="restart")
    assert len(calls) == 1 and store.get("send", draft.identity)["local_done"]
    if late_edit:
        recovered, _, _ = store.read_draft(path, config.max_message_bytes)
        assert (
            recovered.identity != draft.identity
            and recovered.body == "Unsent late edit\n"
        )
    else:
        assert not Path(path).exists()


def test_retirement_preserves_recreated_path_and_captured_edit(account, monkeypatch):
    config, store, _ = account
    draft, path = draft_in(store, "Drafts/reply.md")
    calls = fake_delivery(monkeypatch, store)
    rename = sending.os.rename

    def recreate_during_retirement(source, destination, *args, **kwargs):
        if str(source) == path and str(destination).endswith(".sent-draft"):
            current = parse_draft(Path(path).read_text())
            Path(path).write_text(
                render_draft(replace(current, body="Captured unsent edit\n"))
            )
            result = rename(source, destination, *args, **kwargs)
            Path(path).write_text(
                render_draft(replace(current, body="New editor version\n"))
            )
            return result
        return rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(sending.os, "rename", recreate_during_retirement)
    submit(config, store, path)
    drafts = [
        store.read_draft(str(item), config.max_message_bytes)[0]
        for item in Path(config.root, "Drafts").glob("*.md")
    ]
    assert {item.body for item in drafts} == {
        "Captured unsent edit\n",
        "New editor version\n",
    }
    assert len({item.identity for item in drafts}) == 2 and all(
        item.identity != draft.identity for item in drafts
    )
    assert len(calls) == 1


def test_sent_copy_append_occurs_once_after_smtp_acceptance(account, monkeypatch):
    config, store, server = account
    config = replace(config, sent_copy=SentCopyMode.APPEND)
    draft, path = draft_in(store)
    calls = fake_delivery(monkeypatch, store)
    submit(config, store, path)
    journal = store.get("send", draft.identity)
    assert journal["append_started"] and journal["copy_done"]
    record = store.records()[0]
    assert (record.mailbox, record.uidvalidity, record.uid) == ("Sent", 7, 81)
    sending.recover_sent(config, store, event_id="retry")
    assert (
        len(calls) == 1
        and len([call for call in server.calls if call[0] == "append"]) == 1
    )


def test_lost_append_reply_reconciles_existing_copy_without_resending(
    account, monkeypatch
):
    config, store, server = account
    config = replace(config, sent_copy=SentCopyMode.APPEND)
    draft, path = draft_in(store)
    server.append_error = EmailError(
        "Disconnected after append.", reason="imap_unavailable", retryable=True
    )
    server.save_before_error = True
    calls = fake_delivery(monkeypatch, store)
    with pytest.raises(EmailError):
        submit(config, store, path)
    journal = store.get("send", draft.identity)
    assert (
        journal["state"] == "sent"
        and journal["append_started"]
        and not journal.get("copy_done")
    )
    server.append_error = None
    sending.recover_sent(config, store, event_id="retry")
    assert store.get("send", draft.identity)["copy_done"]
    assert (
        len(calls) == 1
        and len([call for call in server.calls if call[0] == "append"]) == 1
    )


def test_uncertain_absent_sent_copy_never_appends_or_sends_again(account, monkeypatch):
    config, store, server = account
    config = replace(config, sent_copy=SentCopyMode.APPEND)
    draft, path = draft_in(store)
    server.append_error = EmailError(
        "Disconnected during append.", reason="imap_unavailable", retryable=True
    )
    calls = fake_delivery(monkeypatch, store)
    with pytest.raises(EmailError):
        submit(config, store, path)
    server.append_error = None
    sending.recover_sent(config, store, event_id="retry")
    assert not store.get("send", draft.identity).get("copy_done")
    assert (
        len(calls) == 1
        and len([call for call in server.calls if call[0] == "append"]) == 1
    )


def test_failed_local_finalization_recovers_without_smtp_resend(account, monkeypatch):
    config, store, _ = account
    draft, path = draft_in(store)
    calls = fake_delivery(monkeypatch, store)
    display = store.display

    def failed_display(*args, **kwargs):
        raise EmailError("Local write failed.", reason="write_failed")

    monkeypatch.setattr(store, "display", failed_display)
    with pytest.raises(EmailError):
        submit(config, store, path)
    assert store.get("send", draft.identity)["state"] == "sent"
    assert parse_draft(Path(path).read_text()).identity == draft.identity
    monkeypatch.setattr(store, "display", display)
    sending.recover_sent(config, store, event_id="retry")
    assert len(calls) == 1
    assert store.get("send", draft.identity)["local_done"]
    assert parse_draft(Path(path).read_text()).identity != draft.identity


def test_local_io_failure_after_acceptance_retains_durable_sent_state(
    account, monkeypatch
):
    config, store, _ = account
    draft, path = draft_in(store)
    calls = fake_delivery(monkeypatch, store)
    write_text = store.write_text

    def failed_reset(relative, text, **kwargs):
        if relative == "new email.md":
            raise OSError("disk full")
        return write_text(relative, text, **kwargs)

    monkeypatch.setattr(store, "write_text", failed_reset)
    with pytest.raises((OSError, EmailError)):
        submit(config, store, path)
    assert store.get("send", draft.identity)["state"] == "sent"
    assert parse_draft(Path(path).read_text()).identity == draft.identity
    monkeypatch.setattr(store, "write_text", write_text)
    sending.recover_sent(config, store, event_id="retry")
    assert len(calls) == 1
    assert store.get("send", draft.identity)["local_done"]
    assert parse_draft(Path(path).read_text()).identity != draft.identity


def test_existing_server_copy_with_same_id_but_other_content_does_not_append(
    account, monkeypatch
):
    config, store, server = account
    config = replace(config, sent_copy=SentCopyMode.APPEND)
    draft, path = draft_in(store)
    server.messages[81] = (
        f"Message-ID: <lucy-{draft.identity}@example.test>\r\n\r\nOther message".encode()
    )
    calls = fake_delivery(monkeypatch, store)
    with pytest.raises(EmailError) as raised:
        submit(config, store, path)
    assert raised.value.reason == "sent_copy_uncertain"
    assert store.get("send", draft.identity)["state"] == "sent"
    assert len(calls) == 1
    assert not any(call[0] == "append" for call in server.calls)


def test_fetch_recovery_keeps_untouched_prepared_generation_unsent(
    account, monkeypatch
):
    config, store, _ = account
    draft, path = draft_in(store)

    def interrupted_before_data(settings, sender, recipients, payload, *, before_data):
        raise KeyboardInterrupt

    monkeypatch.setattr(sending.smtp, "send", interrupted_before_data)
    with pytest.raises(KeyboardInterrupt):
        submit(config, store, path)
    assert store.get("send", draft.identity)["state"] == "prepared"
    monkeypatch.setattr(
        sending.smtp,
        "send",
        lambda *args, **kwargs: pytest.fail("fetch sent a prepared draft"),
    )
    sending.recover_sent(config, store, event_id="restart")
    assert store.get("send", draft.identity)["state"] == "prepared"
    assert parse_draft(Path(path).read_text()).identity == draft.identity


def test_recover_sent_after_reopening_sqlite_does_not_send(account, monkeypatch):
    config, store, server = account
    config = replace(config, sent_copy=SentCopyMode.APPEND)
    draft, path = draft_in(store)
    server.append_error = EmailError(
        "Disconnected after append.", reason="imap_unavailable", retryable=True
    )
    server.save_before_error = True
    fake_delivery(monkeypatch, store)
    with pytest.raises(EmailError):
        submit(config, store, path)
    monkeypatch.setattr(
        sending.smtp, "send", lambda *args, **kwargs: pytest.fail("recovery sent SMTP")
    )
    server.append_error = None
    with MailStore(config.root) as reopened:
        sending.recover_sent(config, reopened, event_id="restart")
        assert reopened.get("send", draft.identity)["copy_done"]
        assert len(reopened.records()) == 1


@pytest.mark.parametrize("edited_after_acceptance", [False, True])
def test_lower_size_limit_preserves_accepted_delivery_recovery_bounds(
    account, monkeypatch, edited_after_acceptance
):
    config, store, server = account
    config = replace(config, sent_copy=SentCopyMode.APPEND)
    draft, path = draft_in(store)
    draft = replace(draft, body="x" * 70000)
    Path(path).write_text(render_draft(draft))
    calls = fake_delivery(monkeypatch, store)
    write_text = store.write_text

    def fail_reset(relative, text, **kwargs):
        if relative == "new email.md":
            raise OSError("interrupted after delivery")
        return write_text(relative, text, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store, "write_text", fail_reset)
        with pytest.raises(OSError, match="after delivery"):
            submit(config, store, path)
    journal = store.get("send", draft.identity)
    assert journal["state"] == "sent"
    assert journal["max_message_bytes"] == config.max_message_bytes
    assert store.records()[0].raw_bytes == len(calls[0][2])
    server.messages[81] = calls[0][2]
    if edited_after_acceptance:
        Path(path).write_text(
            render_draft(replace(draft, body=draft.body + "\nLater edit"))
        )

    def bounded_fetch(uid, max_bytes):
        assert max_bytes >= len(server.messages[uid])
        server.calls.append(("fetch", uid, max_bytes))
        return server.messages[uid]

    monkeypatch.setattr(server, "fetch", bounded_fetch)
    with MailStore(config.root) as reopened:
        sending.recover_sent(
            replace(config, max_message_bytes=1000), reopened, event_id="lower-limit"
        )
        recovered = reopened.get("send", draft.identity)
        assert recovered["copy_done"] and recovered["local_done"]
        current = parse_draft(Path(path).read_text())
        assert current.identity != draft.identity
        assert current.body == (
            draft.body + "\nLater edit" if edited_after_acceptance else ""
        )
    assert len(calls) == 1
    assert any(call[0] == "fetch" for call in server.calls)
    assert not any(call[0] == "append" for call in server.calls)


def test_recovery_reuses_sent_copy_already_imported_after_local_failure(
    account, monkeypatch
):
    config, store, _ = account
    draft, path = draft_in(store)
    calls = fake_delivery(monkeypatch, store)
    display = store.display

    def fail(*args, **kwargs):
        raise EmailError("Local write failed.", reason="write_failed")

    monkeypatch.setattr(store, "display", fail)
    with pytest.raises(EmailError):
        submit(config, store, path)
    monkeypatch.setattr(store, "display", display)
    journal = store.get("send", draft.identity)
    imported = MessageRecord(
        uuid.uuid4().hex,
        "Sent",
        "Sent",
        7,
        81,
        "",
        journal["payload_path"],
        True,
        fingerprint(calls[0][2]),
        raw_bytes=len(calls[0][2]),
    )
    store.display(imported, decode_message(calls[0][2]))
    sending.recover_sent(config, store, event_id="restart")
    assert len(calls) == 1 and len(store.records()) == 1
    assert store.records()[0].identity == imported.identity
    assert store.get("send", draft.identity)["sent_identity"] == imported.identity


def test_recovery_rejects_modified_accepted_payload(account, monkeypatch):
    config, store, _ = account
    draft, path = draft_in(store)
    calls = fake_delivery(monkeypatch, store)
    display = store.display

    def fail(*args, **kwargs):
        raise EmailError("Local write failed.", reason="write_failed")

    monkeypatch.setattr(store, "display", fail)
    with pytest.raises(EmailError):
        submit(config, store, path)
    monkeypatch.setattr(store, "display", display)
    journal = store.get("send", draft.identity)
    payload_path = Path(store.path(journal["payload_path"]))
    payload_path.write_bytes(
        payload_path.read_bytes().replace(b"Hello Bob", b"Other txt")
    )
    sending.recover_sent(config, store, event_id="restart")
    assert len(calls) == 1 and store.records() == []
    assert store.get("send", draft.identity)["state"] == "sent"
    assert not store.get("send", draft.identity).get("local_done")


def test_unregistered_copied_draft_cannot_send(account, monkeypatch):
    config, store, _ = account
    draft, path = draft_in(store)
    copy = Path(config.root, "copied.md")
    copy.write_text(Path(path).read_text())
    monkeypatch.setattr(
        sending.smtp,
        "send",
        lambda *args, **kwargs: pytest.fail("unmanaged draft sent"),
    )
    with pytest.raises(EmailError) as raised:
        submit(config, store, str(copy))
    assert raised.value.reason == "unmanaged_draft"
    assert store.get("send", draft.identity) is None


@pytest.mark.parametrize("entrypoint", ["recover", "send"])
def test_pending_delivery_cannot_finalize_into_a_different_account(
    account, monkeypatch, entrypoint
):
    config, store, server = account
    config = replace(config, sent_copy=SentCopyMode.APPEND)
    draft, path = draft_in(store)
    server.append_error = EmailError(
        "Disconnected.", reason="imap_unavailable", retryable=True
    )
    calls = fake_delivery(
        monkeypatch,
        store,
        result=DeliveryResult(("bob@example.test",), ("refused@example.test",)),
    )
    with pytest.raises(EmailError):
        submit(config, store, path)
    assert store.get("send", draft.identity)["state"] == "partial"
    changed = replace(config, imap=replace(config.imap, username="another-user"))
    server.calls.clear()
    with pytest.raises(EmailError) as raised:
        if entrypoint == "recover":
            sending.recover_sent(changed, store, event_id="recovery")
        else:
            submit(changed, store, path)
    assert raised.value.reason == "account_changed"
    assert server.calls == []
    assert len(calls) == 1
