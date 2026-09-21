from __future__ import annotations

import html
import logging
import os
import sqlite3
import uuid
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path

from demon_lucy.lib.args.line_edit import delete_args_from_string
from demon_lucy.lib.args.models import ArgSource
from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.notifications import safe_notify
from demon_lucy.lib.text_file import write_text_atomic
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    ModuleResult,
    System,
)
from demon_lucy.modules.email.codec import reply_draft
from demon_lucy.modules.email.config import (
    ACTION_FOLDERS,
    ACTION_NAMES,
    TEMPLATE,
    load_account,
)
from demon_lucy.modules.email.errors import EmailError
from demon_lucy.modules.email.documents import LITERAL_MARKER, is_literal_document
from demon_lucy.modules.email.files import (
    FileBusyError,
    locked_file,
    read_bytes_no_follow,
    safe_path,
)
from demon_lucy.modules.email.models import AccountConfig
from demon_lucy.modules.email.scaffold import initialize
from demon_lucy.modules.email.sending import recover_sent, send_draft
from demon_lucy.modules.email.storage import MailStore
from demon_lucy.modules.email.sync import change_message, decoded_record, fetch_mail

logger = logging.getLogger(__name__)


def _root(value: str, ctx: Context) -> str:
    if not value.strip():
        raise EmailError(
            "Set --email-root to an initialized account directory.",
            reason="root_missing",
        )
    base = ctx.path if os.path.isdir(ctx.path) else os.path.dirname(ctx.path)
    path = os.path.expanduser(value)
    root = os.path.abspath(path if os.path.isabs(path) else os.path.join(base, path))
    if any(part.is_symlink() for part in (Path(root), *Path(root).parents)):
        raise EmailError(
            "Email directories must not use symlinks.", reason="unsafe_path"
        )
    return root


def _network_settings(config: AccountConfig, action: str) -> None:
    if action == "email-reply":
        return
    for name, settings in (("IMAP", config.imap), ("SMTP", config.smtp)):
        if name == "SMTP" and action not in {"email-send", "email-credentials-save"}:
            continue
        if not settings.host or not settings.username:
            raise EmailError(
                f"Set the {name} host and username in .email/.account.conf.",
                reason="invalid_config",
            )


def _status(store: MailStore, action: str, error: EmailError | None) -> None:
    lines = [
        LITERAL_MARKER,
        "# Email status",
        "",
        f"Last action: {action}",
        "",
        html.escape(str(error)) if error else "Last action completed.",
        "",
    ]
    for generation, journal in store.items("send"):
        lines.extend(
            [
                f"## Draft {generation}",
                f"File: {html.escape(journal['draft_path'])}",
                f"Delivery: {journal['state']}",
                f"Accepted: {html.escape(', '.join(journal.get('accepted', []))) or '(none confirmed)'}",
                f"Refused: {html.escape(', '.join(journal.get('refused', []))) or '(none)'}",
                f"Server Sent copy: {'saved or managed by provider' if journal.get('copy_done') else 'pending'}",
                "",
            ]
        )
    for generation, movement in store.items("move"):
        if movement["state"] != "complete":
            lines.extend(
                [
                    f"Unresolved move: {generation}. Refresh and check the server before trying again.",
                    "",
                ]
            )
    path = store.path("status.md")
    old = (
        store.read_text("status.md", 16 * 1024 * 1024) if os.path.exists(path) else None
    )
    store.write_text("status.md", "\n".join(lines) + "\n", expected=old)


