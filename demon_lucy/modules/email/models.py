from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Security(StrEnum):
    TLS = "tls"
    STARTTLS = "starttls"


class SentCopyMode(StrEnum):
    APPEND = "append"
    SERVER = "server"


class CredentialProvider(StrEnum):
    KEYRING = "keyring"
    ENVIRONMENT = "environment"


@dataclass(frozen=True)
class ServerSettings:
    host: str
    port: int
    security: Security
    username: str
    password_env: str
    timeout_seconds: int
    credential_provider: CredentialProvider


@dataclass(frozen=True)
class AccountConfig:
    root: str
    from_address: str
    imap: ServerSettings
    smtp: ServerSettings
    mailboxes: dict[str, str]
    initial_limit: int
    max_message_bytes: int
    sent_copy: SentCopyMode


@dataclass(frozen=True)
class MailboxInfo:
    name: str
    flags: frozenset[str]


@dataclass(frozen=True)
class MessageInfo:
    uid: int
    flags: frozenset[str]
    size: int


@dataclass(frozen=True)
class MoveResult:
    uidvalidity: int | None
    uid: int | None


@dataclass(frozen=True)
class DeliveryResult:
    accepted: tuple[str, ...]
    refused: tuple[str, ...]


@dataclass(frozen=True)
class Attachment:
    filename: str
    content_type: str
    content: bytes


@dataclass(frozen=True)
class DecodedMessage:
    sender: str
    to: str
    cc: str
    subject: str
    date: str
    message_id: str
    reply_to: str
    references: str
    body: str
    attachments: tuple[Attachment, ...]


@dataclass(frozen=True)
class Draft:
    identity: str
    to: str
    cc: str
    bcc: str
    subject: str
    attachments: tuple[str, ...]
    body: str
    in_reply_to: str = ""
    references: str = ""


@dataclass(frozen=True)
class OutgoingMessage:
    payload: bytes
    sender: str
    recipients: tuple[str, ...]
    message_id: str
