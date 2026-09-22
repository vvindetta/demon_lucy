"""Upgrade generated email drop folders without removing user files."""

from __future__ import annotations

import errno
import logging
import os
import shlex
import hashlib
import uuid

from demon_lucy.lib.logfmt import log_record
from demon_lucy.modules.email.config import MAILBOX_ACTION_FOLDERS, DROP_ACTION_FOLDERS
from demon_lucy.modules.email.documents import LITERAL_MARKER
from demon_lucy.modules.email.storage import MailStore

logger = logging.getLogger(__name__)

REFRESH_TEXT = (
    LITERAL_MARKER
    + "\nMove this file to a watched folder to refresh mail. It returns here automatically.\n"
)

_OLD_REFRESH_TEXT = (
    LITERAL_MARKER + "\nDrop this file into Actions/Refresh/ to fetch mail.\n"
)
_OLD_ACTION_HELP = """`Drafts/`, then drop that draft into `Actions/Send/`. Other actions are `Mark read`,
`Mark unread`, `Archive` and `Trash`; they update both local files and the server.
"""
_NEW_ACTION_HELP = """`Drafts/`, then drop that draft into `Sent/`. Use `Actions/Mark read/` and
`Actions/Mark unread/` to change read state. Move messages directly into `Archive/`
or `Trash/` to move them on the server and locally.
"""
_OLD_REFRESH_HELP = (
    "Drop `refresh.md` into `Actions/Refresh/` for an immediate fetch.\n"
)
_NEW_REFRESH_HELP = """Move `refresh.md` to any folder watched by Lucy for an immediate fetch. It returns
to the account root automatically. Hidden paths and moves reported only as a
deletion outside the watched tree cannot trigger refresh.
"""


def upgrade_layout(store: MailStore, *, event_id: str = "") -> None:
    """Call with the account lock held; change only recognized generated files."""
    for folder, flag in (
        ("Send", "email-send"),
        ("Archive", "email-archive"),
        ("Trash", "email-trash"),
        ("Refresh", "email-fetch"),
    ):
        command = shlex.join(["--email-root", store.root, "--" + flag])
        expected = shlex.join(["--dropdir-init", command]) + "\n"
        if folder in MAILBOX_ACTION_FOLDERS:
            target = f"{folder}/init.md"
            target_path = store.path(target)
            if os.path.exists(target_path):
                if store.read_text(target, 1024 * 1024) == expected:
                    os.unlink(target_path)
                    store.changed[target_path] = store.changed.get(target_path, 0) + 1
                else:
                    logger.warning(
                        log_record(
                            "email.layout_preserved",
                            id=event_id,
                            path=target_path,
                            reason="custom_action",
                        )
                    )
        legacy = f"Actions/{folder}"
        directory = store.path(legacy)
        if not os.path.isdir(directory):
            continue
        instruction = f"{legacy}/init.md"
        old_path = store.path(instruction)
        if os.path.exists(old_path):
            if store.read_text(instruction, 1024 * 1024) != expected:
                logger.warning(
                    log_record(
                        "email.layout_preserved",
                        id=event_id,
                        path=old_path,
                        reason="custom_action",
                    )
                )
                continue
            os.unlink(old_path)
            store.changed[old_path] = store.changed.get(old_path, 0) + 1
        try:
            os.rmdir(directory)
        except OSError as error:
            if error.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
                raise
            logger.info(
                log_record(
                    "email.layout_preserved",
                    id=event_id,
                    path=directory,
                    reason="user_files",
                )
            )
        else:
            logger.info(log_record("email.layout_removed", id=event_id, path=directory))

    refresh = store.path("refresh.md")
    if os.path.exists(refresh):
        before = store.read_text("refresh.md", 1024 * 1024)
        if before == _OLD_REFRESH_TEXT:
            store.write_text("refresh.md", REFRESH_TEXT, expected=before)
    welcome = store.path("welcome.md")
    if os.path.exists(welcome):
        before = store.read_text("welcome.md", 1024 * 1024)
        updated = before.replace(_OLD_ACTION_HELP, _NEW_ACTION_HELP).replace(
            _OLD_REFRESH_HELP, _NEW_REFRESH_HELP
        )
        if updated != before:
            store.write_text("welcome.md", updated, expected=before)

    _upgrade_runner(store)
    _upgrade_recovery(store)
    _upgrade_drafts(store)


