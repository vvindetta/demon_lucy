from __future__ import annotations

import logging
import os
import uuid
from dataclasses import replace

from demon_lucy.lib.logfmt import log_record
from demon_lucy.modules.email import smtp
from demon_lucy.modules.email.codec import (
    build_outgoing,
    decode_message,
    new_draft,
    parse_draft,
    render_draft,
)
from demon_lucy.modules.email.errors import EmailError
from demon_lucy.modules.email.files import read_bytes_no_follow
from demon_lucy.modules.email.imap import ImapSession
from demon_lucy.modules.email.models import AccountConfig, SentCopyMode
from demon_lucy.modules.email.storage import MailStore, MessageRecord, fingerprint
from demon_lucy.modules.email.sync import mailbox_names, require_mailbox

logger = logging.getLogger(__name__)


def _payload(store: MailStore, journal: dict) -> bytes:
    content = read_bytes_no_follow(
        store.path(journal["payload_path"]), journal["payload_bytes"]
    )
    if fingerprint(content) != journal["payload_digest"]:
        raise EmailError(
            "The queued message payload was modified. The original draft snapshot is retained; do not resend this generation.",
            reason="payload_changed",
        )
    return content


def _save_local_sent(
    config: AccountConfig, store: MailStore, generation: str, journal: dict
) -> MessageRecord:
    identity = journal.get(
        "sent_identity", uuid.uuid5(uuid.UUID(generation), "sent").hex
    )
    saved = store.get("message", identity)
    if saved:
        return MessageRecord(**saved)
    payload = _payload(store, journal)
    # A refresh may already have imported the provider's Sent copy after a
    # previous local write failed. Reuse an exact copy instead of duplicating it.
    matches = [
        record
        for record in store.records()
        if record.folder == "Sent" and record.digest == fingerprint(payload)
    ]
    if len(matches) == 1:
        store.raw(matches[0], journal["payload_bytes"])
        journal["sent_identity"] = matches[0].identity
        store.put("send", generation, journal)
        return matches[0]
    record = MessageRecord(
        identity,
        "Sent",
        "",
        0,
        0,
        "",
        journal["payload_path"],
        True,
        fingerprint(payload),
        raw_bytes=len(payload),
    )
    store.display(
        record,
        decode_message(payload),
        status="Some recipients were rejected; see status.md."
        if journal["state"] == "partial"
        else "",
    )
    return record


def _renew_draft(
    store: MailStore, relative: str, current: str, *, blank: bool = False
) -> None:
    draft = (
        new_draft(uuid.uuid4().hex)
        if blank
        else replace(parse_draft(current), identity=uuid.uuid4().hex)
    )
    # Registration precedes the guarded write, so a completed replacement is
    # always usable even if the process stops before journal finalization.
    store.put("draft", draft.identity, {"path": relative})
    store.write_text(relative, render_draft(draft), expected=current)


def _recover_retirement(
    store: MailStore, generation: str, journal: dict, *, max_bytes: int
) -> None:
    destination = journal["retirement_path"]
    captured = store.read_text(destination, max_bytes)
    relative = journal["draft_path"]
    if fingerprint(captured) != journal["draft_digest"]:
        recovery = journal.get("retirement_recovery")
        if recovery is None:
            identity = uuid.uuid4().hex
            recovery = {
                "identity": identity,
                "path": relative
                if not os.path.exists(store.path(relative))
                else f"Drafts/Recovered [{identity}].md",
            }
            journal["retirement_recovery"] = recovery
            store.put("send", generation, journal)
        try:
            recovered = replace(parse_draft(captured), identity=recovery["identity"])
        except EmailError:
            # Even an in-progress, temporarily malformed editor buffer must
            # return to a visible file; the submitted generation stays blocked.
            store.write_text(recovery["path"], captured)
            raise
        store.put("draft", recovered.identity, {"path": recovery["path"]})
        target = store.path(recovery["path"])
        if os.path.exists(target):
            visible = store.read_text(recovery["path"], max_bytes)
            if parse_draft(visible).identity != recovered.identity:
                raise EmailError(
                    "The recovered draft destination changed; the private snapshot is retained.",
                    reason="destination_exists",
                )
        else:
            store.write_text(recovery["path"], render_draft(recovered))
    # An editor may recreate the original pathname while the snapshot is being
    # inspected. Preserve that independent edit as a usable new generation too.
    if os.path.exists(store.path(relative)):
        current = store.read_text(relative, max_bytes)
        if parse_draft(current).identity == generation:
            _renew_draft(store, relative, current)


