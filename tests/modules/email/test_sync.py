from dataclasses import replace
from pathlib import Path
import uuid

import pytest

from demon_lucy.modules.email import sync
from demon_lucy.modules.email.codec import reply_draft
from demon_lucy.modules.email.errors import EmailError
from demon_lucy.modules.email.models import (
    AccountConfig,
    CredentialProvider,
    MailboxInfo,
    MessageInfo,
    MoveResult,
    Security,
    SentCopyMode,
    ServerSettings,
)
from demon_lucy.modules.email.storage import MailStore


def message(uid, *, body="hello"):
    return f"From: Alice <alice@example.test>\r\nTo: bob@example.test\r\nSubject: Message {uid}\r\nMessage-ID: <message-{uid}@example.test>\r\n\r\n{body}\r\n".encode()


class MailServer:
    def __init__(self):
        self.folders = {"INBOX": {}, "Sent": {}, "Archive": {}, "Trash": {}}
        self.validities = {name: 7 for name in self.folders}
        self.calls = []
        self.missing_metadata = set()
        self.failed_fetch = set()
        self.move_error = None
        self.move_mapping = MoveResult(7, 77)

    def add(self, uid, *, folder="INBOX", raw=None, read=False):
        self.folders[folder][uid] = (
            raw if raw is not None else message(uid),
            frozenset({r"\Seen"} if read else ()),
        )

    def session(self, settings):
        return FakeImap(self)


class FakeImap:
    def __init__(self, server):
        self.server = server
        self.selected = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def mailboxes(self):
        return [
            MailboxInfo(
                name, frozenset({"\\" + ("Inbox" if name == "INBOX" else name)})
            )
            for name in self.server.folders
        ]

    def select(self, mailbox, readonly=False):
        self.selected = mailbox
        self.server.calls.append(("select", mailbox, readonly))
        return self.server.validities[mailbox]

    def uids(self):
        return list(self.server.folders[self.selected])

    def metadata(self, uids):
        self.server.calls.append(("metadata", self.selected, tuple(uids)))
        return {
            uid: MessageInfo(uid, flags, len(raw))
            for uid, (raw, flags) in self.server.folders[self.selected].items()
            if uid in uids and (self.selected, uid) not in self.server.missing_metadata
        }

    def fetch(self, uid, max_bytes):
        self.server.calls.append(("fetch", self.selected, uid))
        if (self.selected, uid) in self.server.failed_fetch:
            raise EmailError(
                "Disconnected during fetch.", reason="imap_unavailable", retryable=True
            )
        raw = self.server.folders[self.selected][uid][0]
        assert len(raw) <= max_bytes
        return raw

    def headers(self, uid):
        self.server.calls.append(("headers", self.selected, uid))
        return (
            self.server.folders[self.selected][uid][0].split(b"\r\n\r\n", 1)[0]
            + b"\r\n\r\n"
        )

    def set_seen(self, uid, seen):
        self.server.calls.append(("seen", self.selected, uid, seen))
        raw, flags = self.server.folders[self.selected][uid]
        flags = flags | {r"\Seen"} if seen else flags - {r"\Seen"}
        self.server.folders[self.selected][uid] = raw, frozenset(flags)

    def move(self, uid, destination, checkpoint=None):
        self.server.calls.append(("move", self.selected, uid, destination))
        checkpoint("before_move", {"source_uid": uid, "destination": destination})
        if self.server.move_error:
            raise self.server.move_error
        raw, flags = self.server.folders[self.selected].pop(uid)
        self.server.folders[destination][self.server.move_mapping.uid or 77] = (
            raw,
            flags,
        )
        return self.server.move_mapping


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
        {"Inbox": "INBOX", "Sent": "Sent", "Archive": "Archive", "Trash": "Trash"},
        100,
        1024 * 1024,
        SentCopyMode.SERVER,
    )
    server = MailServer()
    monkeypatch.setattr(sync, "ImapSession", server.session)
    with MailStore(str(root)) as store:
        yield config, store, server


def fetch(config, store):
    sync.fetch_mail(config, store, event_id="email-test")


