from __future__ import annotations

import logging
import os
import uuid
from dataclasses import replace

from demon_lucy.lib.logfmt import log_record
from demon_lucy.modules.email.codec import decode_message
from demon_lucy.modules.email.errors import EmailError
from demon_lucy.modules.email.imap import ImapSession
from demon_lucy.modules.email.models import AccountConfig, DecodedMessage
from demon_lucy.modules.email.storage import MailStore, MessageRecord, fingerprint

logger = logging.getLogger(__name__)


def mailbox_names(config: AccountConfig, session: ImapSession) -> dict[str, str]:
    available = session.mailboxes()
    names = {item.name for item in available}
    result: dict[str, str] = {}
    for folder, configured in config.mailboxes.items():
        if configured:
            if configured in names or (
                folder == "Inbox" and configured.upper() == "INBOX"
            ):
                result[folder] = configured
            else:
                raise EmailError(
                    f"The configured {folder} mailbox does not exist.",
                    reason="mailbox_missing",
                )
        else:
            matches = [
                item.name
                for item in available
                if ("\\" + folder).casefold()
                in {flag.casefold() for flag in item.flags}
            ]
            if len(matches) == 1:
                result[folder] = matches[0]
    if len(set(result.values())) != len(result):
        raise EmailError(
            "Inbox, Sent, Archive, and Trash must use different mailboxes.",
            reason="invalid_config",
        )
    return result


def require_mailbox(names: dict[str, str], folder: str) -> str:
    if folder not in names:
        raise EmailError(
            f"Set --email-{folder.lower()}-mailbox in the account file; no unique special-use folder was found.",
            reason="mailbox_unconfigured",
        )
    return names[folder]


def decoded_record(store: MailStore, record: MessageRecord) -> DecodedMessage:
    message = decode_message(store.raw(record, record.raw_bytes))
    if record.oversized:
        message = replace(
            message,
            body="Message exceeds the configured size limit. Increase --email-max-message-bytes and refresh to download it.",
            attachments=(),
        )
    return message


def _instance(store: MailStore) -> uuid.UUID:
    saved = store.get("account", "instance")
    if saved is None:
        saved = {"id": uuid.uuid4().hex}
        store.put("account", "instance", saved)
    return uuid.UUID(saved["id"])


def _reconcile_copied_moves(
    store: MailStore,
    names: dict[str, str],
    snapshots: dict[str, tuple[int, list[int]]],
    *,
    event_id: str,
) -> None:
    """Reconcile acknowledged copies once source removal is observable."""
    folders = {mailbox: folder for folder, mailbox in names.items()}
    for identity, movement in store.items("move"):
        if movement["state"] == "complete":
            continue
        details = movement.get("details", {})
        destination = movement["destination"]
        source_snapshot = snapshots.get(movement["source"])
        destination_snapshot = snapshots.get(destination)
        destination_uid = details.get("uid")
        destination_validity = details.get("uidvalidity")
        if (
            not source_snapshot
            or source_snapshot[0] != movement["uidvalidity"]
            or movement["uid"] in source_snapshot[1]
            or not destination_snapshot
            or destination_snapshot[0] != destination_validity
            or destination_uid not in destination_snapshot[1]
        ):
            continue
        saved = store.get("message", identity)
        if saved is None:
            continue
        original = MessageRecord(**saved)
        source_binding = (movement["source"], movement["uidvalidity"], movement["uid"])
        destination_binding = (destination, destination_validity, destination_uid)
        if original.uid and (
            original.mailbox,
            original.uidvalidity,
            original.uid,
        ) not in {
            source_binding,
            destination_binding,
        }:
            continue
        duplicates = [
            record
            for record in store.records()
            if record.identity != identity
            and (record.mailbox, record.uidvalidity, record.uid) == destination_binding
        ]
        if len(duplicates) > 1:
            continue
        source = duplicates[0] if duplicates else original
        rebound = replace(
            original,
            folder=folders[destination],
            mailbox=destination,
            uidvalidity=destination_validity,
            uid=destination_uid,
            raw_path=source.raw_path,
            read=source.read,
            digest=source.digest,
            raw_bytes=source.raw_bytes,
            oversized=source.oversized,
        )
        store.display(rebound, decoded_record(store, rebound))
        for duplicate in duplicates:
            store.retire_duplicate(duplicate)
        store.put("move", identity, {**movement, "state": "complete"})
        logger.info(
            log_record(
                "email.move_reconciled",
                id=event_id,
                path=store.path(rebound.path),
                source_mailbox=movement["source"],
                source_uid=movement["uid"],
                destination_mailbox=destination,
                uid=destination_uid,
                uidvalidity=destination_validity,
            )
        )


