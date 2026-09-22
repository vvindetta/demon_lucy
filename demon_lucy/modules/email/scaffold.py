from __future__ import annotations

import hashlib
import os
import shlex
import sys
import uuid
from pathlib import Path

from demon_lucy.lib.args.models import ParsedArgs
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.modules.email.codec import new_draft
from demon_lucy.modules.email.config import (
    ACCOUNT_FILE,
    ACTION_FOLDERS,
    DROP_ACTION_FOLDERS,
    SETTINGS_TEMPLATE,
    account_from_args,
    load_account,
)
from demon_lucy.modules.email.documents import LITERAL_MARKER
from demon_lucy.modules.email.files import locked_file, safe_path, write_text_if_missing
from demon_lucy.modules.email.models import CredentialProvider
from demon_lucy.modules.email.layout import REFRESH_TEXT, upgrade_layout
from demon_lucy.modules.email.storage import MailStore

WATCHER_FILE = ".email/.watcher.conf"
CREDENTIALS_FILE = ".email/.credentials.env"


def _validate_unit_value(value: str) -> None:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Email service paths cannot contain control characters")


def _unit_path(value: str) -> str:
    _validate_unit_value(value)
    return (
        value.replace("\\", "\\x5c")
        .replace(" ", "\\x20")
        .replace('"', "\\x22")
        .replace("'", "\\x27")
        .replace("%", "%%")
    )


def _unit_command(arguments: list[str]) -> str:
    quoted: list[str] = []
    for value in arguments:
        _validate_unit_value(value)
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
        escaped = escaped.replace("$", "$$")
        quoted.append(f'"{escaped}"')
    return " ".join(quoted)


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