def test_initial_fetch_latest_100_then_only_new_messages(account):
    config, store, server = account
    for uid in range(1, 151):
        server.add(uid)
    fetch(config, store)
    assert sorted(record.uid for record in store.records()) == list(range(51, 151))
    assert store.get("mailbox", "INBOX") == {"uidvalidity": 7, "last_uid": 150}
    original = {
        record.uid: (record.identity, record.path) for record in store.records()
    }
    server.calls.clear()
    fetch(config, store)
    assert not any(call[0] == "fetch" for call in server.calls)
    server.add(151)
    fetch(config, store)
    assert len(store.records()) == 101
    assert {
        record.uid: (record.identity, record.path)
        for record in store.records()
        if record.uid < 151
    } == original
    assert [call for call in server.calls if call[0] == "fetch"] == [
        ("fetch", "INBOX", 151)
    ]


@pytest.mark.parametrize("limit, expected", [(2, [4, 5]), (0, [1, 2, 3, 4, 5])])
def test_configured_initial_limit(account, limit, expected):
    config, store, server = account
    for uid in range(1, 6):
        server.add(uid)
    fetch(replace(config, initial_limit=limit), store)
    assert sorted(record.uid for record in store.records()) == expected


@pytest.mark.parametrize("failure", ["metadata", "body"])
def test_failed_message_does_not_advance_checkpoint_past_it(account, failure):
    config, store, server = account
    server.add(100)
    fetch(config, store)
    for uid in (101, 102, 103):
        server.add(uid)
    failures = server.missing_metadata if failure == "metadata" else server.failed_fetch
    failures.add(("INBOX", 102))
    with pytest.raises(EmailError):
        fetch(config, store)
    assert store.get("mailbox", "INBOX")["last_uid"] == 101
    assert sorted(record.uid for record in store.records()) == [100, 101]
    failures.clear()
    fetch(config, store)
    assert store.get("mailbox", "INBOX")["last_uid"] == 103
    assert sorted(record.uid for record in store.records()) == [100, 101, 102, 103]


def test_uidvalidity_reset_invalidates_old_binding_and_preserves_old_content(account):
    config, store, server = account
    server.add(1, raw=message(1, body="old content"))
    fetch(config, store)
    original = store.records()[0]
    server.validities["INBOX"] = 8
    server.add(1, raw=message(1, body="new content"))
    fetch(config, store)
    records = store.records()
    local = next(record for record in records if record.identity == original.identity)
    current = next(record for record in records if record.uid)
    assert local.folder == ".email/recovery" and local.uid == 0 and local.mailbox == ""
    assert "old content" in Path(store.path(local.path)).read_text()
    assert current.uidvalidity == 8 and current.uid == 1
    assert "new content" in Path(store.path(current.path)).read_text()


def test_external_move_retains_identity_and_rebinds_uid(account):
    config, store, server = account
    server.add(1)
    fetch(config, store)
    original = store.records()[0]
    server.folders["Archive"][77] = server.folders["INBOX"].pop(1)
    fetch(config, store)
    assert len(store.records()) == 1
    moved = store.records()[0]
    assert (moved.identity, moved.folder, moved.mailbox, moved.uid) == (
        original.identity,
        "Archive",
        "Archive",
        77,
    )
    assert Path(store.path(moved.path)).exists()
    assert not Path(store.path(original.path)).exists()


def test_remote_delete_preserves_local_edits(account):
    config, store, server = account
    server.add(1)
    fetch(config, store)
    record = store.records()[0]
    path = Path(store.path(record.path))
    path.write_text(path.read_text() + "\nMy private annotation\n")
    del server.folders["INBOX"][1]
    fetch(config, store)
    record = store.records()[0]
    assert record.folder == ".email/recovery" and not record.uid
    assert "My private annotation" in Path(store.path(record.path)).read_text()


