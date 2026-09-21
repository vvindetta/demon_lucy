from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from demon_lucy.lib.text_file import (
    SourceChangedError,
    write_bytes_atomic,
    write_text_atomic,
)
from demon_lucy.modules.email.documents import LITERAL_MARKER
from demon_lucy.modules.email.errors import EmailError
from demon_lucy.modules.email.files import (
    read_bytes_no_follow,
    safe_path,
    write_text_if_missing,
)
from demon_lucy.modules.email.models import AccountConfig, DecodedMessage, Draft


def fingerprint(content: bytes | str) -> str:
    return hashlib.sha256(
        content.encode("utf-8") if isinstance(content, str) else content
    ).hexdigest()


def _preserved_path(relative: str) -> str:
    name = re.sub(r"^(?:[0-9a-fA-F]{32}-)+", "", Path(relative).name)
    suffix = ".md" if name.lower().endswith(".md") else ""
    stem = name[: -len(suffix)] if suffix else name
    maximum = 255 - 33 - len(suffix.encode("utf-8"))
    stem = stem.encode("utf-8")[:maximum].decode("utf-8", errors="ignore")
    return f"Local-only/{uuid.uuid4().hex}-{stem}{suffix}"


@dataclass
class MessageRecord:
    identity: str
    folder: str
    mailbox: str
    uidvalidity: int
    uid: int
    path: str
    raw_path: str
    read: bool
    digest: str
    raw_bytes: int
    rendered_digest: str = ""
    oversized: bool = False