def fetch_mail(config: AccountConfig, store: MailStore, *, event_id: str) -> None:
    store.bind_account(config)
    instance = _instance(store)
    with ImapSession(config.imap) as session:
        names = mailbox_names(config, session)
        snapshots: dict[str, tuple[int, list[int]]] = {}
        # Read memberships first, so an external move can retain the local identity.
        for folder, mailbox in names.items():
            validity = session.select(mailbox, readonly=True)
            snapshots[mailbox] = validity, sorted(set(session.uids()))
        for record in store.records():
            snapshot = snapshots.get(record.mailbox)
            if (
                record.uid
                and snapshot
                and (record.uidvalidity != snapshot[0] or record.uid not in snapshot[1])
            ):
                store.preserve_local(record)

        _reconcile_copied_moves(store, names, snapshots, event_id=event_id)

        for folder, mailbox in names.items():
            validity, uids = snapshots[mailbox]
            previous = store.get("mailbox", mailbox)
            if previous is None or previous["uidvalidity"] != validity:
                selected = (
                    uids[-config.initial_limit :] if config.initial_limit else uids
                )
                last_uid = selected[0] - 1 if selected else 0
                previous = {"uidvalidity": validity, "last_uid": last_uid}
                store.put("mailbox", mailbox, previous)
            if session.select(mailbox, readonly=True) != validity:
                raise EmailError(
                    "Mailbox changed during fetch; refresh again.",
                    reason="mailbox_changed",
                    retryable=True,
                )
            records = {
                record.uid: record
                for record in store.records()
                if record.mailbox == mailbox
                and record.uidvalidity == validity
                and record.uid
            }
            new_uids = [uid for uid in uids if uid > previous["last_uid"]]
            wanted = sorted(set(new_uids) | (records.keys() & set(uids)))
            metadata = session.metadata(wanted) if wanted else {}
            for uid in wanted:
                info = metadata.get(uid)
                if info is None:
                    if uid in session.uids():
                        raise EmailError(
                            "The server omitted message metadata; fetch will retry without advancing its checkpoint.",
                            reason="fetch_incomplete",
                            retryable=True,
                        )
                    continue
                record = records.get(uid)
                download = record is None or (
                    record.oversized and info.size <= config.max_message_bytes
                )
                if download:
                    oversized = info.size > config.max_message_bytes
                    raw = (
                        session.headers(uid)
                        if oversized
                        else session.fetch(uid, config.max_message_bytes)
                    )
                    digest = fingerprint(raw)
                    message = decode_message(raw)
                    if record is None:
                        candidates = [
                            item
                            for item in store.records()
                            if not item.uid
                            and item.digest == digest
                            and not item.oversized
                        ]
                        # Servers may add headers to their own SMTP Sent copy.
                        if not candidates and folder == "Sent" and message.message_id:
                            generations = [
                                key
                                for key, value in store.items("send")
                                if value.get("message_id") == message.message_id
                                and value.get("state") in {"sent", "partial"}
                            ]
                            candidates = [
                                item
                                for item in store.records()
                                if not item.uid
                                and item.identity
                                in {
                                    uuid.uuid5(uuid.UUID(generation), "sent").hex
                                    for generation in generations
                                }
                            ]
                        if len(candidates) == 1:
                            record = candidates[0]
                        else:
                            identity = uuid.uuid5(
                                instance, f"{mailbox}\0{validity}\0{uid}"
                            ).hex
                            record = MessageRecord(
                                identity,
                                folder,
                                mailbox,
                                validity,
                                uid,
                                "",
                                "",
                                "\\Seen" in info.flags,
                                digest,
                                raw_bytes=len(raw),
                            )
                    record.folder = folder
                    record.mailbox = mailbox
                    record.uidvalidity = validity
                    record.uid = uid
                    record.read = "\\Seen" in info.flags
                    record.digest = digest
                    record.raw_bytes = len(raw)
                    record.oversized = oversized
                    record.raw_path = f".email/raw/.{record.identity}-{digest[:16]}.{'headers' if oversized else 'eml'}"
                    store.write_blob(record.raw_path, raw)
                    if oversized:
                        message = replace(
                            message,
                            body="Message exceeds the configured size limit. Increase --email-max-message-bytes and refresh to download it.",
                            attachments=(),
                        )
                    store.display(record, message)
                    logger.info(
                        log_record(
                            "email.message_received",
                            id=event_id,
                            path=store.path(record.path),
                            mailbox=folder,
                            uid=uid,
                            oversized=oversized,
                        )
                    )
                elif record.read != ("\\Seen" in info.flags):
                    record.read = "\\Seen" in info.flags
                    store.display(record, decoded_record(store, record))
                elif not os.path.exists(store.path(record.path)):
                    store.display(record, decoded_record(store, record))
                if uid > previous["last_uid"]:
                    previous["last_uid"] = uid
                    store.put("mailbox", mailbox, previous)
                movement = store.get("move", record.identity)
                if (
                    movement
                    and movement["destination"] == record.mailbox
                    and movement["state"] != "complete"
                ):
                    store.put(
                        "move", record.identity, {**movement, "state": "complete"}
                    )
        for folder in config.mailboxes.keys() - names.keys():
            logger.warning(
                log_record(
                    "email.mailbox_unconfigured",
                    id=event_id,
                    folder=folder,
                    reason="no_unique_special_use",
                )
            )
    logger.info(
        log_record(
            "email.fetch_done", id=event_id, path=config.root, folders=len(names)
        )
    )