def test_flag_update_preserves_edited_copy_and_does_not_download_again(account):
    config, store, server = account
    server.add(1)
    fetch(config, store)
    record = store.records()[0]
    old_path = Path(store.path(record.path))
    old_path.write_text(old_path.read_text() + "\nMy annotation\n")
    raw, _ = server.folders["INBOX"][1]
    server.folders["INBOX"][1] = raw, frozenset({r"\Seen", r"\Flagged"})
    server.calls.clear()
    fetch(config, store)
    current = store.records()[0]
    assert current.identity == record.identity and current.read
    assert "**Status:** read" in Path(store.path(current.path)).read_text()
    assert not any(call[0] == "fetch" for call in server.calls)
    preserved = list(Path(config.root, ".email/recovery").glob("*.md"))
    assert len(preserved) == 1 and "My annotation" in preserved[0].read_text()
    assert "My annotation" not in Path(store.path(current.path)).read_text()


def test_missing_local_file_is_recreated_from_private_original(account):
    config, store, server = account
    server.add(1)
    fetch(config, store)
    record = store.records()[0]
    path = Path(store.path(record.path))
    path.unlink()
    server.calls.clear()
    fetch(config, store)
    assert path.is_file()
    assert store.records()[0].identity == record.identity
    assert not any(call[0] == "fetch" for call in server.calls)


def test_oversized_placeholder_upgrades_after_size_limit_increase(account):
    config, store, server = account
    server.add(1, raw=message(1, body="large content " * 100))
    fetch(replace(config, max_message_bytes=200), store)
    record = store.records()[0]
    assert record.oversized
    assert any(call[0] == "headers" for call in server.calls)
    assert not any(call[0] == "fetch" for call in server.calls)
    assert (
        "exceeds the configured size limit" in Path(store.path(record.path)).read_text()
    )
    fetch(config, store)
    upgraded = store.records()[0]
    assert upgraded.identity == record.identity and not upgraded.oversized
    assert (
        store.raw(upgraded, config.max_message_bytes) == server.folders["INBOX"][1][0]
    )
    assert "large content" in Path(store.path(upgraded.path)).read_text()


def test_lower_download_limit_keeps_cached_message_readable_for_fetch_reply_and_flags(
    account,
):
    config, store, server = account
    raw = message(1, body="x" * 70000)
    server.add(1, raw=raw)
    fetch(config, store)
    original = store.records()[0]
    assert original.raw_bytes == len(raw)
    lowered = replace(config, max_message_bytes=1000)
    server.folders["INBOX"][1] = (raw, frozenset({r"\Seen"}))
    server.calls.clear()
    with MailStore(config.root) as restarted:
        fetch(lowered, restarted)
        cached = restarted.records()[0]
        assert cached.identity == original.identity
        assert cached.read and not cached.oversized
        assert cached.raw_bytes == len(raw)
        reply = reply_draft(uuid.uuid4().hex, sync.decoded_record(restarted, cached))
        assert "x" * 70000 in reply.body
        sync.change_message(
            lowered,
            restarted,
            restarted.path(cached.path),
            "email-mark-unread",
            event_id="lower-limit",
        )
        assert not restarted.records()[0].read
        assert restarted.raw(restarted.records()[0], len(raw)) == raw
    assert not any(call[0] in {"fetch", "headers"} for call in server.calls)


def test_cached_message_size_binding_rejects_grown_payload(account):
    config, store, server = account
    server.add(1, raw=message(1, body="x" * 70000))
    fetch(config, store)
    cached = store.records()[0]
    path = Path(store.path(cached.raw_path))
    path.write_bytes(path.read_bytes() + b"extra bytes")
    with pytest.raises(ValueError, match="size limit"):
        sync.decoded_record(store, cached)


def test_mark_read_preserves_other_remote_flags(account):
    config, store, server = account
    server.add(1)
    raw, _ = server.folders["INBOX"][1]
    server.folders["INBOX"][1] = raw, frozenset({r"\Flagged"})
    fetch(config, store)
    original = store.records()[0]
    result = sync.change_message(
        config, store, store.path(original.path), "email-mark-read", event_id="test"
    )
    assert server.folders["INBOX"][1][1] == frozenset({r"\Flagged", r"\Seen"})
    assert result == store.path(store.records()[0].path)
    assert store.records()[0].read