class MailStore:
    """Account-local durable state. Callers hold the account lock during use."""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.changed: dict[str, int] = {}
        self.ensure_directory(".email")
        db_path = self.path(".email/.state.sqlite3")
        for suffix in ("", "-journal", "-wal", "-shm"):
            self.path(".email/.state.sqlite3" + suffix)
        self.connection = sqlite3.connect(db_path, timeout=0)
        os.chmod(db_path, 0o600)
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(kind, key))"
        )
        self.connection.commit()

    def __enter__(self) -> MailStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.connection.close()

    def path(self, relative: str) -> str:
        return safe_path(self.root, relative)

    def relative(self, path: str) -> str:
        relative = os.path.relpath(os.path.abspath(path), self.root)
        self.path(relative)
        return relative

    def ensure_directory(self, relative: str) -> str:
        if relative == ".":
            if os.path.islink(self.root):
                raise ValueError("symlink root is not allowed")
            os.makedirs(self.root, mode=0o700, exist_ok=True)
            return self.root
        path = self.path(relative)
        os.makedirs(path, mode=0o700, exist_ok=True)
        return self.path(relative)

    def get(self, kind: str, key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT value FROM records WHERE kind=? AND key=?", (kind, key)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, kind: str, key: str, value: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO records(kind,key,value) VALUES(?,?,?) ON CONFLICT(kind,key) DO UPDATE SET value=excluded.value",
            (kind, key, json.dumps(value, ensure_ascii=False)),
        )
        self.connection.commit()

    def items(self, kind: str) -> list[tuple[str, dict[str, Any]]]:
        return [
            (key, json.loads(value))
            for key, value in self.connection.execute(
                "SELECT key,value FROM records WHERE kind=? ORDER BY key", (kind,)
            )
        ]

    def bind_account(self, config: AccountConfig) -> None:
        identity = {
            "host": config.imap.host,
            "port": config.imap.port,
            "username": config.imap.username,
        }
        saved = self.get("account", "binding")
        if saved is not None and saved != identity:
            raise EmailError(
                "This directory belongs to another IMAP account. Initialize a separate email directory.",
                reason="account_changed",
            )
        if saved is None:
            self.put("account", "binding", identity)

    def read_text(self, relative: str, max_bytes: int) -> str:
        return read_bytes_no_follow(self.path(relative), max_bytes).decode("utf-8")

    def write_text(
        self, relative: str, text: str, *, expected: str | None = None
    ) -> None:
        self.ensure_directory(str(Path(relative).parent))
        path = self.path(relative)
        if expected is None:
            if not write_text_if_missing(path, text):
                if read_bytes_no_follow(path, len(text.encode("utf-8"))) != text.encode(
                    "utf-8"
                ):
                    raise SourceChangedError("Email destination already exists")
                return
        elif text == expected:
            return
        else:
            write_text_atomic(path, text, expected_text=expected)
        self.changed[path] = self.changed.get(path, 0) + 1

    def write_blob(self, relative: str, content: bytes) -> None:
        self.ensure_directory(str(Path(relative).parent))
        path = self.path(relative)
        if os.path.exists(path):
            if read_bytes_no_follow(path, len(content)) != content:
                raise EmailError(
                    "A stored email payload was modified.", reason="payload_changed"
                )
            return
        write_bytes_atomic(path, content)

    def records(self) -> list[MessageRecord]:
        return [MessageRecord(**value) for _, value in self.items("message")]

    def save_record(self, record: MessageRecord) -> None:
        self.put("message", record.identity, asdict(record))

    def record_for_path(self, path: str) -> MessageRecord:
        relative = self.relative(path)
        found = [record for record in self.records() if record.path == relative]
        if len(found) != 1:
            raise EmailError(
                "Drop a managed message from this email account.",
                reason="unmanaged_message",
            )
        record = found[0]
        text = self.read_text(relative, 128 * 1024 * 1024)
        if text.splitlines()[:2] != [
            LITERAL_MARKER,
            f"<!-- lucy-email-id:{record.identity} -->",
        ]:
            raise EmailError(
                "The message identity was changed.", reason="identity_changed"
            )
        return record

    def preserve_local(self, record: MessageRecord) -> None:
        old_path = self.path(record.path)
        if os.path.exists(old_path):
            self.ensure_directory("Local-only")
            new_relative = _preserved_path(record.path)
            os.rename(old_path, self.path(new_relative))
            self.changed[old_path] = self.changed.get(old_path, 0) + 1
            self.changed[self.path(new_relative)] = 1
        else:
            new_relative = record.path
        record.path = new_relative
        record.folder = "Local-only"
        record.mailbox = ""
        record.uid = 0
        self.save_record(record)

    def raw(self, record: MessageRecord, max_bytes: int) -> bytes:
        content = read_bytes_no_follow(
            self.path(record.raw_path), min(max_bytes, record.raw_bytes)
        )
        if fingerprint(content) != record.digest:
            raise EmailError(
                "The stored message payload was modified.", reason="payload_changed"
            )
        return content

    def retire_duplicate(self, record: MessageRecord) -> None:
        """Retire a reconciled copy's binding, preserving its visible content."""
        path = self.path(record.path)
        if os.path.exists(path):
            # Keep the content even when its last-read digest was unchanged:
            # an editor may have added an annotation just before this rename.
            self.ensure_directory("Local-only")
            preserved = self.path(_preserved_path(record.path))
            os.rename(path, preserved)
            self.changed[preserved] = self.changed.get(preserved, 0) + 1
            self.changed[path] = self.changed.get(path, 0) + 1
        self.connection.execute(
            "DELETE FROM records WHERE kind=? AND key=?", ("message", record.identity)
        )
        self.connection.commit()

    def display(
        self, record: MessageRecord, message: DecodedMessage, *, status: str = ""
    ) -> None:
        from demon_lucy.modules.email.codec import message_filename, render_message

        relative = f"{record.folder}/{message_filename(message, record.identity, read=record.read)}"
        links: list[tuple[str, str]] = []
        for index, attachment in enumerate(message.attachments):
            attachment_path = f".email/attachments/.{record.identity}-{index}.blob"
            self.write_blob(attachment_path, attachment.content)
            links.append(
                (
                    attachment.filename,
                    os.path.relpath(
                        self.path(attachment_path), os.path.dirname(self.path(relative))
                    ).replace("\\", "/"),
                )
            )
        text = render_message(
            message,
            identity=record.identity,
            read=record.read,
            attachment_links=links,
            status=status,
        )
        old_relative = record.path
        old_path = self.path(old_relative) if old_relative else None
        old_text = None
        if old_path and os.path.exists(old_path):
            old_text = self.read_text(old_relative, 128 * 1024 * 1024)
        elif os.path.exists(self.path(relative)):
            # A prior rename/write can have completed before the database
            # checkpoint. Only reclaim a destination bearing this identity.
            old_relative = relative
            old_path = self.path(relative)
            old_text = self.read_text(relative, 128 * 1024 * 1024)
            if old_text.splitlines()[:2] != [
                LITERAL_MARKER,
                f"<!-- lucy-email-id:{record.identity} -->",
            ]:
                raise EmailError(
                    "A message destination already exists.", reason="destination_exists"
                )
        if old_text is not None and fingerprint(old_text) not in {
            record.rendered_digest,
            fingerprint(text),
        }:
            self.ensure_directory("Local-only")
            preserved = _preserved_path(old_relative)
            os.rename(old_path, self.path(preserved))
            self.changed[old_path] = self.changed.get(old_path, 0) + 1
            self.changed[self.path(preserved)] = 1
            old_text = None
        if old_text is not None and old_relative != relative:
            self.ensure_directory(record.folder)
            if os.path.exists(self.path(relative)):
                raise EmailError(
                    "A message destination already exists.", reason="destination_exists"
                )
            os.rename(old_path, self.path(relative))
            self.changed[old_path] = self.changed.get(old_path, 0) + 1
            self.changed[self.path(relative)] = (
                self.changed.get(self.path(relative), 0) + 1
            )
        self.write_text(relative, text, expected=old_text)
        record.path = relative
        record.rendered_digest = fingerprint(text)
        self.save_record(record)

    def create_draft(self, draft: Draft, relative: str) -> None:
        from demon_lucy.modules.email.codec import render_draft

        text = render_draft(draft)
        self.write_text(relative, text)
        self.put("draft", draft.identity, {"path": relative})

    def read_draft(self, path: str, max_bytes: int) -> tuple[Draft, str, str]:
        from demon_lucy.modules.email.codec import parse_draft

        relative = self.relative(path)
        text = self.read_text(relative, max_bytes)
        draft = parse_draft(text)
        saved = self.get("draft", draft.identity)
        if saved is None or saved["path"] != relative:
            raise EmailError(
                "Use new email.md or a reply draft from this account.",
                reason="unmanaged_draft",
            )
        return draft, relative, text