def _setup_files(
    root: str, provider: CredentialProvider, variables: tuple[str, ...]
) -> dict[str, str]:
    lucy_home = str(Path(__file__).resolve().parents[3])
    watcher = [
        sys.executable,
        str(Path(lucy_home) / "main_daemon.py"),
        "--sys-config-path",
        str(Path(root) / WATCHER_FILE),
    ]
    fetch = [
        sys.executable,
        str(Path(lucy_home) / "main_oneshot.py"),
        "--sys-config-path",
        str(Path(root) / WATCHER_FILE),
        "--sys-modules",
        "email",
        "--email-root",
        root,
        "--email-fetch",
    ]
    unit = "lucy-email-" + hashlib.sha256(root.encode("utf-8")).hexdigest()[:12]
    environment = (
        f"EnvironmentFile={_unit_path(str(Path(root) / CREDENTIALS_FILE))}\n"
        if provider is CredentialProvider.ENVIRONMENT
        else ""
    )
    common = (
        f"WorkingDirectory={_unit_path(lucy_home)}\n"
        + environment
        + "Environment=PYTHONUNBUFFERED=1\n"
        "UMask=0077\n"
    )
    files = {
        f"setup-systemd/{unit}-watcher.service": (
            "[Unit]\nDescription=Lucy email action watcher\n\n"
            "[Service]\nType=exec\n" + common + f"ExecStart={_unit_command(watcher)}\n"
            "Restart=on-failure\nRestartSec=5\n\n"
            "[Install]\nWantedBy=default.target\n"
        ),
        f"setup-systemd/{unit}-fetch.service": (
            "[Unit]\nDescription=Fetch Lucy email\n\n"
            "[Service]\nType=oneshot\n" + common + f"ExecStart={_unit_command(fetch)}\n"
        ),
        f"setup-systemd/{unit}-fetch.timer": (
            "[Unit]\nDescription=Fetch Lucy email every five minutes\n\n"
            "[Timer]\nOnStartupSec=30s\nOnUnitActiveSec=5min\n"
            f"Unit={unit}-fetch.service\n\n"
            "[Install]\nWantedBy=timers.target\n"
        ),
    }
    if provider is CredentialProvider.KEYRING:
        install = shlex.join(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "-r",
                str(Path(lucy_home) / "demon_lucy/modules/email/requirements.txt"),
            ]
        )
        save = shlex.join(
            [
                sys.executable,
                str(Path(lucy_home) / "main_oneshot.py"),
                "--sys-config-path",
                str(Path(root) / WATCHER_FILE),
                "--sys-modules",
                "email",
                "--email-root",
                root,
                "--email-credentials-save",
            ]
        )
        credential_setup = f"""Passwords use your Linux desktop's Secret Service or KWallet store by default.
Install the email module's optional dependency, then save credentials from a terminal
after configuring the account hosts and usernames:

```sh
{install}
{save}
```

The password prompts hide your input. Passwords are not written into the email
directory. Run the watcher and fetch jobs as your logged-in desktop user, with
access to that session's D-Bus wallet. The wallet must be available and unlocked;
your desktop may prompt to unlock it. An unavailable wallet stops the request.
Lucy does not fall back to environment variables or plaintext files.
"""
    else:
        variable_names = ", ".join(f"`{variable}`" for variable in variables)
        credential_setup = f"""This account explicitly selects `--email-credential-provider environment`.
For terminal or scheduler runs, populate {variable_names} in the launch environment.
For the generated user services, edit `.email/.credentials.env` and uncomment the
needed assignments. This optional file holds plaintext secrets; keep it private
and outside Git. `.email/` is ignored. This mode supports headless systems and Termux.
"""
    link = shlex.join(
        ["systemctl", "--user", "link", *[str(Path(root) / name) for name in files]]
    )
    enable = shlex.join(
        [
            "systemctl",
            "--user",
            "enable",
            "--now",
            unit + "-watcher.service",
            unit + "-fetch.timer",
        ]
    )
    files["welcome.md"] = f"""{LITERAL_MARKER}
# Lucy email

This directory is one IMAP/SMTP account. Initialization preserves existing files.

## Account setup

Edit `.email/.account.conf`: set your sender address, IMAP/SMTP hosts and usernames.
Use a password or app password supported by your provider; OAuth is not supported.
{credential_setup}
IMAP defaults to TLS on port 993; SMTP defaults to STARTTLS on port 587.
Both verify certificates. For SMTP TLS, select `tls` and port 465.

Lucy discovers Sent, Archive and Trash from server special-use flags. If discovery
is ambiguous, set the mailbox names explicitly. Set `--email-sent-copy-mode server`
when your SMTP provider already saves Sent copies; the default `append` has Lucy
save them through IMAP. Each configured folder starts with its newest 100 messages.
The message-size limit is 25 MiB; oversized incoming mail gets a placeholder.

## Start and refresh

Use the generated watcher with only `dropdir` and `email` enabled. If your main
notes watcher also covers this account directory, add this to its own config:

```text
{shlex.join(["--sys-ignore-paths", root])}
```

The email markers identify messages and drafts; they do not disable Lucy's normal
note parser or other modules. Keep this account out of a general notes watcher.

Start the watcher from a terminal:

```sh
{shlex.join(watcher)}
```

In another terminal, fetch:

```sh
{shlex.join(fetch)}
```

Or install the generated user services after saving your credentials:

```sh
{link}
systemctl --user daemon-reload
{enable}
```

The watcher handles action folders; the timer fetches every five minutes. These
files are generated only: initialization does not install or enable services.

## Read, compose and act

Read messages in `Inbox/`, `Sent/`, `Archive/` and `Trash/`. Opening a message leaves
its read state unchanged. Local edits and remotely removed messages are preserved
in `Local-only/`. Drafts are local files; moving or deleting one does not send it.

Edit `new email.md`: fill `To`, optional `Cc`/`Bcc`, `Subject`, and the body after
the blank line. Keep one `> ` prefix on every body line; the blank starter has one
ready for typing. Lucy removes that one level when sending. This keeps body text
away from Lucy's unchanged argument parser. To send a quoted line, write `> > text`.
List attachment paths in `Attachments`, quoting paths with spaces;
relative paths start in the draft's directory. Keep the first two marker lines.
Drop it into `Actions/Send/` to send. Lucy restores a blank starter after success.
Saving the draft alone never sends. Sent bodies are plain text with MIME attachments.

Drop a received message into `Actions/Reply/` to create a threaded, quoted reply in
`Drafts/`, then drop that draft into `Actions/Send/`. Use `Actions/Mark read/` and
`Actions/Mark unread/` to change read state. Move messages directly into `Archive/`
or `Trash/` to move them on the server and locally.
Trash moves to the server's Trash mailbox; it does not permanently delete mail.
Move `refresh.md` to any folder watched by Lucy for an immediate fetch. It returns
to the account root automatically. Hidden paths and moves reported only as a
deletion outside the watched tree cannot trigger refresh.

Files return from their action folder before the operation runs. A failed action
keeps the input. Original messages and attachment bytes are stored privately in `.email/`.

## Delivery recovery

After an uncertain SMTP outcome or partial recipient acceptance, the same draft
generation is blocked from sending again. Check your provider's Sent folder and
recipient delivery before making another send attempt. Move the original draft
to `Local-only/` to keep the delivery record. If it was `new email.md`, run initialization
again to create a missing starter; for a reply, use `Actions/Reply/` again. Copy only
the intended content and remaining recipients into the new draft; keep its new markers.
Delivery details appear in `status.md`. Refresh retries unfinished Sent-copy work
without another SMTP send; an uncertain append is left for manual review.
"""
    return files