def test_move_updates_binding_and_final_path(account):
    config, store, server = account
    server.add(1)
    fetch(config, store)
    original = store.records()[0]
    result = sync.change_message(
        config, store, store.path(original.path), "email-archive", event_id="test"
    )
    moved = store.records()[0]
    assert (moved.identity, moved.folder, moved.uid, moved.uidvalidity) == (
        original.identity,
        "Archive",
        77,
        7,
    )
    assert result == store.path(moved.path) and Path(result).is_file()
    assert store.get("move", original.identity)["state"] == "complete"
    assert not Path(store.path(original.path)).exists()


def test_move_without_mapping_rebinds_on_refresh(account):
    config, store, server = account
    server.add(1)
    fetch(config, store)
    original = store.records()[0]
    server.move_mapping = MoveResult(None, None)
    sync.change_message(
        config, store, store.path(original.path), "email-trash", event_id="test"
    )
    assert store.records()[0].uid == 0
    fetch(config, store)
    current = store.records()[0]
    assert len(store.records()) == 1
    assert (
        current.identity == original.identity
        and current.uid == 77
        and current.folder == "Trash"
    )


def test_uncertain_move_is_journaled_and_replay_blocked(account):
    config, store, server = account
    server.add(1)
    fetch(config, store)
    original = store.records()[0]
    server.move_error = EmailError(
        "Move connection failed.", reason="imap_unavailable", retryable=True
    )
    with pytest.raises(EmailError):
        sync.change_message(
            config, store, store.path(original.path), "email-trash", event_id="test"
        )
    pending = store.get("move", original.identity)
    assert pending["state"] == "pending" and pending["phase"] == "before_move"
    server.move_error = None
    with pytest.raises(EmailError) as raised:
        sync.change_message(
            config, store, store.path(original.path), "email-trash", event_id="test"
        )
    assert raised.value.reason == "move_uncertain"
    assert len([call for call in server.calls if call[0] == "move"]) == 1
    # A fetch observes the completed remote move and resolves the journal.
    server.folders["Trash"][77] = server.folders["INBOX"].pop(1)
    fetch(config, store)
    assert store.get("move", original.identity)["state"] == "complete"
    assert store.records()[0].identity == original.identity


def _interrupt_after_copy(account, monkeypatch):
    config, store, server = account
    server.add(1)
    fetch(config, store)
    original = store.records()[0]

    def copy_then_fail(session, uid, destination, checkpoint=None):
        server.calls.append(("copy", session.selected, uid, destination))
        server.folders[destination][77] = server.folders[session.selected][uid]
        checkpoint(
            "before_delete",
            {
                "source_uid": uid,
                "destination": destination,
                "uidvalidity": 7,
                "uid": 77,
            },
        )
        raise EmailError(
            "Disconnected after COPY.", reason="imap_unavailable", retryable=True
        )

    monkeypatch.setattr(FakeImap, "move", copy_then_fail)
    with pytest.raises(EmailError, match="after COPY"):
        sync.change_message(
            config, store, store.path(original.path), "email-archive", event_id="test"
        )
    return original


@pytest.mark.parametrize("import_copy_before_completion", [False, True])
def test_acknowledged_copy_reconciles_after_manual_source_removal(
    account, monkeypatch, import_copy_before_completion
):
    config, store, server = account
    original = _interrupt_after_copy(account, monkeypatch)
    if import_copy_before_completion:
        fetch(config, store)
        assert len(store.records()) == 2
        assert store.get("move", original.identity)["state"] == "pending"
    del server.folders["INBOX"][1]
    with MailStore(config.root) as restarted:
        fetch(config, restarted)
        records = restarted.records()
        assert len(records) == 1
        assert (
            records[0].identity,
            records[0].mailbox,
            records[0].uidvalidity,
            records[0].uid,
        ) == (original.identity, "Archive", 7, 77)
        assert restarted.get("move", original.identity)["state"] == "complete"
        fetch(config, restarted)
        assert restarted.records() == records
    assert len([call for call in server.calls if call[0] == "copy"]) == 1
    assert not any(
        call[0] in {"move", "seen", "delete", "expunge"} for call in server.calls
    )


