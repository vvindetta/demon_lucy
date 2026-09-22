from __future__ import annotations

import os
import re
import stat
from dataclasses import replace
from email.message import EmailMessage
from pathlib import Path

import pytest

from demon_lucy.lib.args.parser import parse_args
from demon_lucy.modules.email.documents import LITERAL_MARKER
from demon_lucy.lib.text_file import SourceChangedError
from demon_lucy.modules.email.codec import (
    decode_message,
    message_filename,
    new_draft,
    render_draft,
)
from demon_lucy.modules.email.config import SETTINGS_TEMPLATE, account_from_args
from demon_lucy.modules.email.errors import EmailError
from demon_lucy.modules.email import storage
from demon_lucy.modules.email.storage import MailStore, MessageRecord, fingerprint

IDENTITY = "9ef8ec8c7f1a41cf974c65fd6f17d7f5"


def _config(root: Path):
    return account_from_args(
        str(root),
        parse_args(
            [
                "--email-imap-host",
                "mail.example.test",
                "--email-imap-username",
                "lucy@example.test",
            ],
            SETTINGS_TEMPLATE,
        ),
    )


def _message_with_attachment() -> bytes:
    message = EmailMessage()
    message["From"] = "sender@example.test"
    message["To"] = "lucy@example.test"
    message["Subject"] = "Saved email"
    message.set_content("Body\n--cmd untrusted\n")
    message.add_attachment(
        bytes(range(256)),
        maintype="application",
        subtype="octet-stream",
        filename="../../hostile [name].bin",
    )
    return message.as_bytes()


def _display(store: MailStore) -> MessageRecord:
    raw = _message_with_attachment()
    record = MessageRecord(
        IDENTITY,
        "Inbox",
        "INBOX",
        17,
        31,
        "",
        f".email/raw/.{IDENTITY}.eml",
        False,
        fingerprint(raw),
        raw_bytes=len(raw),
    )
    store.write_blob(record.raw_path, raw)
    store.display(record, decode_message(raw))
    return record


