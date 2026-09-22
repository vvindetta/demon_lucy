from __future__ import annotations

import os
import re

from demon_lucy.lib.args.models import KnownArg, ParsedArgs, Template
from demon_lucy.lib.args.sources import _parse_config_args
from demon_lucy.modules.email.errors import EmailError
from demon_lucy.modules.email.files import safe_path
from demon_lucy.modules.email.models import (
    AccountConfig,
    CredentialProvider,
    Security,
    SentCopyMode,
    ServerSettings,
)

ACCOUNT_FILE = ".email/.account.conf"
ACTION_NAMES = (
    "email-fetch",
    "email-reply",
    "email-send",
    "email-mark-read",
    "email-mark-unread",
    "email-archive",
    "email-trash",
)
DROP_ACTION_FOLDERS = {
    "Actions/Reply": "email-reply",
    "Actions/Mark read": "email-mark-read",
    "Actions/Mark unread": "email-mark-unread",
}
MAILBOX_ACTION_FOLDERS = {
    "Sent": "email-send",
    "Archive": "email-archive",
    "Trash": "email-trash",
}
ACTION_FOLDERS = {**DROP_ACTION_FOLDERS, **MAILBOX_ACTION_FOLDERS}

SETTINGS_TEMPLATE: Template = [
    KnownArg(
        name="email-credential-provider",
        value_type=CredentialProvider,
        default=CredentialProvider.KEYRING,
        description="keyring stores passwords in the desktop credential store; environment explicitly reads password environment variables.",
    ),
    KnownArg(
        name="email-from-address",
        value_type=str,
        default="",
        description="Sender mailbox address for this email account.",
    ),
    KnownArg(
        name="email-imap-host",
        value_type=str,
        default="",
        description="IMAP server hostname.",
    ),
    KnownArg(
        name="email-imap-port",
        value_type=int,
        default=993,
        description="IMAP port; default 993 for TLS. Use 143 with STARTTLS.",
    ),
    KnownArg(
        name="email-imap-security",
        value_type=Security,
        default=Security.TLS,
        description="IMAP encryption: tls or starttls; certificates are verified.",
    ),
    KnownArg(
        name="email-imap-username",
        value_type=str,
        default="",
        description="IMAP login username.",
    ),
    KnownArg(
        name="email-imap-password-env",
        value_type=str,
        default="LUCY_EMAIL_PASSWORD",
        description="Environment variable containing the IMAP password or app password.",
    ),
    KnownArg(
        name="email-smtp-host",
        value_type=str,
        default="",
        description="SMTP server hostname.",
    ),
    KnownArg(
        name="email-smtp-port",
        value_type=int,
        default=587,
        description="SMTP port; default 587 for STARTTLS. Use 465 with TLS.",
    ),
    KnownArg(
        name="email-smtp-security",
        value_type=Security,
        default=Security.STARTTLS,
        description="SMTP encryption: starttls or tls; certificates are verified.",
    ),
    KnownArg(
        name="email-smtp-username",
        value_type=str,
        default="",
        description="SMTP login username.",
    ),
    KnownArg(
        name="email-smtp-password-env",
        value_type=str,
        default="LUCY_EMAIL_PASSWORD",
        description="Environment variable containing the SMTP password or app password.",
    ),
    KnownArg(
        name="email-inbox-mailbox",
        value_type=str,
        default="INBOX",
        description="Remote Inbox mailbox name.",
    ),
    KnownArg(
        name="email-sent-mailbox",
        value_type=str,
        default="",
        description="Remote Sent mailbox; empty discovers the unique special-use folder.",
    ),
    KnownArg(
        name="email-archive-mailbox",
        value_type=str,
        default="",
        description="Remote Archive mailbox; empty discovers the unique special-use folder.",
    ),
    KnownArg(
        name="email-trash-mailbox",
        value_type=str,
        default="",
        description="Remote Trash mailbox; empty discovers the unique special-use folder.",
    ),
    KnownArg(
        name="email-sent-copy-mode",
        value_type=SentCopyMode,
        default=SentCopyMode.APPEND,
        description="append: Lucy saves sent mail via IMAP; server: SMTP provider saves it.",
    ),
    KnownArg(
        name="email-initial-message-limit",
        value_type=int,
        default=100,
        description="Newest messages imported per folder on first fetch; 0 imports all history.",
    ),
    KnownArg(
        name="email-timeout-seconds",
        value_type=int,
        default=30,
        description="Email network operation timeout in seconds.",
    ),
    KnownArg(
        name="email-max-message-bytes",
        value_type=int,
        default=25 * 1024 * 1024,
        description="Maximum received or outgoing MIME message size; default 25 MiB.",
    ),
]