class Email(AbstractModule):
    name = "email"
    priority = 35
    template = TEMPLATE

    def __init__(self) -> None:
        self._handled_drops: OrderedDict[tuple[str, str, str, str], None] = (
            OrderedDict()
        )

    def _report_error(self, ctx: Context, root: str, error: EmailError) -> None:
        log = logger.warning if error.retryable else logger.error
        log(
            log_record(
                "email.action_failed",
                id=ctx.event_id,
                path=ctx.path,
                account=root,
                reason=error.reason,
                error=str(error),
            )
        )
        if not error.retryable:
            safe_notify(f"email:{root}", str(error), args=ctx.args, use_rare_mode=True)

    def _initialize(self, ctx: Context) -> ModuleResult | None:
        argument = ctx.args.require("email-init")
        if not argument.value or argument.source not in {ArgSource.CLI, ArgSource.FILE}:
            return None
        root = ""
        try:
            root = _root(argument.value, ctx)
            changed = initialize(root, ctx.args)
            if argument.source is ArgSource.FILE:
                text = read_bytes_no_follow(ctx.path, 128 * 1024 * 1024).decode("utf-8")
                lines = text.splitlines(keepends=True)
                for line in argument.lines:
                    if 0 < line <= len(lines):
                        cleaned = delete_args_from_string(
                            lines[line - 1], ["--email-init"]
                        )
                        lines[line - 1] = (
                            cleaned
                            if cleaned.strip()
                            else f"Email directory ready: {root}\n"
                        )
                updated = "".join(lines)
                if updated != text:
                    write_text_atomic(ctx.path, updated, expected_text=text)
                    changed[ctx.path] = changed.get(ctx.path, 0) + 1
            logger.info(log_record("email.initialized", id=ctx.event_id, path=root))
            return ModuleResult(context=ctx, changed=changed)
        except FileBusyError:
            logger.info(
                log_record(
                    "email.skip", id=ctx.event_id, path=root, reason="account_busy"
                )
            )
        except (EmailError, OSError, ValueError, sqlite3.Error) as exc:
            error = (
                exc
                if isinstance(exc, EmailError)
                else EmailError(
                    "Could not initialize the email directory. Check its paths and permissions.",
                    reason="initialization_failed",
                )
            )
            self._report_error(ctx, root, error)
        return None

    def _execute(
        self, ctx: Context, action: str, *, dropped: bool
    ) -> ModuleResult | None:
        root = ""
        store: MailStore | None = None
        final_path = ctx.path
        try:
            root = _root(ctx.args.require("email-root").value, ctx)
            if dropped:
                folder = next(
                    folder for folder, flag in ACTION_FOLDERS.items() if flag == action
                )
                destination = os.path.abspath(
                    os.fsdecode(getattr(ctx.event, "dest_path", ""))
                )
                source = os.path.abspath(os.fsdecode(ctx.event.src_path))
                if (
                    os.path.dirname(destination) != safe_path(root, f"Actions/{folder}")
                    or os.path.abspath(ctx.path) != source
                    or os.path.lexists(destination)
                ):
                    raise EmailError(
                        "The drop must return to its original file before an email action can run.",
                        reason="invalid_drop",
                    )
                safe_path(root, os.path.relpath(source, root))
                if not os.path.isfile(source):
                    raise EmailError(
                        "The dropped source file is unavailable.", reason="invalid_drop"
                    )
            config = load_account(root)
            _network_settings(config, action)
            lock = safe_path(root, ".email/.lock")
            with locked_file(lock), MailStore(root) as store:
                error: EmailError | None = None
                try:
                    if action == "email-credentials-save":
                        from demon_lucy.modules.email.credentials import (
                            save_credentials,
                        )

                        store.bind_account(config)
                        save_credentials(config)
                    elif action == "email-fetch":
                        recover_sent(config, store, event_id=ctx.event_id)
                        fetch_mail(config, store, event_id=ctx.event_id)
                    elif action == "email-reply":
                        record = store.record_for_path(ctx.path)
                        draft = reply_draft(
                            uuid.uuid4().hex, decoded_record(store, record)
                        )
                        relative = f"Drafts/Reply [{draft.identity}].md"
                        store.create_draft(draft, relative)
                        logger.info(
                            log_record(
                                "email.reply_created",
                                id=ctx.event_id,
                                path=store.path(relative),
                            )
                        )
                    elif action == "email-send":
                        final_path = send_draft(
                            config, store, ctx.path, event_id=ctx.event_id
                        )
                    else:
                        final_path = change_message(
                            config, store, ctx.path, action, event_id=ctx.event_id
                        )
                except EmailError as exc:
                    error = exc
                except (OSError, ValueError, sqlite3.Error) as exc:
                    error = EmailError(
                        "Email files could not be read or saved. The delivery journal is retained; check status.md before sending again.",
                        reason="storage_failed",
                    )
                    logger.debug(
                        log_record(
                            "email.storage_detail",
                            id=ctx.event_id,
                            error=type(exc).__name__,
                        )
                    )
                _status(store, action, error)
                if error:
                    self._report_error(ctx, root, error)
                else:
                    logger.info(
                        log_record(
                            "email.action_done",
                            id=ctx.event_id,
                            command=action,
                            path=final_path,
                        )
                    )
                return ModuleResult(
                    context=replace(ctx, path=final_path), changed=store.changed
                )
        except FileBusyError:
            logger.info(
                log_record(
                    "email.skip", id=ctx.event_id, path=root, reason="account_busy"
                )
            )
        except (EmailError, OSError, ValueError, sqlite3.Error) as exc:
            error = (
                exc
                if isinstance(exc, EmailError)
                else EmailError(
                    "The email account cannot be loaded. Check its configuration and paths.",
                    reason="account_unavailable",
                )
            )
            self._report_error(ctx, root, error)
        return ModuleResult(context=ctx, changed=store.changed) if store else None

    def created(self, ctx: Context, system: System) -> ModuleResult | None:
        return None if is_literal_document(ctx.path) else self._initialize(ctx)

    def modified(self, ctx: Context, system: System) -> ModuleResult | None:
        return None if is_literal_document(ctx.path) else self._initialize(ctx)

    def moved(self, ctx: Context, system: System) -> ModuleResult | None:
        actions = [
            name
            for name in ACTION_NAMES
            if ctx.args.require(name).source is ArgSource.CLI
            and ctx.args.require(name).value
        ]
        if not actions:
            return None
        if (
            len(actions) != 1
            or ctx.args.require("email-root").source is not ArgSource.CLI
            or ctx.event is None
            or ctx.event.is_directory
        ):
            self._report_error(
                ctx,
                ctx.args.require("email-root").value,
                EmailError(
                    "An email drop rule needs one action and its account root.",
                    reason="invalid_action",
                ),
            )
            return None
        key = (
            ctx.event_id,
            str(ctx.event.src_path),
            str(getattr(ctx.event, "dest_path", "")),
            actions[0],
        )
        if key in self._handled_drops:
            return None
        self._handled_drops[key] = None
        if len(self._handled_drops) > 2048:
            self._handled_drops.popitem(last=False)
        return self._execute(ctx, actions[0], dropped=True)

    def cli(self, ctx: Context, system: System) -> ModuleResult | None:
        credentials = ctx.args.require("email-credentials-save")
        if credentials.source is ArgSource.CLI and credentials.value:
            return self._execute(ctx, "email-credentials-save", dropped=False)
        if (
            ctx.args.require("email-init").source is ArgSource.CLI
            and ctx.args.require("email-init").value
        ):
            return self._initialize(ctx)
        actions = [
            name
            for name in ACTION_NAMES
            if ctx.args.require(name).source is ArgSource.CLI
            and ctx.args.require(name).value
        ]
        if not actions:
            return None
        if actions != ["email-fetch"]:
            self._report_error(
                ctx,
                ctx.args.require("email-root").value,
                EmailError(
                    "Use the email action folders for message actions; CLI supports --email-init, --email-credentials-save and --email-fetch.",
                    reason="invalid_action",
                ),
            )
            return None
        return self._execute(ctx, "email-fetch", dropped=False)