def _finish_draft(
    store: MailStore, generation: str, journal: dict, *, max_bytes: int
) -> None:
    if journal.get("local_done") or journal["state"] != "sent":
        return
    relative = journal["draft_path"]
    if journal.get("retirement_path") and os.path.exists(
        store.path(journal["retirement_path"])
    ):
        _recover_retirement(store, generation, journal, max_bytes=max_bytes)
        journal["local_done"] = True
        store.put("send", generation, journal)
        return
    try:
        current = store.read_text(relative, max_bytes)
    except FileNotFoundError:
        journal["local_done"] = True
        store.put("send", generation, journal)
        return
    parsed = parse_draft(current)
    if parsed.identity != generation:
        # A prior successful reset may have preceded a crash before journal commit.
        journal["local_done"] = True
        store.put("send", generation, journal)
        return
    if relative == "new email.md" or fingerprint(current) != journal["draft_digest"]:
        _renew_draft(
            store,
            relative,
            current,
            blank=fingerprint(current) == journal["draft_digest"],
        )
    else:
        destination = f".email/outgoing/.{generation}.sent-draft"
        store.ensure_directory(".email/outgoing")
        if os.path.exists(store.path(destination)):
            raise EmailError(
                "The sent draft recovery destination already exists.",
                reason="destination_exists",
            )
        journal["retirement_path"] = destination
        store.put("send", generation, journal)
        os.rename(store.path(relative), store.path(destination))
        store.changed[store.path(relative)] = (
            store.changed.get(store.path(relative), 0) + 1
        )
        _recover_retirement(store, generation, journal, max_bytes=max_bytes)
    journal["local_done"] = True
    store.put("send", generation, journal)


def _save_server_sent(
    config: AccountConfig,
    store: MailStore,
    generation: str,
    journal: dict,
    record: MessageRecord,
) -> None:
    if journal.get("copy_done"):
        return
    if config.sent_copy is SentCopyMode.SERVER:
        journal["copy_done"] = True
        store.put("send", generation, journal)
        return
    payload = _payload(store, journal)
    with ImapSession(config.imap) as session:
        destination = require_mailbox(mailbox_names(config, session), "Sent")
        found = session.find_message(destination, journal["message_id"])
        validity = session.select(destination)
        # Even a first append reconciles the stable ID, avoiding duplicates if
        # the provider saved a Sent copy despite the selected account policy.
        matches = [
            uid
            for uid in found
            if fingerprint(
                session.fetch(
                    uid, max(config.max_message_bytes, journal["max_message_bytes"])
                )
                .replace(b"\r\n", b"\n")
                .rstrip(b"\n")
            )
            == fingerprint(payload.replace(b"\r\n", b"\n").rstrip(b"\n"))
        ]
        if len(matches) == 1:
            record.mailbox, record.uidvalidity, record.uid = (
                destination,
                validity,
                matches[0],
            )
        elif found or journal.get("append_started"):
            raise EmailError(
                "Delivery succeeded, but the server Sent copy is uncertain. Check the server; Lucy will not resend or append another copy automatically.",
                reason="sent_copy_uncertain",
            )
        else:
            journal["append_started"] = True
            store.put("send", generation, journal)
            result = session.append(destination, payload)
            record.mailbox = destination
            record.uidvalidity = result.uidvalidity or 0
            record.uid = result.uid or 0
        store.save_record(record)
        journal["copy_done"] = True
        store.put("send", generation, journal)


def finish_sent(
    config: AccountConfig, store: MailStore, generation: str, journal: dict
) -> None:
    record = _save_local_sent(config, store, generation, journal)
    _finish_draft(
        store,
        generation,
        journal,
        max_bytes=max(config.max_message_bytes, journal["max_message_bytes"]),
    )
    _save_server_sent(config, store, generation, journal, record)