def test_copy_reconciliation_preserves_annotations_on_both_local_files(
    account, monkeypatch
):
    config, store, server = account
    original = _interrupt_after_copy(account, monkeypatch)
    original_path = Path(store.path(original.path))
    original_text = original_path.read_text() + "\nSource annotation\n"
    original_path.write_text(original_text)
    fetch(config, store)
    copy = next(
        record for record in store.records() if record.identity != original.identity
    )
    copied_path = Path(store.path(copy.path))
    copied_text = copied_path.read_text() + "\nCopied annotation\n"
    copied_path.write_text(copied_text)
    del server.folders["INBOX"][1]
    fetch(config, store)
    assert len(store.records()) == 1
    assert store.records()[0].identity == original.identity
    assert not copied_path.exists()
    preserved = [
        path.read_text() for path in Path(config.root, ".email/recovery").glob("*.md")
    ]
    assert sorted(preserved) == sorted([original_text, copied_text])
    fetch(config, store)
    assert len(list(Path(config.root, ".email/recovery").glob("*.md"))) == 2


def test_copy_reconciliation_recovers_restart_between_rebinding_and_retirement(
    account, monkeypatch
):
    config, store, server = account
    original = _interrupt_after_copy(account, monkeypatch)
    fetch(config, store)
    del server.folders["INBOX"][1]

    def interrupted(record):
        raise OSError("interrupted before retiring duplicate")

    with monkeypatch.context() as patch:
        patch.setattr(store, "retire_duplicate", interrupted)
        with pytest.raises(OSError, match="interrupted"):
            fetch(config, store)
    assert store.get("move", original.identity)["state"] == "pending"
    assert len(store.records()) == 2
    with MailStore(config.root) as restarted:
        fetch(config, restarted)
        assert len(restarted.records()) == 1
        assert restarted.records()[0].identity == original.identity
        assert restarted.get("move", original.identity)["state"] == "complete"
        fetch(config, restarted)
        assert len(restarted.records()) == 1


def test_copy_reconciliation_does_not_trust_reused_destination_uid(
    account, monkeypatch
):
    config, store, server = account
    original = _interrupt_after_copy(account, monkeypatch)
    fetch(config, store)
    del server.folders["INBOX"][1]
    server.validities["Archive"] = 8
    server.folders["Archive"][77] = (
        message(88, body="Different message after reset"),
        frozenset(),
    )
    fetch(config, store)
    assert store.get("move", original.identity)["state"] == "pending"
    original_after = next(
        record for record in store.records() if record.identity == original.identity
    )
    assert original_after.uid == 0 and original_after.folder == ".email/recovery"


def test_move_guard_checks_uidvalidity_before_remote_mutation(account):
    config, store, server = account
    server.add(1)
    fetch(config, store)
    original = store.records()[0]
    server.validities["INBOX"] = 8
    with pytest.raises(EmailError) as raised:
        sync.change_message(
            config, store, store.path(original.path), "email-trash", event_id="test"
        )
    assert raised.value.reason == "mailbox_changed"
    assert not any(call[0] == "move" for call in server.calls)


def test_account_identity_cannot_rebind_existing_store(account):
    config, store, server = account
    fetch(config, store)
    changed = replace(config, imap=replace(config.imap, username="another-user"))
    with pytest.raises(EmailError) as raised:
        fetch(changed, store)
    assert raised.value.reason == "account_changed"


def test_refresh_recovers_write_failure_after_status_filename_move(
    account, monkeypatch
):
    config, store, server = account
    server.add(1)
    fetch(config, store)
    original = store.records()[0]
    raw, _ = server.folders["INBOX"][1]
    server.folders["INBOX"][1] = raw, frozenset({r"\Seen"})
    write_text = store.write_text

    def disk_full(relative, text, **kwargs):
        raise OSError("disk full after moving status filename")

    monkeypatch.setattr(store, "write_text", disk_full)
    with pytest.raises((OSError, EmailError)):
        fetch(config, store)
    monkeypatch.setattr(store, "write_text", write_text)
    fetch(config, store)
    current = store.records()[0]
    assert current.identity == original.identity and current.read
    assert Path(store.path(current.path)).is_file()
    assert "**Status:** read" in Path(store.path(current.path)).read_text()
