from __future__ import annotations

import html
import mimetypes
import re
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import formatdate, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

from demon_lucy.lib.args.parser import split_arg_line
from demon_lucy.modules.email.files import read_bytes_no_follow
from demon_lucy.modules.email.documents import (
    LITERAL_MARKER,
    read_frontmatter,
    render_frontmatter,
)
from demon_lucy.modules.email.errors import EmailError
from demon_lucy.modules.email.models import (
    Attachment,
    DecodedMessage,
    Draft,
    OutgoingMessage,
)

_IDENTITY = re.compile(r"<!-- lucy-email-id:([a-fA-F0-9-]+) -->")
_HEADER_NAMES = (
    "To",
    "Cc",
    "Bcc",
    "Subject",
    "Attachments",
    "In-Reply-To",
    "References",
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MESSAGE_ID = re.compile(r"<[^<>\s@]+@[^<>\s@]+>")


def _single_line(value: object) -> str:
    return " ".join(_CONTROL.sub(" ", str(value or "")).split())


def _validate_header(value: str) -> None:
    if _CONTROL.search(value):
        raise EmailError(
            "Email headers must contain one line of text.", reason="invalid_header"
        )


def _validate_identity(identity: str) -> None:
    try:
        UUID(identity)
    except (ValueError, AttributeError, TypeError) as error:
        raise EmailError(
            "The email identity is missing or invalid.", reason="invalid_identity"
        ) from error


def _markdown(value: str) -> str:
    value = html.escape(value, quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", value)


class _HTMLText(HTMLParser):
    """Read text without interpreting URLs or executing/loading HTML content."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self.hidden: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "head", "template"}:
            self.hidden.append(tag)
        if not self.hidden and tag in {
            "br",
            "p",
            "div",
            "li",
            "tr",
            "hr",
            "blockquote",
        }:
            self.output.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
            return
        if tag in {"p", "div", "li", "tr", "blockquote"}:
            self.output.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.output.append(data)


def _text_content(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        original = part.get_payload()
        return original if isinstance(original, str) else ""
    try:
        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    except (LookupError, TypeError):
        return payload.decode("utf-8", errors="replace")


def _attachment_content(part: Message, raw: bytes | None) -> bytes:
    if raw is not None:
        # Header-only parsing leaves an attached message as a byte payload.
        # Fully parsing message/rfc822 and serializing it again would change
        # its original line endings and potentially its header folding.
        unparsed = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
        payload = unparsed.get_payload(decode=True)
        if payload is not None:
            return payload
    payload = part.get_payload(decode=True)
    if payload is not None:
        return payload
    # Encapsulated messages are parsed as a list instead of a byte payload.
    children = part.get_payload()
    if isinstance(children, list):
        return b"\r\n".join(child.as_bytes(policy=policy.SMTP) for child in children)
    return str(children or "").encode("utf-8", errors="replace")


def _raw_children(raw: bytes | None, boundary: str | None) -> list[bytes]:
    if raw is None or boundary is None:
        return []
    try:
        delimiter = b"--" + boundary.encode("ascii")
    except UnicodeError:
        return []
    children: list[bytes] = []
    current: list[bytes] | None = None
    for line in raw.splitlines(keepends=True):
        stripped = line.rstrip(b"\r\n").rstrip(b" \t")
        if stripped in (delimiter, delimiter + b"--"):
            if current is not None:
                content = b"".join(current)
                # The newline immediately preceding a boundary belongs to
                # that boundary, not to the preceding MIME part's payload.
                content = (
                    content[:-2]
                    if content.endswith(b"\r\n")
                    else content.removesuffix(b"\n")
                )
                children.append(content)
            if stripped == delimiter + b"--":
                current = None
                break
            current = []
        elif current is not None:
            current.append(line)
    if current is not None:
        children.append(b"".join(current))
    return children


def decode_message(raw: bytes) -> DecodedMessage:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    body_part = message.get_body(preferencelist=("plain", "html"))
    body = _text_content(body_part) if body_part is not None else ""
    if body_part is not None and body_part.get_content_type() == "text/html":
        parser = _HTMLText()
        parser.feed(body)
        parser.close()
        body = re.sub(
            r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", "".join(parser.output)
        ).strip()
    body = body.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "\ufffd")

    attachments: list[Attachment] = []

    def collect(part: Message, raw_part: bytes | None) -> None:
        if part is not body_part and (
            part.get_content_disposition() == "attachment"
            or part.get_filename() is not None
            or (not part.is_multipart() and part.get_content_maintype() != "text")
        ):
            attachments.append(
                Attachment(
                    filename=_single_line(part.get_filename() or "attachment"),
                    content_type=part.get_content_type(),
                    content=_attachment_content(part, raw_part),
                )
            )
            return
        if part.is_multipart():
            raw_parts = _raw_children(raw_part, part.get_boundary())
            for index, child in enumerate(part.get_payload()):
                collect(child, raw_parts[index] if index < len(raw_parts) else None)

    collect(message, raw)
    return DecodedMessage(
        sender=_single_line(message.get("From")),
        to=_single_line(message.get("To")),
        cc=_single_line(message.get("Cc")),
        subject=_single_line(message.get("Subject")),
        date=_single_line(message.get("Date")),
        message_id=_single_line(message.get("Message-ID")),
        reply_to=_single_line(message.get("Reply-To")),
        references=_single_line(message.get("References")),
        body=body,
        attachments=tuple(attachments),
    )


def new_draft(identity: str) -> Draft:
    _validate_identity(identity)
    return Draft(
        identity=identity, to="", cc="", bcc="", subject="", attachments=(), body=""
    )


def render_draft(draft: Draft) -> str:
    _validate_identity(draft.identity)
    values = dict(
        zip(
            ("to", "cc", "bcc", "subject", "in-reply-to", "references"),
            (
                draft.to,
                draft.cc,
                draft.bcc,
                draft.subject,
                draft.in_reply_to,
                draft.references,
            ),
        )
    )
    for value in (*values.values(), *draft.attachments):
        _validate_header(value)
    return render_frontmatter(
        {
            "email": "draft",
            "id": draft.identity,
            **values,
            "attachments": list(draft.attachments),
        },
        draft.body.replace("\r\n", "\n").replace("\r", "\n"),
    )


def parse_draft(text: str) -> Draft:
    # Existing registered drafts keep their generation and delivery journal during migration.
    if text.startswith(LITERAL_MARKER):
        return _parse_legacy_draft(text)
    values, body = read_frontmatter(text)
    if values.get("email") != "draft" or set(values) - {
        "email",
        "id",
        "to",
        "cc",
        "bcc",
        "subject",
        "attachments",
        "in-reply-to",
        "references",
    }:
        raise EmailError("Unknown email draft header.", reason="invalid_draft")
    identity = values.get("id")
    _validate_identity(identity)
    headers = {}
    for name in ("to", "cc", "bcc", "subject", "in-reply-to", "references"):
        value = values.get(name, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise EmailError(
                "Email headers must contain text.", reason="invalid_header"
            )
        _validate_header(value)
        headers[name.replace("-", "_")] = value
    attachments = values.get("attachments", [])
    if attachments is None:
        attachments = []
    if not isinstance(attachments, list) or any(
        not isinstance(path, str) for path in attachments
    ):
        raise EmailError(
            "Attachments must be a YAML list of paths.", reason="invalid_attachments"
        )
    for path in attachments:
        _validate_header(path)
    return Draft(
        identity=identity, attachments=tuple(attachments), body=body, **headers
    )


def _parse_legacy_draft(text: str) -> Draft:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n", 2)
    if len(lines) != 3 or lines[0] != LITERAL_MARKER:
        raise EmailError("This file is not a Lucy email draft.", reason="invalid_draft")
    marker = _IDENTITY.fullmatch(lines[1])
    if marker is None:
        raise EmailError(
            "The email identity is missing or invalid.", reason="invalid_identity"
        )
    identity = marker.group(1)
    _validate_identity(identity)
    header_text, separator, body = lines[2].partition("\n\n")
    if not separator:
        raise EmailError(
            "Separate the draft headers and body with a blank line.",
            reason="invalid_draft",
        )
    values: dict[str, str] = {}
    allowed = {name.lower() for name in _HEADER_NAMES}
    for line in header_text.split("\n"):
        name, colon, value = line.partition(":")
        key = name.lower()
        if not colon or key not in allowed or key in values:
            raise EmailError(
                "The draft has an unknown or duplicate header.", reason="invalid_draft"
            )
        _validate_header(value)
        values[key] = value.strip()
    try:
        attachments = tuple(split_arg_line(values.get("attachments", "")))
    except ValueError as error:
        raise EmailError(
            "Quote attachment paths containing spaces.", reason="invalid_attachments"
        ) from error
    body_lines: list[str] = []
    for line in body.split("\n"):
        if line.startswith("> "):
            body_lines.append(line[2:])
        elif line in {"", ">"}:
            body_lines.append("")
        else:
            raise EmailError(
                "Keep the > prefix on every draft body line; Lucy removes it when sending.",
                reason="invalid_draft_body",
            )
    return Draft(
        identity=identity,
        to=values.get("to", ""),
        cc=values.get("cc", ""),
        bcc=values.get("bcc", ""),
        subject=values.get("subject", ""),
        attachments=attachments,
        body="\n".join(body_lines),
        in_reply_to=values.get("in-reply-to", ""),
        references=values.get("references", ""),
    )


def reply_draft(identity: str, message: DecodedMessage) -> Draft:
    _validate_identity(identity)
    subject = (
        message.subject
        if re.match(r"re\s*:", message.subject, re.IGNORECASE)
        else f"Re: {message.subject}"
    )
    message_ids = _MESSAGE_ID.findall(message.message_id)
    in_reply_to = message_ids[0] if message_ids else ""
    references = list(
        dict.fromkeys([*_MESSAGE_ID.findall(message.references), *message_ids[:1]])
    )
    quoted = "\n".join(
        f"> {_markdown(line)}" if line else ">" for line in message.body.splitlines()
    )
    return Draft(
        identity=identity,
        to=message.reply_to or message.sender,
        cc="",
        bcc="",
        subject=subject,
        attachments=(),
        body=f"\n\n{quoted}\n" if quoted else "",
        in_reply_to=in_reply_to,
        references=" ".join(references),
    )


def _addresses(value: str) -> tuple[Address, ...]:
    _validate_header(value)
    if not value.strip():
        return ()
    try:
        probe = EmailMessage(policy=policy.SMTP)
        probe["To"] = value
        header = probe["To"]
        if header.defects or not header.addresses:
            raise ValueError("malformed addresses")
        result: list[Address] = []
        for address in header.addresses:
            if not address.username or not address.domain:
                raise ValueError("incomplete address")
            address.username.encode("ascii")
            domain = address.domain.encode("idna").decode("ascii")
            result.append(
                Address(
                    display_name=address.display_name,
                    username=address.username,
                    domain=domain,
                )
            )
        return tuple(result)
    except (ValueError, TypeError, UnicodeError, IndexError) as error:
        raise EmailError(
            "Use valid email addresses with an ASCII local part.",
            reason="invalid_address",
        ) from error


def _thread_header(value: str) -> str:
    _validate_header(value)
    ids = _MESSAGE_ID.findall(value)
    if value.strip() and " ".join(ids) != " ".join(value.split()):
        raise EmailError(
            "The draft contains an invalid reply reference.", reason="invalid_header"
        )
    return " ".join(ids)


def build_outgoing(
    draft: Draft, *, from_address: str, draft_path: str, max_bytes: int
) -> OutgoingMessage:
    _validate_identity(draft.identity)
    _validate_header(draft.subject)
    sender = _addresses(from_address)
    if len(sender) != 1:
        raise EmailError(
            "Configure exactly one sender address.", reason="invalid_address"
        )
    to, cc, bcc = _addresses(draft.to), _addresses(draft.cc), _addresses(draft.bcc)
    recipients = tuple(dict.fromkeys(address.addr_spec for address in (*to, *cc, *bcc)))
    if not recipients:
        raise EmailError(
            "Add at least one To, Cc, or Bcc recipient.", reason="missing_recipient"
        )
    message_id = f"<lucy-{UUID(draft.identity).hex}@{sender[0].domain}>"
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = sender[0]
    if to:
        message["To"] = to
    if cc:
        message["Cc"] = cc
    message["Subject"] = draft.subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = message_id
    for name, value in (
        ("In-Reply-To", draft.in_reply_to),
        ("References", draft.references),
    ):
        if value:
            message[name] = _thread_header(value)
    if max_bytes < 1 or len(draft.body.encode("utf-8")) > max_bytes:
        raise EmailError(
            "The message exceeds the configured size limit.", reason="message_too_large"
        )
    # Keep the wire payload 7-bit safe without requiring SMTPUTF8/8BITMIME.
    message.set_content(draft.body, cte="quoted-printable")
    total_attachment_bytes = 0
    for value in draft.attachments:
        _validate_header(value)
        if not value:
            raise EmailError(
                "An attachment path is empty.", reason="invalid_attachments"
            )
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = Path(draft_path).absolute().parent / path
        try:
            content = read_bytes_no_follow(
                str(path), max_bytes - total_attachment_bytes
            )
        except ValueError as error:
            raise EmailError(
                "Attachments exceed the configured size limit.",
                reason="message_too_large",
            ) from error
        except OSError as error:
            raise EmailError(
                "An attachment is missing, unsafe, or unreadable.",
                reason="invalid_attachments",
            ) from error
        total_attachment_bytes += len(content)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        maintype, subtype = content_type.split("/", 1)
        message.add_attachment(
            content, maintype=maintype, subtype=subtype, filename=path.name
        )
    payload = message.as_bytes()
    if len(payload) > max_bytes:
        raise EmailError(
            "The message exceeds the configured size limit.", reason="message_too_large"
        )
    return OutgoingMessage(
        payload=payload,
        sender=sender[0].addr_spec,
        recipients=recipients,
        message_id=message_id,
    )


def render_message(
    message: DecodedMessage,
    *,
    identity: str,
    read: bool,
    attachment_links: list[tuple[str, str]],
    status: str = "",
) -> str:
    _validate_identity(identity)
    lines = [
        f"# {_markdown(message.subject or '(no subject)')}",
        "",
    ]
    for name, value in (
        ("From", message.sender),
        ("To", message.to),
        ("Cc", message.cc),
        ("Date", message.date),
    ):
        if value:
            lines.append(f"**{name}:** {_markdown(_single_line(value))}")
    lines.append(f"**Status:** {'read' if read else 'unread'}")
    if status:
        lines.append(f"**Note:** {_markdown(_single_line(status))}")
    if attachment_links:
        lines.extend(["", "**Attachments:**"])
        for name, target in attachment_links:
            if (
                target.startswith(("/", "\\"))
                or ":" in target
                or "\\" in target
                or _CONTROL.search(target)
            ):
                raise EmailError(
                    "An attachment link is unsafe.", reason="invalid_attachments"
                )
            lines.append(
                f"- [{_markdown(_single_line(name))}]({quote(target, safe='/.-_~')})"
            )
    lines.append("")
    lines.extend(
        f"> {_markdown(line)}" if line else ">" for line in message.body.splitlines()
    )
    return render_frontmatter(
        {
            "email": "message",
            "id": identity,
            "from": message.sender,
            "to": message.to,
            "subject": message.subject,
            "date": message.date,
            "read": read,
        },
        "\n".join(lines) + "\n",
    )


def message_filename(message: DecodedMessage, identity: str, *, read: bool) -> str:
    _validate_identity(identity)

    def component(value: str, limit: int) -> str:
        value = re.sub(r'[<>:"/\\|?*]', "_", _single_line(value)).strip(" .")
        return (
            value.encode("utf-8")[:limit].decode("utf-8", errors="ignore").rstrip(" .")
        )

    try:
        date = parsedate_to_datetime(message.date).strftime("%Y-%m-%d") + " "
    except (ValueError, TypeError, IndexError):
        date = ""
    sender = component(message.sender, 40)
    sender = f"{sender} - " if sender else ""
    subject = component(message.subject or "No subject", 100) or "No subject"
    return f"{'' if read else '● '}{date}{sender}{subject} [{identity}].md"
