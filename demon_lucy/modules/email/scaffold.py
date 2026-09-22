from __future__ import annotations

import os
import shlex
import uuid

from demon_lucy.lib.args.models import ParsedArgs
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.modules.email.codec import new_draft
from demon_lucy.modules.email.config import (
    ACCOUNT_FILE,
    ACTION_FOLDERS,
    SETTINGS_TEMPLATE,
    account_from_args,
)
from demon_lucy.modules.email.documents import LITERAL_MARKER
from demon_lucy.modules.email.files import locked_file, safe_path, write_text_if_missing
from demon_lucy.modules.email.models import CredentialProvider
from demon_lucy.modules.email.layout import REFRESH_TEXT, upgrade_layout
from demon_lucy.modules.email.storage import MailStore

# Former generated files are recognized only for migration.
WATCHER_FILE = ".email/.watcher.conf"
CREDENTIALS_FILE = ".email/.credentials.env"
FOLDERS = (
    "Inbox",
    "Drafts",
    "Sent",
    "Archive",
    "Trash",
    "Actions",
    ".email/raw",
    ".email/attachments",
    ".email/outgoing",
    ".email/recovery",
    *ACTION_FOLDERS,
)


def _account_text(args: ParsedArgs) -> tuple[str, CredentialProvider, tuple[str, ...]]:
    settings = parse_args([], SETTINGS_TEMPLATE).merged_with(args)
    account = account_from_args(".", settings)
    lines = ["# Lucy email account settings. Passwords are stored separately."]
    for setting in SETTINGS_TEMPLATE:
        value = str(settings.require(setting.name).value)
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Email settings cannot contain control characters")
        lines.append(shlex.join([f"--{setting.name}={value}"]))
    variables = tuple(
        dict.fromkeys((account.imap.password_env, account.smtp.password_env))
    )
    return "\n".join(lines) + "\n", account.imap.credential_provider, variables


def welcome_text(root: str) -> str:
    return f"""{LITERAL_MARKER}
# Lucy email

Edit `.email/.account.conf` to configure your sender address and IMAP/SMTP account.
Passwords use your Linux desktop's Secret Service or KWallet. Save them from a terminal:

```sh
python3 main_oneshot.py --sys-modules email --email-root {shlex.quote(root)} --email-credentials-save
```

For an explicitly selected environment credential provider, set the password variables
in the existing Lucy service's environment. Passwords do not belong in email files.

## Run with Lucy

Add `email` to your existing `--sys-modules` list and add these settings to Lucy's config:

```text
{shlex.join(["--email-root", root])}
{shlex.join(["--sys-ignore-paths", root])}
--email-fetch-interval-seconds 300
```

Restart your existing Lucy daemon. Email runs inside that process. The account is
excluded from ordinary note actions; the email module observes its own files.
It refreshes every five minutes and restores missing folders and control files.
Moving a standard folder or control file within the observed tree returns it home.
Keep the account's parent inside `--sys-watch-paths` to observe moves outside it.

## Read, compose and act

Read mail in `Inbox/`, `Sent/`, `Archive/` and `Trash/`.
Edit `new email.md`: fill in the YAML `to`, `subject`, optional `cc`/`bcc`, and
`attachments` (a list of file paths). Write the body after the closing `---`.
Keep the `email` and `id` fields. Attachment paths are relative to the draft.

Move a draft into `Sent/` to send it. Lucy returns the draft before sending and
restores a blank starter after success. Saving, importing, or opening a file never
sends mail. Existing messages in `Sent/` are history and are never resent.

Move a message into `Actions/Reply/` to create a reply in `Drafts/`.
Use `Actions/Mark read/` and `Actions/Mark unread/` to change read state.
Move messages directly into `Archive/` or `Trash/` to move them on the server.
Trash does not permanently delete mail. Move `refresh.md` anywhere observed by
Lucy to fetch immediately; it returns automatically.

Raw messages, attachments, delivery journals and recovered local edits are stored
in `.email/`. Recovery copies are in `.email/recovery/`.

## Delivery recovery

Check `status.md` after a failed send. An uncertain or partially accepted delivery
blocks that draft generation from resending. Verify delivery with your provider
before composing a new draft for any remaining recipients. Refresh retries a
pending Sent copy without sending another message.

Structure repair preserves existing content. It cannot recover deleted mail bytes
or a deleted private database; restore `.email/` from backup in that case.
"""


def repair_layout(store: MailStore, args: ParsedArgs) -> None:
    for folder in FOLDERS:
        path = store.path(folder)
        if not os.path.isdir(path):
            store.ensure_directory(folder)
            store.changed[path] = 1
    saved = store.get("layout", "account")
    if not os.path.exists(store.path(ACCOUNT_FILE)):
        text = saved["text"] if saved else _account_text(args)[0]
        store.write_text(ACCOUNT_FILE, text)
    text = store.read_text(ACCOUNT_FILE, 1024 * 1024)
    if saved is None or saved["text"] != text:
        store.put("layout", "account", {"text": text})
    for relative, text in {
        ".gitignore": ".email/\n",
        ".email/.gitignore": "*\n",
        "refresh.md": REFRESH_TEXT,
        "welcome.md": welcome_text(store.root),
        "status.md": LITERAL_MARKER + "\n# Email status\n\nNo action has run yet.\n",
    }.items():
        if write_text_if_missing(store.path(relative), text):
            store.changed[store.path(relative)] = 1
    if not os.path.exists(store.path("new email.md")):
        store.create_draft(new_draft(uuid.uuid4().hex), "new email.md")


def initialize(root: str, args: ParsedArgs, *, event_id: str = "") -> dict[str, int]:
    root = os.path.abspath(os.path.expanduser(root))
    _account_text(args)  # Validate settings before creating files.
    os.makedirs(safe_path(root, ".email"), mode=0o700, exist_ok=True)
    with locked_file(safe_path(root, ".email/.lock")), MailStore(root) as store:
        upgrade_layout(store, event_id=event_id)
        repair_layout(store, args)
        return dict(store.changed)
