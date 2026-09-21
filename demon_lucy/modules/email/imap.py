"""UID-only IMAP operations with bounded literals and targeted deletion."""

from __future__ import annotations

import base64
import imaplib
import re
import ssl
from collections.abc import Callable, Iterator
from email import policy
from email.parser import BytesHeaderParser

from demon_lucy.modules.email.errors import EmailError
from demon_lucy.modules.email.credentials import password_for
from demon_lucy.modules.email.models import (
    MailboxInfo,
    MessageInfo,
    MoveResult,
    Security,
    ServerSettings,
)


_CHUNK_BYTES = 64 * 1024
_BATCH_UIDS = 100
_UID = re.compile(rb"(?:^|[ (])UID ([1-9][0-9]*)(?=[ )]|$)", re.I)
_SIZE = re.compile(rb"(?:^|[ (])RFC822\.SIZE ([0-9]+)(?=[ )]|$)", re.I)
_RECORD_START = re.compile(rb"^[0-9]+ \(")
_LIST = re.compile(rb'^\(([^)]*)\)\s+(?:NIL|"(?:[^"\\]|\\.)*")\s+(.+)$', re.I)


class _BoundedLiterals:
    literal_budget = _CHUNK_BYTES

    def read(self, size):
        if size < 0 or size > self.literal_budget:
            raise EmailError(
                "IMAP returned an oversized literal.", reason="imap_response_too_large"
            )
        self.literal_budget -= size
        return super().read(size)


class _IMAP4(_BoundedLiterals, imaplib.IMAP4):
    pass


class _IMAP4_SSL(_BoundedLiterals, imaplib.IMAP4_SSL):
    pass


def _protocol_error() -> EmailError:
    return EmailError(
        "IMAP returned an invalid response.", reason="imap_response_invalid"
    )


def _encode_mailbox(name: str) -> bytes:
    if not name or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise EmailError("Invalid mailbox name.", reason="mailbox_invalid")
    result: list[str] = []
    non_ascii: list[str] = []

    def flush() -> None:
        if non_ascii:
            encoded = base64.b64encode("".join(non_ascii).encode("utf-16-be")).decode(
                "ascii"
            )
            result.append("&" + encoded.rstrip("=").replace("/", ",") + "-")
            non_ascii.clear()

    for char in name:
        if " " <= char <= "~":
            flush()
            result.append("&-" if char == "&" else char)
        else:
            non_ascii.append(char)
    flush()
    return "".join(result).encode("ascii")


def _decode_mailbox(value: bytes) -> str:
    try:

        def decode(match: re.Match[str]) -> str:
            encoded = match.group(1).encode("ascii")
            if not encoded:
                return "&"
            return base64.b64decode(
                encoded.replace(b",", b"/") + b"=" * (-len(encoded) % 4), validate=True
            ).decode("utf-16-be")

        return re.sub(r"&([^-]*)-", decode, value.decode("ascii"))
    except (UnicodeError, ValueError):
        raise _protocol_error() from None


def _quote(value: bytes) -> bytes:
    if any(char in value for char in (b"\r", b"\n", b"\x00")):
        raise _protocol_error()
    return b'"' + value.replace(b"\\", b"\\\\").replace(b'"', b'\\"') + b'"'


def _uid(value: int) -> str:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= 4294967295
    ):
        raise EmailError("Invalid message UID.", reason="message_uid_invalid")
    return str(value)


def _fetch_records(data: list) -> Iterator[tuple[bytes, list[tuple[bytes, bytes]]]]:
    """Keep trailing UID/FLAGS fields attached to their literal's record."""
    headers: list[bytes] = []
    literals: list[tuple[bytes, bytes]] = []
    for item in data:
        if item is None:
            continue
        header = item[0] if isinstance(item, tuple) else item
        if not isinstance(header, bytes):
            raise _protocol_error()
        if _RECORD_START.match(header) and headers:
            yield b" ".join(headers), literals
            headers, literals = [], []
        headers.append(header)
        if isinstance(item, tuple):
            if len(item) != 2 or not isinstance(item[1], bytes):
                raise _protocol_error()
            literals.append(item)
    if headers:
        yield b" ".join(headers), literals