def recover_sent(config: AccountConfig, store: MailStore, *, event_id: str) -> None:
    store.bind_account(config)
    for generation, journal in store.items("send"):
        if journal["state"] == "sending":
            journal["state"] = "uncertain"
            store.put("send", generation, journal)
        if journal["state"] not in {"sent", "partial"}:
            continue
        if journal.get("copy_done") and (
            journal.get("local_done") or journal["state"] == "partial"
        ):
            continue
        try:
            finish_sent(config, store, generation, journal)
        except EmailError as exc:
            logger.warning(
                log_record(
                    "email.sent_copy_pending",
                    id=event_id,
                    path=config.root,
                    draft=generation,
                    reason=exc.reason,
                )
            )


def send_draft(
    config: AccountConfig, store: MailStore, path: str, *, event_id: str
) -> str:
    store.bind_account(config)
    draft, relative, text = store.read_draft(path, config.max_message_bytes)
    journal = store.get("send", draft.identity)
    if journal and journal["state"] in {"sent", "partial", "sending", "uncertain"}:
        if journal["state"] == "sent":
            finish_sent(config, store, draft.identity, journal)
        raise EmailError(
            "This draft was already submitted or delivery is uncertain. Check status.md and the server before creating a fresh draft.",
            reason="draft_already_submitted",
        )
    outgoing = build_outgoing(
        draft,
        from_address=config.from_address,
        draft_path=path,
        max_bytes=config.max_message_bytes,
    )
    digest = fingerprint(outgoing.payload)
    payload_path = f".email/outgoing/.{draft.identity}-{digest[:16]}.eml"
    store.write_blob(payload_path, outgoing.payload)
    snapshot_path = f".email/outgoing/.{draft.identity}-{fingerprint(text)[:16]}.draft"
    store.write_blob(snapshot_path, text.encode("utf-8"))
    journal = {
        "state": "prepared",
        "draft_path": relative,
        "draft_digest": fingerprint(text),
        "snapshot_path": snapshot_path,
        "payload_path": payload_path,
        "payload_digest": digest,
        "payload_bytes": len(outgoing.payload),
        "max_message_bytes": config.max_message_bytes,
        "message_id": outgoing.message_id,
        "recipients": list(outgoing.recipients),
        "accepted": [],
        "refused": [],
    }
    store.put("send", draft.identity, journal)

    def before_data() -> None:
        journal["state"] = "sending"
        store.put("send", draft.identity, journal)

    try:
        result = smtp.send(
            config.smtp,
            outgoing.sender,
            outgoing.recipients,
            outgoing.payload,
            before_data=before_data,
        )
    except EmailError as exc:
        journal["state"] = "uncertain" if exc.delivery_uncertain else "failed"
        journal["accepted"] = list(exc.accepted) if exc.delivery_uncertain else []
        journal["refused"] = list(exc.refused)
        journal["reason"] = exc.reason
        store.put("send", draft.identity, journal)
        raise
    journal["state"] = "partial" if result.refused else "sent"
    journal["accepted"], journal["refused"] = (
        list(result.accepted),
        list(result.refused),
    )
    store.put("send", draft.identity, journal)
    logger.info(
        log_record(
            "email.sent",
            id=event_id,
            path=path,
            accepted=len(result.accepted),
            refused=len(result.refused),
        )
    )
    try:
        finish_sent(config, store, draft.identity, journal)
    except EmailError as exc:
        raise EmailError(
            "The message was sent. Saving its server Sent copy needs attention; refresh and check status.md. Do not send it again.",
            reason=exc.reason,
            retryable=exc.retryable,
        ) from None
    if result.refused:
        raise EmailError(
            "Some recipients were rejected. Check status.md; accepted recipients will not be sent the message again.",
            reason="partial_delivery",
        )
    saved = store.get(
        "message",
        journal.get("sent_identity", uuid.uuid5(uuid.UUID(draft.identity), "sent").hex),
    )
    return path if os.path.exists(path) else store.path(saved["path"])
