"""Repair the account's own structure without interpreting email body text."""

from __future__ import annotations

import errno
import logging
import os
from pathlib import Path

from demon_lucy.lib.args.models import ParsedArgs
from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.path import path_is_inside
from demon_lucy.modules.email.config import ACCOUNT_FILE, ACTION_FOLDERS
from demon_lucy.modules.email.documents import document_identity
from demon_lucy.modules.email.errors import EmailError
from demon_lucy.modules.email.files import (
    locked_file,
    read_bytes_no_follow,
    safe_path,
    write_text_if_missing,
)
from demon_lucy.modules.email.scaffold import FOLDERS, repair_layout
from demon_lucy.modules.email.storage import MailStore

logger = logging.getLogger(__name__)


class Structure:
    def __init__(self, root: str, args: ParsedArgs):
        self.root = root
        self.args = args
        self.directories = {
            root,
            safe_path(root, ".email"),
            *(safe_path(root, folder) for folder in FOLDERS),
        }
        self.files = {
            safe_path(root, relative)
            for relative in (
                ACCOUNT_FILE,
                ".email/.state.sqlite3",
                ".email/.lock",
                ".email/.gitignore",
                ".gitignore",
                "welcome.md",
                "status.md",
                "new email.md",
            )
        }

    def restore_move(
        self, source: str, destination: str, *, event_id: str = ""
    ) -> bool:
        if source not in self.directories | self.files and not self._misplaced_message(
            source, destination
        ):
            return False
        if source == safe_path(self.root, "new email.md") and os.path.dirname(
            destination
        ) == safe_path(self.root, "Sent"):
            return False
        # An explicit move of a standard structure item is reversible. Never
        # overwrite a concurrent edit or follow a relocated symlink.
        if source == destination or not os.path.lexists(destination):
            return True
        safe_path(str(Path(destination).parent), Path(destination).name)
        safe_path(str(Path(source).parent), Path(source).name)
        if source in self.directories:
            if not os.path.isdir(destination):
                raise EmailError(
                    "A moved email folder changed type; both paths were preserved.",
                    reason="structure_conflict",
                )
            if os.path.isdir(source) and not os.listdir(source):
                os.rmdir(source)
            if os.path.lexists(source):
                raise EmailError(
                    "The email folder's original path is occupied; both folders were preserved.",
                    reason="structure_conflict",
                )
            os.rename(destination, source)
        else:
            content = read_bytes_no_follow(destination, 128 * 1024 * 1024)
            if os.path.lexists(source):
                raise EmailError(
                    "The email file's original path is occupied; both files were preserved.",
                    reason="structure_conflict",
                )
            try:
                os.link(destination, source, follow_symlinks=False)
            except OSError as error:
                if error.errno != errno.EXDEV:
                    raise
                if not write_text_if_missing(source, content.decode("utf-8")):
                    raise EmailError(
                        "The email file's original path is occupied.",
                        reason="structure_conflict",
                    ) from None
            if read_bytes_no_follow(destination, 128 * 1024 * 1024) != content:
                raise EmailError(
                    "The moved email file changed while returning; both files were preserved.",
                    reason="structure_conflict",
                )
            os.unlink(destination)
        logger.info(
            log_record(
                "email.structure_restored", id=event_id, src=destination, dest=source
            )
        )
        return True

    def _misplaced_message(self, source: str, destination: str) -> bool:
        if (
            not path_is_inside(source, self.root)
            or path_is_inside(destination, safe_path(self.root, ".email"))
            or not os.path.isfile(destination)
        ):
            return False
        if os.path.dirname(source) != os.path.dirname(destination) and os.path.dirname(
            destination
        ) in {safe_path(self.root, folder) for folder in ACTION_FOLDERS}:
            return False  # The module restores and executes deliberate folder actions.
        if not os.path.isfile(safe_path(self.root, ".email/.state.sqlite3")):
            return False
        relative = os.path.relpath(source, self.root)
        safe_path(self.root, relative)
        text = read_bytes_no_follow(destination, 128 * 1024 * 1024).decode("utf-8")
        identity = document_identity(text)
        if identity is None:
            return False
        with locked_file(
            safe_path(self.root, ".email/.lock"), blocking=True
        ), MailStore(self.root) as store:
            return any(
                binding is not None and binding["path"] == relative
                for binding in (
                    store.get("message", identity),
                    store.get("draft", identity),
                )
            )

    def repair(self) -> dict[str, int]:
        # Do not silently create a fresh database: that would lose uncertain
        # delivery generations and could allow an already delivered draft to send again.
        if not os.path.isfile(safe_path(self.root, ".email/.state.sqlite3")):
            raise EmailError(
                "Email's private database is missing. Restore .email/ from its moved location or backup.",
                reason="state_missing",
            )
        with locked_file(safe_path(self.root, ".email/.lock")), MailStore(
            self.root
        ) as store:
            repair_layout(store, self.args)
            changed = dict(store.changed)
        if changed:
            logger.info(
                log_record(
                    "email.structure_repaired",
                    account=self.root,
                    changed_paths=len(changed),
                )
            )
        return changed