def _needs_credential_setup(
    store: MailStore, setup: dict[str, str], provider: CredentialProvider
) -> bool:
    services = [relative for relative in setup if relative.endswith(".service")]
    if any(
        not os.path.exists(store.path(relative))
        for relative in ["welcome.md", *services]
    ):
        return True
    if os.path.exists(store.path(CREDENTIALS_FILE)):
        return False
    if provider is CredentialProvider.ENVIRONMENT:
        return True
    # A previously generated environment service also needs its missing env file
    # restored when initialization was invoked without explicit provider flags.
    environment_line = f"EnvironmentFile={_unit_path(store.path(CREDENTIALS_FILE))}"
    return any(
        environment_line in store.read_text(relative, 128 * 1024).splitlines()
        for relative in services
    )


def initialize(root: str, args: ParsedArgs, *, event_id: str = "") -> dict[str, int]:
    root = os.path.abspath(os.path.expanduser(root))
    account_text, provider, variables = _account_text(args)
    # Render before touching disk so invalid path characters cannot leave partial setup.
    setup = _setup_files(root, provider, variables)
    private = safe_path(root, ".email")
    os.makedirs(private, mode=0o700, exist_ok=True)
    with locked_file(safe_path(root, ".email/.lock")):
        with MailStore(root) as store:
            if os.path.exists(store.path(ACCOUNT_FILE)) and _needs_credential_setup(
                store, setup, provider
            ):
                account = load_account(root)
                provider = account.imap.credential_provider
                variables = tuple(
                    dict.fromkeys(
                        (account.imap.password_env, account.smtp.password_env)
                    )
                )
                setup = _setup_files(root, provider, variables)
            for folder in (
                "Inbox",
                "Drafts",
                "Sent",
                "Archive",
                "Trash",
                "Local-only",
                ".email/raw",
                ".email/attachments",
                ".email/outgoing",
                "setup-systemd",
                *ACTION_FOLDERS,
            ):
                store.ensure_directory(folder)
            files = {
                ACCOUNT_FILE: account_text,
                WATCHER_FILE: (
                    "--sys-modules dropdir email\n"
                    + shlex.join(["--sys-watch-paths", root])
                    + "\n"
                    "--sys-log-level info\n--sys-disable-opened-events\n"
                ),
                ".gitignore": ".email/\n",
                ".email/.gitignore": "*\n",
                "refresh.md": REFRESH_TEXT,
                **setup,
            }
            if provider is CredentialProvider.ENVIRONMENT:
                files[CREDENTIALS_FILE] = (
                    "# Explicit environment provider: private plaintext systemd EnvironmentFile.\n"
                    + "\n".join(
                        f"# {variable}='your-app-password'" for variable in variables
                    )
                    + "\n"
                )
            for folder, action in DROP_ACTION_FOLDERS.items():
                command = shlex.join(["--email-root", root, "--" + action])
                files[f"{folder}/init.md"] = (
                    shlex.join(["--dropdir-init", command]) + "\n"
                )
            for relative, text in files.items():
                path = store.path(relative)
                if write_text_if_missing(path, text):
                    store.changed[path] = 1
            upgrade_layout(store, event_id=event_id)
            starter = store.path("new email.md")
            if not os.path.exists(starter):
                store.create_draft(new_draft(uuid.uuid4().hex), "new email.md")
            return dict(store.changed)