def test_account_store_creates_root_and_persists_state_and_private_payloads(
    tmp_path: Path,
) -> None:
    root = tmp_path / "missing" / "account"
    with MailStore(str(root)) as store:
        record = _display(store)
        assert store.record_for_path(store.path(record.path)) == record
        assert fingerprint(store.raw(record, 100_000)) == record.digest
        store.put("checkpoint", "inbox", {"uid": 31})
        assert store.path(record.path) in store.changed
        blobs = list((root / ".email" / "attachments").iterdir())
        assert len(blobs) == 1
        assert blobs[0].read_bytes() == bytes(range(256))
        assert blobs[0].name.startswith(".")
        assert Path(record.raw_path).name.startswith(".")
        assert "../../hostile" not in str(blobs[0])
        if os.name == "posix":
            assert (
                stat.S_IMODE((root / ".email" / ".state.sqlite3").stat().st_mode)
                == 0o600
            )
            assert stat.S_IMODE(blobs[0].stat().st_mode) == 0o600
    with MailStore(str(root)) as reopened:
        assert reopened.get("checkpoint", "inbox") == {"uid": 31}
        assert reopened.records()[0].identity == IDENTITY


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks")
@pytest.mark.parametrize(
    "position",
    ["root", "root_parent", "private_directory", "database", "database_journal"],
)
def test_store_rejects_symlinked_roots_private_files_and_ancestors(
    tmp_path: Path, position: str
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "account"
    if position == "root":
        root.symlink_to(outside, target_is_directory=True)
    elif position == "root_parent":
        link = tmp_path / "parent"
        link.symlink_to(outside, target_is_directory=True)
        root = link / "account"
    else:
        root.mkdir()
        if position == "private_directory":
            (root / ".email").symlink_to(outside, target_is_directory=True)
        else:
            (root / ".email").mkdir()
            name = ".state.sqlite3" + (
                "-journal" if position == "database_journal" else ""
            )
            (root / ".email" / name).symlink_to(outside / "sensitive")
    with pytest.raises((OSError, ValueError)):
        with MailStore(str(root)):
            pytest.fail("unsafe root opened")
    assert list(outside.iterdir()) == []


def test_storage_paths_cannot_escape_account(tmp_path: Path) -> None:
    root = tmp_path / "account"
    with MailStore(str(root)) as store:
        for relative in ("../outside", "/etc/passwd", "sub/../../outside"):
            with pytest.raises(ValueError):
                store.write_text(relative, "must not write")
        with pytest.raises(ValueError):
            store.relative(str(tmp_path / "outside"))
    assert not (tmp_path / "outside").exists()


def test_write_text_refuses_existing_content_and_intervening_edits(
    tmp_path: Path,
) -> None:
    with MailStore(str(tmp_path)) as store:
        store.write_text("draft.md", "original content")
        with pytest.raises((SourceChangedError, ValueError)):
            store.write_text("draft.md", "shorter replacement")
        assert (tmp_path / "draft.md").read_text() == "original content"
        (tmp_path / "draft.md").write_text("user edit")
        with pytest.raises(SourceChangedError):
            store.write_text("draft.md", "replacement", expected="original content")
        assert (tmp_path / "draft.md").read_text() == "user edit"


def test_blobs_are_immutable_and_identical_rewrites_are_idempotent(
    tmp_path: Path,
) -> None:
    with MailStore(str(tmp_path)) as store:
        relative = ".email/raw/.raw.eml"
        content = b"\x00raw\xff\r\n"
        store.write_blob(relative, content)
        store.write_blob(relative, content)
        with pytest.raises((EmailError, ValueError)):
            store.write_blob(relative, b"changed")
        assert Path(store.path(relative)).read_bytes() == content


def test_display_preserves_local_edits_before_read_status_refresh(
    tmp_path: Path,
) -> None:
    with MailStore(str(tmp_path)) as store:
        record = _display(store)
        original_path = Path(store.path(record.path))
        local_text = original_path.read_text() + "\nMy local annotation\n"
        original_path.write_text(local_text)
        record.read = True
        store.display(record, decode_message(store.raw(record, 100_000)))
        preserved = list((tmp_path / ".email/recovery").iterdir())
        assert len(preserved) == 1
        assert preserved[0].read_text() == local_text
        current = Path(store.path(record.path))
        assert current.exists()
        assert "**Status:** read" in current.read_text()
        assert "My local annotation" not in current.read_text()
        assert store.record_for_path(str(current)).uid == 31
        with pytest.raises(EmailError, match="managed message"):
            store.record_for_path(str(preserved[0]))


def test_long_edited_message_survives_remote_move_and_rebinding(tmp_path: Path) -> None:
    message = EmailMessage()
    message["From"] = "A sender with a long displayed name <sender@example.test>"
    message["Date"] = "Tue, 22 Sep 2026 14:30:00 +0200"
    message["Subject"] = "🦊" * 100
    message.set_content("Body")
    raw = message.as_bytes()
    with MailStore(str(tmp_path)) as store:
        record = MessageRecord(
            IDENTITY,
            "Inbox",
            "INBOX",
            17,
            31,
            "",
            f".email/raw/.{IDENTITY}.eml",
            False,
            fingerprint(raw),
            raw_bytes=len(raw),
        )
        store.write_blob(record.raw_path, raw)
        decoded = decode_message(raw)
        store.display(record, decoded)
        original = Path(store.path(record.path))
        edited = original.read_text() + "\nLocal annotation\n"
        original.write_text(edited)
        store.preserve_local(record)
        record.folder, record.mailbox, record.uid = "Archive", "Archive", 32
        store.display(record, decoded)
        assert record.path.startswith("Archive/")
        assert "Local annotation" not in Path(store.path(record.path)).read_text()
        preserved = list((tmp_path / ".email/recovery").iterdir())
        assert len(preserved) == 1
        assert preserved[0].read_text() == edited
        assert len(preserved[0].name.encode("utf-8")) <= 255
        assert not re.match(r"[0-9a-f]{32}-[0-9a-f]{32}-", preserved[0].name)


def test_preserved_names_truncate_utf8_and_keep_one_prefix(tmp_path: Path) -> None:
    with MailStore(str(tmp_path)) as store:
        record = _display(store)
        original = Path(store.path(record.path))
        long_relative = "Inbox/" + "🦊" * 62 + ".md"
        original.rename(store.path(long_relative))
        record.path = long_relative
        for _ in range(3):
            store.preserve_local(record)
            path = Path(store.path(record.path))
            assert path.exists()
            assert len(path.name.encode("utf-8")) <= 255
            assert path.suffix == ".md"
            assert not re.match(r"[0-9a-f]{32}-[0-9a-f]{32}-", path.name)


def test_attachment_links_normalize_windows_separators(
    tmp_path: Path, monkeypatch
) -> None:
    relpath = os.path.relpath
    monkeypatch.setattr(
        storage.os.path,
        "relpath",
        lambda path, start: relpath(path, start).replace("/", "\\"),
    )
    with MailStore(str(tmp_path)) as store:
        record = _display(store)
        text = Path(store.path(record.path)).read_text()
        assert f"](../.email/attachments/.{IDENTITY}-0.blob)" in text


def test_display_unchanged_message_is_idempotent_and_moves_existing_file(
    tmp_path: Path,
) -> None:
    with MailStore(str(tmp_path)) as store:
        record = _display(store)
        initial = dict(store.changed)
        message = decode_message(store.raw(record, 100_000))
        store.display(record, message)
        assert store.changed == initial
        original = Path(store.path(record.path))
        record.folder = "Archive"
        store.display(record, message)
        assert record.path.startswith("Archive/")
        assert not original.exists()
        assert Path(store.path(record.path)).exists()
        assert store.changed[str(original)] > initial[str(original)]


def test_preserve_removed_message_keeps_content_and_clears_remote_binding(
    tmp_path: Path,
) -> None:
    with MailStore(str(tmp_path)) as store:
        record = _display(store)
        original = Path(store.path(record.path))
        content = original.read_bytes()
        store.preserve_local(record)
        assert not original.exists()
        assert record.folder == ".email/recovery"
        assert record.mailbox == ""
        assert record.uid == 0
        assert Path(store.path(record.path)).read_bytes() == content
        assert store.records()[0].path == record.path


def test_account_binding_allows_same_account_and_rejects_new_host_port_or_user(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with MailStore(str(tmp_path)) as store:
        store.bind_account(config)
        store.bind_account(config)
    with MailStore(str(tmp_path)) as store:
        for changed in (
            replace(config.imap, host="other.example.test"),
            replace(config.imap, port=999),
            replace(config.imap, username="other@example.test"),
        ):
            with pytest.raises(EmailError) as error:
                store.bind_account(replace(config, imap=changed))
            assert error.value.reason == "account_changed"


def test_managed_draft_must_match_registered_account_identity_and_path(
    tmp_path: Path,
) -> None:
    with (
        MailStore(str(tmp_path / "first")) as first,
        MailStore(str(tmp_path / "second")) as second,
    ):
        draft = replace(
            new_draft(IDENTITY), to="friend@example.test", body="Draft content"
        )
        first.create_draft(draft, "new email.md")
        loaded, relative, text = first.read_draft(first.path("new email.md"), 100_000)
        assert (loaded, relative, text) == (draft, "new email.md", render_draft(draft))
        first.write_text("Drafts/copied.md", text)
        second.write_text("new email.md", text)
        for store, path in ((first, "Drafts/copied.md"), (second, "new email.md")):
            with pytest.raises(EmailError) as error:
                store.read_draft(store.path(path), 100_000)
            assert error.value.reason == "unmanaged_draft"


@pytest.mark.parametrize(
    "change", ["identity", "literal", "identity_first", "copied_path"]
)
def test_managed_messages_require_exact_literal_identity_markers_and_registered_path(
    tmp_path: Path, change: str
) -> None:
    with MailStore(str(tmp_path)) as store:
        record = _display(store)
        path = Path(store.path(record.path))
        original = path.read_text()
        if change == "identity":
            path.write_text(original.replace(IDENTITY, "0" * 32))
        elif change == "literal":
            path.write_text(original.replace("email: message", "email: ordinary", 1))
        elif change == "identity_first":
            lines = original.splitlines(keepends=True)
            path.write_text(lines[1] + lines[0] + "".join(lines[2:]))
        else:
            path = tmp_path / "copy.md"
            path.write_text(original)
        with pytest.raises(EmailError):
            store.record_for_path(str(path))


def test_raw_payload_roundtrips_exact_bytes(tmp_path: Path) -> None:
    raw = b"From: example@test\r\nX-Header: folded\r\n continuation\n\n\x00\xffbody\r\n"
    with MailStore(str(tmp_path)) as store:
        record = MessageRecord(
            IDENTITY,
            "Inbox",
            "INBOX",
            17,
            31,
            "",
            ".email/raw/.original.eml",
            False,
            fingerprint(raw),
            raw_bytes=len(raw),
        )
        store.write_blob(record.raw_path, raw)
        store.save_record(record)
        assert store.raw(record, len(raw)) == raw
        with pytest.raises(ValueError, match="size limit"):
            store.raw(record, len(raw) - 1)


def test_raw_payload_tampering_is_detected_before_reuse(tmp_path: Path) -> None:
    with MailStore(str(tmp_path)) as store:
        record = _display(store)
        Path(store.path(record.raw_path)).write_bytes(b"modified message")
        with pytest.raises(EmailError) as error:
            store.raw(record, 100_000)
        assert error.value.reason == "payload_changed"


@pytest.mark.parametrize("checkpoint", ["before_write", "before_database"])
@pytest.mark.parametrize("edited_after_interruption", [False, True])
def test_display_recovers_interrupted_rename_without_losing_edits(
    tmp_path: Path, monkeypatch, checkpoint: str, edited_after_interruption: bool
) -> None:
    with MailStore(str(tmp_path)) as store:
        record = _display(store)
        original = Path(store.path(record.path))
        message = decode_message(store.raw(record, 100_000))
        record.read = True
        destination = (
            tmp_path / "Inbox" / message_filename(message, record.identity, read=True)
        )

        def interrupted(*args, **kwargs):
            raise RuntimeError("simulated interruption")

        with monkeypatch.context() as patch:
            patch.setattr(
                store,
                "write_text" if checkpoint == "before_write" else "save_record",
                interrupted,
            )
            with pytest.raises(RuntimeError, match="simulated interruption"):
                store.display(record, message)
        assert destination.exists()
        assert not original.exists()
        if edited_after_interruption:
            destination.write_text(
                destination.read_text() + "\nEdited after interruption\n"
            )
        preserved_content = destination.read_bytes()

    with MailStore(str(tmp_path)) as reopened:
        record = reopened.records()[0]
        assert Path(reopened.path(record.path)) == original
        record.read = True
        reopened.display(record, message)
        assert Path(reopened.path(record.path)) == destination
        assert "**Status:** read" in destination.read_text()
        local = list((tmp_path / ".email/recovery").glob("*"))
        if edited_after_interruption:
            assert len(local) == 1
            assert local[0].read_bytes() == preserved_content
        else:
            assert local == []


def test_display_does_not_claim_unrelated_destination_during_recovery(
    tmp_path: Path,
) -> None:
    with MailStore(str(tmp_path)) as store:
        record = _display(store)
        message = decode_message(store.raw(record, 100_000))
        original = Path(store.path(record.path))
        original.unlink()
        record.read = True
        destination = (
            tmp_path / "Inbox" / message_filename(message, record.identity, read=True)
        )
        content = f"{LITERAL_MARKER}\n<!-- lucy-email-id:{'0' * 32} -->\nOther file\n"
        destination.write_text(content)
        with pytest.raises(EmailError) as error:
            store.display(record, message)
        assert error.value.reason == "destination_exists"
        assert destination.read_text() == content