def change_message(
    config: AccountConfig, store: MailStore, path: str, action: str, *, event_id: str
) -> str:
    record = store.record_for_path(path)
    if not record.uid or not record.mailbox:
        raise EmailError(
            "This message has no current remote binding; refresh the account.",
            reason="message_unbound",
        )
    pending = store.get("move", record.identity)
    if pending and pending["state"] != "complete":
        raise EmailError(
            "A previous move is unresolved. Refresh and check the server before changing this message.",
            reason="move_uncertain",
        )
    store.bind_account(config)
    with ImapSession(config.imap) as session:
        if session.select(record.mailbox) != record.uidvalidity:
            raise EmailError(
                "Mailbox identity changed. Refresh before changing this message.",
                reason="mailbox_changed",
            )
        if record.uid not in session.uids():
            raise EmailError(
                "The message moved on the server. Refresh the account.",
                reason="message_missing",
            )
        if action in {"email-mark-read", "email-mark-unread"}:
            read = action == "email-mark-read"
            session.set_seen(record.uid, read)
            record.read = read
        else:
            folder = "Archive" if action == "email-archive" else "Trash"
            names = mailbox_names(config, session)
            destination = require_mailbox(names, folder)
            if destination != record.mailbox:
                movement = {
                    "state": "pending",
                    "source": record.mailbox,
                    "uidvalidity": record.uidvalidity,
                    "uid": record.uid,
                    "destination": destination,
                }

                def checkpoint(phase: str, details: dict) -> None:
                    store.put(
                        "move",
                        record.identity,
                        {**movement, "phase": phase, "details": details},
                    )

                result = session.move(record.uid, destination, checkpoint=checkpoint)
                record.mailbox = destination
                record.uidvalidity = result.uidvalidity or 0
                record.uid = result.uid or 0
                record.folder = folder
                store.put("move", record.identity, {**movement, "state": "complete"})
        store.display(record, decoded_record(store, record))
    logger.info(
        log_record(
            "email.message_changed",
            id=event_id,
            path=store.path(record.path),
            command=action,
        )
    )
    return store.path(record.path)