class ImapSession:
    def __init__(self, settings: ServerSettings):
        self.settings = settings
        self.client = None
        self.capabilities: frozenset[str] = frozenset()

    def __enter__(self) -> ImapSession:
        settings = self.settings
        password = password_for(settings, "imap")
        if any(char in settings.username or char in password for char in "\r\n\x00"):
            raise EmailError("Invalid IMAP credentials.", reason="credentials_invalid")
        if settings.security not in (Security.TLS, Security.STARTTLS):
            raise EmailError(
                "IMAP requires TLS or STARTTLS.", reason="security_invalid"
            )
        try:
            context = ssl.create_default_context()
            if settings.security is Security.TLS:
                self.client = _IMAP4_SSL(
                    settings.host,
                    settings.port,
                    ssl_context=context,
                    timeout=settings.timeout_seconds,
                )
            else:
                self.client = _IMAP4(
                    settings.host, settings.port, timeout=settings.timeout_seconds
                )
                self._command(self.client.starttls, ssl_context=context)
            self.client.debug = 0
            try:
                self._command(self.client.login, settings.username, password)
            except EmailError as error:
                if error.reason == "imap_command_rejected":
                    raise EmailError(
                        "IMAP authentication failed.",
                        reason="imap_authentication_failed",
                    ) from None
                raise
            # Older Python versions do not refresh capabilities after LOGIN.
            data = self._command(self.client.capability)
            self.capabilities = frozenset(
                b" ".join(item for item in data if isinstance(item, bytes))
                .decode("ascii")
                .upper()
                .split()
            )
            return self
        except ssl.SSLCertVerificationError:
            self.__exit__(None, None, None)
            raise EmailError(
                "IMAP TLS certificate verification failed.",
                reason="imap_certificate_invalid",
            ) from None
        except (OSError, imaplib.IMAP4.error, UnicodeError):
            self.__exit__(None, None, None)
            raise EmailError(
                "IMAP connection failed.", reason="imap_unavailable", retryable=True
            ) from None
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.client is not None:
            try:
                self.client.logout()
            except (OSError, imaplib.IMAP4.error, EmailError):
                try:
                    self.client.shutdown()
                except (OSError, imaplib.IMAP4.error):
                    pass
            finally:
                self.client = None

    def _command(
        self, command, *args, literal_budget: int = _CHUNK_BYTES, **kwargs
    ) -> list:
        self.client.literal_budget = literal_budget
        try:
            status, data = command(*args, **kwargs)
        except ssl.SSLCertVerificationError:
            raise EmailError(
                "IMAP TLS certificate verification failed.",
                reason="imap_certificate_invalid",
            ) from None
        except (OSError, imaplib.IMAP4.abort):
            raise EmailError(
                "IMAP connection failed.", reason="imap_unavailable", retryable=True
            ) from None
        except imaplib.IMAP4.error:
            raise EmailError(
                "IMAP rejected the operation.", reason="imap_command_rejected"
            ) from None
        if status != "OK":
            raise EmailError(
                "IMAP rejected the operation.", reason="imap_command_rejected"
            )
        return data or []

    def mailboxes(self) -> list[MailboxInfo]:
        result = []
        for item in self._command(self.client.list, '""', '"*"'):
            if item is None or item == b"":
                continue
            prefix = item[0] if isinstance(item, tuple) else item
            match = _LIST.fullmatch(prefix)
            if match is None:
                raise _protocol_error()
            name = match.group(2)
            if isinstance(item, tuple):
                name = item[1]
            elif name.startswith(b'"'):
                if not name.endswith(b'"'):
                    raise _protocol_error()
                name = re.sub(rb"\\(.)", rb"\1", name[1:-1])
            flags = frozenset(flag.decode("ascii") for flag in match.group(1).split())
            result.append(MailboxInfo(name=_decode_mailbox(name), flags=flags))
        return result

    def select(self, name: str, readonly: bool = False) -> int:
        self._command(
            self.client.select, _quote(_encode_mailbox(name)), readonly=readonly
        )
        _, values = self.client.response("UIDVALIDITY")
        try:
            if len(values) != 1:
                raise ValueError
            value = int(values[0])
            _uid(value)
            return value
        except (TypeError, ValueError):
            raise _protocol_error() from None

    def _search(self, *criteria) -> list[int]:
        data = self._command(self.client.uid, "SEARCH", None, *criteria)
        result = []
        for item in data:
            if item is None:
                continue
            if not isinstance(item, bytes):
                raise _protocol_error()
            for token in item.split():
                if not token.isdigit():
                    raise _protocol_error()
                value = int(token)
                _uid(value)
                result.append(value)
        return sorted(set(result))

    def uids(self) -> list[int]:
        return self._search("ALL")

    def metadata(self, uids: list[int]) -> dict[int, MessageInfo]:
        result = {}
        for start in range(0, len(uids), _BATCH_UIDS):
            batch = uids[start : start + _BATCH_UIDS]
            wanted = set(batch)
            data = self._command(
                self.client.uid,
                "FETCH",
                ",".join(_uid(uid) for uid in batch),
                "(UID FLAGS RFC822.SIZE)",
            )
            for header, _ in _fetch_records(data):
                match = _UID.search(header)
                if match is None or int(match.group(1)) not in wanted:
                    continue
                size = _SIZE.search(header)
                flags_match = re.search(rb"FLAGS \(([^)]*)\)", header, re.I)
                # Unsolicited FLAGS responses need not contain RFC822.SIZE.
                if size is None or flags_match is None:
                    continue
                uid = int(match.group(1))
                result[uid] = MessageInfo(
                    uid,
                    frozenset(
                        flag.decode("ascii", errors="replace")
                        for flag in flags_match.group(1).split()
                    ),
                    int(size.group(1)),
                )
        return result

    def _body(self, uid: int, section: str, offset: int, count: int) -> bytes:
        data = self._command(
            self.client.uid,
            "FETCH",
            _uid(uid),
            f"(UID BODY.PEEK[{section}]<{offset}.{count}>)",
            literal_budget=count,
        )
        found = []
        for header, literals in _fetch_records(data):
            match = _UID.search(header)
            if match is None or int(match.group(1)) != uid:
                continue
            for literal_header, value in literals:
                body = re.search(
                    rb"BODY\["
                    + section.encode("ascii")
                    + rb"\](?:<([0-9]+)>)?\s+\{[0-9]+\}$",
                    literal_header,
                    re.I,
                )
                if body is not None and int(body.group(1) or 0) == offset:
                    found.append(value)
        if len(found) != 1 or len(found[0]) > count:
            raise _protocol_error()
        return found[0]

    def fetch(self, uid: int, max_bytes: int) -> bytes:
        info = self.metadata([uid]).get(uid)
        if info is None:
            raise EmailError("The message no longer exists.", reason="message_missing")
        if info.size > max_bytes:
            raise EmailError(
                "The message exceeds the configured size limit.",
                reason="message_too_large",
            )
        parts = []
        for offset in range(0, info.size, _CHUNK_BYTES):
            count = min(_CHUNK_BYTES, info.size - offset)
            part = self._body(uid, "", offset, count)
            if len(part) != count:
                raise _protocol_error()
            parts.append(part)
        return b"".join(parts)

    def headers(self, uid: int) -> bytes:
        # Read at most 64 KiB; a truncated header is unsafe for reconciliation.
        value = self._body(uid, "HEADER", 0, _CHUNK_BYTES)
        if not value.endswith((b"\r\n\r\n", b"\n\n")):
            raise EmailError(
                "The message headers are incomplete or oversized.",
                reason="message_headers_invalid",
            )
        return value

    def set_seen(self, uid: int, seen: bool) -> None:
        self._command(
            self.client.uid,
            "STORE",
            _uid(uid),
            "+FLAGS.SILENT" if seen else "-FLAGS.SILENT",
            r"(\Seen)",
        )

    def _mapping(self, code: str, source_uid: int | None = None) -> MoveResult:
        _, values = self.client.response(code)
        if not values or values == [None]:
            return MoveResult(None, None)
        try:
            parts = values[-1].split()
            if len(parts) != (3 if source_uid is not None else 2):
                raise ValueError
            validity, destination = int(parts[0]), int(parts[-1])
            _uid(validity)
            _uid(destination)
            if source_uid is not None and int(parts[1]) != source_uid:
                raise ValueError
            return MoveResult(validity, destination)
        except (TypeError, ValueError):
            raise _protocol_error() from None

    def move(
        self,
        uid: int,
        destination: str,
        checkpoint: Callable[[str, dict], None] | None = None,
    ) -> MoveResult:
        source = _uid(uid)
        target = _quote(_encode_mailbox(destination))
        details = {"source_uid": uid, "destination": destination}
        if "MOVE" not in self.capabilities and "UIDPLUS" not in self.capabilities:
            raise EmailError(
                "The IMAP server does not support safe message moves.",
                reason="imap_move_unsupported",
            )
        # Discard old response codes before this operation's result is read.
        self.client.response("COPYUID")
        if "MOVE" in self.capabilities:
            if checkpoint:
                checkpoint("before_move", details)
            self._command(self.client.uid, "MOVE", source, target)
            return self._mapping("COPYUID", uid)
        if checkpoint:
            checkpoint("before_copy", details)
        self._command(self.client.uid, "COPY", source, target)
        copied = self._mapping("COPYUID", uid)
        if copied.uid is None:
            raise EmailError(
                "The server copied the message without a safe UID mapping; refresh before recovery.",
                reason="imap_copy_mapping_missing",
            )
        details = {**details, "uidvalidity": copied.uidvalidity, "uid": copied.uid}
        if checkpoint:
            checkpoint("copied", details)
            checkpoint("before_delete", details)
        self._command(self.client.uid, "STORE", source, "+FLAGS.SILENT", r"(\Deleted)")
        if checkpoint:
            checkpoint("before_expunge", details)
        self._command(self.client.uid, "EXPUNGE", source)
        return copied

    def append(self, mailbox: str, payload: bytes) -> MoveResult:
        self.client.response("APPENDUID")
        self._command(
            self.client.append,
            _quote(_encode_mailbox(mailbox)),
            r"(\Seen)",
            None,
            payload,
        )
        return self._mapping("APPENDUID")

    def find_message(self, mailbox: str, message_id: str) -> list[int]:
        try:
            encoded = message_id.encode("ascii")
        except UnicodeError:
            raise EmailError(
                "Invalid message identifier.", reason="message_id_invalid"
            ) from None
        if not encoded or len(encoded) > 998:
            raise EmailError("Invalid message identifier.", reason="message_id_invalid")
        self.select(mailbox, readonly=True)
        # IMAP HEADER searches are substring searches, so verify exact matches.
        candidates = self._search("HEADER", "Message-ID", _quote(encoded))
        matches = []
        for uid in candidates:
            headers = BytesHeaderParser(policy=policy.default).parsebytes(
                self.headers(uid)
            )
            values = [
                value
                for name, value in headers.raw_items()
                if name.lower() == "message-id"
            ]
            if len(values) == 1 and values[0].strip() == message_id:
                matches.append(uid)
        return matches
