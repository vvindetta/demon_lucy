"""Upgrade generated email drop folders without removing user files."""

from __future__ import annotations

import errno
import logging
import os
import shlex

from demon_lucy.lib.logfmt import log_record
from demon_lucy.modules.email.config import MAILBOX_ACTION_FOLDERS
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
_NEW_ACTION_HELP = """`Drafts/`, then drop that draft into `Actions/Send/`. Use `Actions/Mark read/` and
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