def _upgrade_runner(store: MailStore) -> None:
    from demon_lucy.modules.email.scaffold import welcome_text

    for folder, action in DROP_ACTION_FOLDERS.items():
        relative = folder + "/init.md"
        path = store.path(relative)
        expected = (
            shlex.join(
                [
                    "--dropdir-init",
                    shlex.join(["--email-root", store.root, "--" + action]),
                ]
            )
            + "\n"
        )
        if os.path.exists(path) and store.read_text(relative, 1024 * 1024) == expected:
            os.unlink(path)
            store.changed[path] = 1
    unit = "lucy-email-" + hashlib.sha256(store.root.encode()).hexdigest()[:12]
    for suffix, description in (
        ("watcher.service", "Lucy email action watcher"),
        ("fetch.service", "Fetch Lucy email"),
        ("fetch.timer", "Fetch Lucy email every five minutes"),
    ):
        relative = f"setup-systemd/{unit}-{suffix}"
        path = store.path(relative)
        if os.path.exists(path):
            content = store.read_text(relative, 1024 * 1024)
            if content.startswith(f"[Unit]\nDescription={description}\n"):
                store.write_text(f".email/recovery/setup/{unit}-{suffix}", content)
                os.unlink(path)
                store.changed[path] = 1
    directory = store.path("setup-systemd")
    if os.path.isdir(directory) and not os.listdir(directory):
        os.rmdir(directory)
        store.changed[directory] = 1
    watcher = store.path(".email/.watcher.conf")
    expected = (
        "--sys-modules dropdir email\n"
        + shlex.join(["--sys-watch-paths", store.root])
        + "\n--sys-log-level info\n--sys-disable-opened-events\n"
    )
    if (
        os.path.exists(watcher)
        and store.read_text(".email/.watcher.conf", 1024 * 1024) == expected
    ):
        os.unlink(watcher)
        store.changed[watcher] = 1
    welcome = store.path("welcome.md")
    if os.path.exists(welcome):
        text = store.read_text("welcome.md", 1024 * 1024)
        if (
            text.startswith(LITERAL_MARKER + "\n# Lucy email\n")
            and "generated watcher" in text
        ):
            store.write_text(".email/recovery/welcome-before-upgrade.md", text)
            store.write_text("welcome.md", welcome_text(store.root), expected=text)


def _upgrade_recovery(store: MailStore) -> None:
    old = store.path("Local-only")
    if not os.path.isdir(old):
        return
    store.ensure_directory(".email/recovery")
    relative = ".email/recovery/local-" + uuid.uuid4().hex
    # Whole-directory rename preserves arbitrary user files, nested folders and names.
    if os.listdir(old):
        os.rename(old, store.path(relative))
        for kind in ("message", "draft"):
            for key, value in store.items(kind):
                if value.get("path", "").startswith("Local-only/"):
                    value["path"] = relative + value["path"][len("Local-only") :]
                    if kind == "message":
                        value["folder"] = ".email/recovery"
                    store.put(kind, key, value)
    else:
        os.rmdir(old)
    store.changed[old] = 1


def _upgrade_drafts(store: MailStore) -> None:
    from demon_lucy.modules.email.codec import parse_draft, render_draft
    from demon_lucy.modules.email.errors import EmailError

    for identity, binding in store.items("draft"):
        path = store.path(binding["path"])
        # Keep bytes referenced by an active delivery journal unchanged.
        if not os.path.isfile(path) or store.get("send", identity) is not None:
            continue
        before = store.read_text(binding["path"], 128 * 1024 * 1024)
        if not before.startswith(LITERAL_MARKER):
            continue
        try:
            draft = parse_draft(before)
        except EmailError:
            continue  # An editor may currently have an incomplete draft.
        if draft.identity == identity:
            store.write_text(binding["path"], render_draft(draft), expected=before)