TEMPLATE: Template = [
    KnownArg(
        name="email-fetch-interval-seconds",
        value_type=int,
        default=300,
        description="Fetch interval inside Lucy; 0 disables automatic fetches.",
    ),
    KnownArg(
        name="email-repair-interval-seconds",
        value_type=int,
        default=2,
        description="Check and restore missing email folders and control files at this interval.",
    ),
    KnownArg(
        name="email-credentials-save",
        value_type=bool,
        default=False,
        description="Prompt privately in a terminal and save IMAP/SMTP passwords in the Linux desktop credential store for --email-root.",
    ),
    KnownArg(
        name="email-init",
        value_type=str,
        default="",
        description="Create an email directory with a blank composer, action folders, and account setup.",
    ),
    KnownArg(
        name="email-root",
        value_type=str,
        default="",
        description="Initialized email account directory for an email action.",
    ),
    KnownArg(
        name="email-fetch",
        value_type=bool,
        default=False,
        description="Fetch mail from the CLI or by moving the account's refresh.md file.",
    ),
    KnownArg(
        name="email-reply",
        value_type=bool,
        default=False,
        description="Create a reply draft for a message through its Reply drop folder.",
    ),
    KnownArg(
        name="email-send",
        value_type=bool,
        default=False,
        description="Send a draft by moving it into Sent/; never triggered by an ordinary save.",
    ),
    KnownArg(
        name="email-mark-read",
        value_type=bool,
        default=False,
        description="Mark a managed message read on the server and locally.",
    ),
    KnownArg(
        name="email-mark-unread",
        value_type=bool,
        default=False,
        description="Mark a managed message unread on the server and locally.",
    ),
    KnownArg(
        name="email-archive",
        value_type=bool,
        default=False,
        description="Move a managed message to the remote Archive mailbox.",
    ),
    KnownArg(
        name="email-trash",
        value_type=bool,
        default=False,
        description="Move a managed message to the remote Trash mailbox without permanent deletion.",
    ),
    *SETTINGS_TEMPLATE,
]


def load_account(root: str) -> AccountConfig:
    path = safe_path(root, ACCOUNT_FILE)
    args = _parse_config_args(path, SETTINGS_TEMPLATE)
    if args.unknown:
        raise EmailError(
            "Unknown setting in the email account file.", reason="invalid_config"
        )
    return account_from_args(root, args)


def account_from_args(root: str, args: ParsedArgs) -> AccountConfig:
    def value(name: str):
        return args.require("email-" + name).value

    for name in ("initial-message-limit", "timeout-seconds", "max-message-bytes"):
        number = value(name)
        if number < 0 or (name != "initial-message-limit" and number == 0):
            raise EmailError(f"Invalid --email-{name}.", reason="invalid_config")

    def server(protocol: str) -> ServerSettings:
        host = value(protocol + "-host").strip()
        username = value(protocol + "-username").strip()
        variable = value(protocol + "-password-env").strip()
        if not 1 <= value(protocol + "-port") <= 65535:
            raise EmailError(
                f"Invalid {protocol.upper()} port.", reason="invalid_config"
            )
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable):
            raise EmailError(
                f"Invalid {protocol.upper()} password environment variable name.",
                reason="invalid_config",
            )
        for text in (host, username):
            if any(ord(character) < 32 or ord(character) == 127 for character in text):
                raise EmailError(
                    "Control characters in account settings.", reason="invalid_config"
                )
        return ServerSettings(
            host,
            value(protocol + "-port"),
            value(protocol + "-security"),
            username,
            variable,
            value("timeout-seconds"),
            value("credential-provider"),
        )

    mailboxes = {
        name: value(name.lower() + "-mailbox").strip()
        for name in ("Inbox", "Sent", "Archive", "Trash")
    }
    if any(
        any(ord(c) < 32 or ord(c) == 127 for c in name) for name in mailboxes.values()
    ):
        raise EmailError(
            "Control characters in mailbox names.", reason="invalid_config"
        )
    return AccountConfig(
        os.path.abspath(root),
        value("from-address").strip(),
        server("imap"),
        server("smtp"),
        mailboxes,
        value("initial-message-limit"),
        value("max-message-bytes"),
        value("sent-copy-mode"),
    )
