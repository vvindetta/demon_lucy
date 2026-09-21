"""Email credentials from an approved Linux secret store or explicit environment."""

from __future__ import annotations

import getpass
import importlib
import os
import re
import sys
import warnings

from demon_lucy.modules.email.errors import EmailError
from demon_lucy.modules.email.models import (
    AccountConfig,
    CredentialProvider,
    ServerSettings,
)


def _service(settings: ServerSettings, protocol: str) -> str:
    if protocol not in {"imap", "smtp"}:
        raise EmailError(
            "Unknown email credential protocol.", reason="credentials_invalid"
        )
    host = settings.host.strip().rstrip(".").lower()
    if (
        not host
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in host)
        or not settings.username
        or any(ord(char) < 32 or ord(char) == 127 for char in settings.username)
    ):
        raise EmailError(
            "Set a valid email server hostname and username before saving credentials.",
            reason="credentials_invalid",
        )
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise EmailError(
            "The email server hostname is invalid.", reason="credentials_invalid"
        ) from None
    return (
        f"demon-lucy.email:{protocol}:{host}:{settings.port}:{settings.security.value}"
    )


def _secure_backend():
    if not sys.platform.startswith("linux"):
        raise EmailError(
            "The keyring provider requires Linux Secret Service or KWallet. Configure the environment provider explicitly on other systems.",
            reason="credentials_keyring_unavailable",
        )
    try:
        keyring = importlib.import_module("keyring")
    except ImportError:
        raise EmailError(
            "Install email secret-store support: python -m pip install -r demon_lucy/modules/email/requirements.txt.",
            reason="credentials_dependency_missing",
        ) from None
    except Exception:
        raise EmailError(
            "Linux secret-store support could not be loaded. Check the keyring installation and configuration.",
            reason="credentials_keyring_unavailable",
        ) from None
    try:
        approved = []
        for module_name, class_names in (
            ("keyring.backends.SecretService", ("Keyring",)),
            ("keyring.backends.kwallet", ("DBusKeyring", "DBusKeyringKWallet4")),
        ):
            try:
                module = importlib.import_module(module_name)
            except ImportError:
                continue
            approved.extend(
                getattr(module, name) for name in class_names if hasattr(module, name)
            )
        backend = keyring.get_keyring()
        if type(backend) in approved:
            return backend
        chainer = importlib.import_module("keyring.backends.chainer")
        if type(backend) is chainer.ChainerBackend:
            # Invoke one approved child directly. Chainer.get_password() itself
            # may fall through to a plaintext or otherwise unsupported backend.
            for child in backend.backends:
                if type(child) in approved:
                    return child
    except Exception:
        raise EmailError(
            "The Linux secret store is unavailable. Start or unlock Secret Service or KWallet in this desktop session and retry.",
            reason="credentials_keyring_unavailable",
        ) from None
    raise EmailError(
        "Configure Linux Secret Service or KWallet for keyring. Plaintext, null, and third-party credential backends are not supported.",
        reason="credentials_backend_unsupported",
    )


def password_for(settings: ServerSettings, protocol: str) -> str:
    if settings.credential_provider is CredentialProvider.ENVIRONMENT:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", settings.password_env):
            raise EmailError(
                "Invalid email password environment variable.",
                reason="credentials_invalid",
            )
        password = os.environ.get(settings.password_env)
        if not password:
            raise EmailError(
                "The email password environment variable is empty.",
                reason="credentials_missing",
            )
        return password
    if settings.credential_provider is not CredentialProvider.KEYRING:
        raise EmailError(
            "Unknown email credential provider.", reason="credentials_invalid"
        )
    service = _service(settings, protocol)
    backend = _secure_backend()
    try:
        password = backend.get_password(service, settings.username)
    except Exception:
        raise EmailError(
            "The email password could not be read from the Linux secret store. Unlock Secret Service or KWallet and retry.",
            reason="credentials_keyring_unavailable",
        ) from None
    if not isinstance(password, str) or not password:
        raise EmailError(
            "No email password is saved for this server. Run --email-credentials-save with --email-root in an interactive terminal.",
            reason="credentials_missing",
        )
    return password


def save_credentials(config: AccountConfig) -> None:
    entries = [("imap", config.imap), ("smtp", config.smtp)]
    if any(
        settings.credential_provider is not CredentialProvider.KEYRING
        for _, settings in entries
    ):
        raise EmailError(
            "Set --email-credential-provider keyring in the account configuration before saving credentials.",
            reason="credentials_provider_mismatch",
        )
    services = [_service(settings, protocol) for protocol, settings in entries]
    if sys.stdin is None or not sys.stdin.isatty():
        raise EmailError(
            "Save email credentials from an interactive terminal; password input must not be echoed.",
            reason="credentials_prompt_unavailable",
        )
    backend = _secure_backend()
    passwords = []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            for protocol, _ in entries:
                password = getpass.getpass(
                    f"{protocol.upper()} password: ", stream=sys.stderr
                )
                if (
                    not isinstance(password, str)
                    or not password
                    or any(char in password for char in "\r\n\x00")
                ):
                    raise EmailError(
                        "Email passwords must be nonempty and contain no newline or NUL characters.",
                        reason="credentials_invalid",
                    )
                passwords.append(password)
    except (getpass.GetPassWarning, EOFError, OSError):
        raise EmailError(
            "Secure password input is unavailable. Retry in an interactive terminal with echo disabled.",
            reason="credentials_prompt_unavailable",
        ) from None
    try:
        # Both hidden prompts complete before the first write. Never send a
        # password to a shell command, note, account configuration, or logger.
        for (_, settings), service, password in zip(entries, services, passwords):
            backend.set_password(service, settings.username, password)
    except Exception:
        raise EmailError(
            "Email credentials could not be saved in the Linux secret store. Unlock Secret Service or KWallet and retry; one password may already have been saved.",
            reason="credentials_keyring_unavailable",
        ) from None
