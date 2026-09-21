from __future__ import annotations

from dataclasses import replace
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path

import pytest

from demon_lucy.lib.args.sources import parse_note_args
from demon_lucy.modules.dropdir.module import DROPDIR_TEMPLATE
from demon_lucy.modules.email.config import TEMPLATE
from demon_lucy.modules.email.documents import LITERAL_MARKER
from demon_lucy.modules.email.codec import (
    build_outgoing,
    decode_message,
    message_filename,
    new_draft,
    parse_draft,
    render_draft,
    render_message,
    reply_draft,
)
from demon_lucy.modules.email.errors import EmailError


IDENTITY = "9ef8ec8c-7f1a-41cf-974c-65fd6f17d7f5"


def _incoming() -> EmailMessage:
    message = EmailMessage()
    message["From"] = "Мария <maria@example.test>"
    message["To"] = "lucy@example.test"
    message["Subject"] = "Привет, Lucy!"
    message["Date"] = "Tue, 22 Sep 2026 14:30:00 +0200"
    message["Message-ID"] = "<original@example.test>"
    message.set_content("Hello Lucy!\n")
    return message


def _send(draft, tmp_path: Path, *, max_bytes: int = 100_000):
    return build_outgoing(
        draft,
        from_address="Lucy <lucy@example.test>",
        draft_path=str(tmp_path / "draft.md"),
        max_bytes=max_bytes,
    )


def test_new_draft_is_blank_and_roundtrips() -> None:
    draft = new_draft(IDENTITY)
    text = render_draft(draft)
    assert text.startswith(
        f"{LITERAL_MARKER}\n<!-- lucy-email-id:{IDENTITY} -->\nTo: \n"
    )
    assert parse_draft(text) == draft
    assert draft.to == draft.body == draft.subject == ""
    assert text.endswith("\n\n> ")


@pytest.mark.parametrize(
    "body",
    [
        "",
        "\n",
        "line",
        "line\n",
        "> original\n>> nested\n",
        "  leading spaces\n\tindent",
    ],
)
def test_draft_body_uses_one_reversible_quote_level(body: str) -> None:
    draft = replace(new_draft(IDENTITY), body=body)
    text = render_draft(draft)
    quoted = text.partition("\n\n")[2]
    assert all(line.startswith("> ") for line in quoted.split("\n"))
    assert parse_draft(text).body == body


def test_draft_body_flags_are_inert_to_unchanged_note_parser(tmp_path: Path) -> None:
    body = (
        "--dropdir-action-delay-milliseconds 60000\n"
        '--dropdir-action "--email-send" Actions/Send\n'
        "--email-init outside\n--email-send\n"
        "--email-root /another/account\n"
    )
    path = tmp_path / "draft.md"
    draft = replace(new_draft(IDENTITY), to="friend@example.test", body=body)
    path.write_text(render_draft(draft))
    assert parse_note_args(str(path), [*DROPDIR_TEMPLATE, *TEMPLATE]).known == ()
    outgoing = _send(parse_draft(path.read_text()), tmp_path)
    sent = BytesParser(policy=policy.default).parsebytes(outgoing.payload)
    assert sent.get_content().replace("\r\n", "\n") == body


@pytest.mark.parametrize(
    "body", ["plain body", "> quoted\n--email-send", " > indented", ">missing space"]
)
def test_draft_rejects_unquoted_nonempty_body_lines(body: str) -> None:
    headers = render_draft(new_draft(IDENTITY)).partition("\n\n")[0]
    with pytest.raises(EmailError) as error:
        parse_draft(headers + "\n\n" + body)
    assert error.value.reason == "invalid_draft_body"


def test_draft_accepts_empty_body_and_empty_quote_markers() -> None:
    headers = render_draft(new_draft(IDENTITY)).partition("\n\n")[0]
    assert parse_draft(headers + "\n\n").body == ""
    assert parse_draft(headers + "\n\n>").body == ""


def test_draft_normalizes_carriage_returns_before_quoting_flags(tmp_path: Path) -> None:
    draft = replace(new_draft(IDENTITY), body="first\r--email-send\r\nlast")
    path = tmp_path / "draft.md"
    path.write_text(render_draft(draft))
    assert parse_draft(path.read_text()).body == "first\n--email-send\nlast"
    assert parse_note_args(str(path), TEMPLATE).known == ()


def test_draft_paths_quotes_and_literal_flags_roundtrip() -> None:
    draft = replace(
        new_draft(IDENTITY),
        to="Мария <maria@example.test>",
        attachments=(
            '/tmp/Maria\'s "draft".txt',
            r"C:\My Notes\file.txt",
            "simple.txt",
        ),
        body="--cmd destructive\n--- include begin ---\n\nTo: arbitrary body\n",
    )
    assert parse_draft(render_draft(draft)) == draft
    assert parse_draft(render_draft(draft).replace("\n", "\r\n")) == draft


@pytest.mark.parametrize(
    "text",
    [
        "To: a@example.test\n\nbody",
        f"{LITERAL_MARKER}\n<!-- lucy-email-id:../../x -->\nTo: a@example.test\n\nbody",
        render_draft(new_draft(IDENTITY)).replace("Cc: ", "To: "),
        render_draft(new_draft(IDENTITY)).replace("Subject: ", "From: "),
        render_draft(new_draft(IDENTITY)).replace("Subject: ", "Subject: \x00"),
        render_draft(new_draft(IDENTITY)).replace(
            "Attachments: ", "Attachments: 'unclosed"
        ),
    ],
)
def test_draft_rejects_missing_identity_or_malformed_headers(text: str) -> None:
    with pytest.raises(EmailError):
        parse_draft(text)


def test_decode_unicode_plain_preference_and_exact_attachment_bytes() -> None:
    incoming = _incoming()
    incoming.add_alternative("<p>HTML alternative</p>", subtype="html")
    binary = bytes(range(256))
    incoming.add_attachment(
        binary,
        maintype="application",
        subtype="octet-stream",
        filename="../../файл.bin",
    )
    decoded = decode_message(incoming.as_bytes())
    assert decoded.subject == "Привет, Lucy!"
    assert decoded.sender == "Мария <maria@example.test>"
    assert decoded.body == "Hello Lucy!\n"
    assert len(decoded.attachments) == 1
    assert decoded.attachments[0].filename == "../../файл.bin"
    assert decoded.attachments[0].content == binary


def test_html_fallback_drops_remote_assets_scripts_and_styles() -> None:
    incoming = _incoming()
    incoming.set_content(
        "<head><style>hidden style</style></head><p>Hello &amp; goodbye</p>"
        '<script>hidden script</script><img src="https://tracker.test/pixel">'
        '<p><a href="https://tracker.test/link">Visible text</a></p>',
        subtype="html",
    )
    decoded = decode_message(incoming.as_bytes())
    assert "Hello & goodbye" in decoded.body
    assert "Visible text" in decoded.body
    assert "hidden" not in decoded.body
    assert "tracker" not in decoded.body
    assert "<" not in decoded.body


def test_unknown_charset_and_malformed_mime_are_readable() -> None:
    decoded = decode_message(
        b"Subject: =?utf-8?q?Hello?=\r\n"
        b"Content-Type: text/plain; charset=unknown-charset\r\n"
        b"Content-Transfer-Encoding: base64\r\n\r\nSGVsbG8g/wo="
    )
    assert decoded.subject == "Hello"
    assert decoded.body == "Hello \ufffd\n"
    assert decode_message(b"not MIME\n--cmd test").body == "not MIME\n--cmd test"


def test_attachment_message_is_not_rendered_as_body() -> None:
    incoming = _incoming()
    attached = _incoming()
    attached.replace_header("Subject", "Attached")
    incoming.add_attachment(attached, filename="forwarded.eml")
    decoded = decode_message(incoming.as_bytes())
    assert decoded.body == "Hello Lucy!\n"
    assert decoded.attachments[0].filename == "forwarded.eml"
    assert b"Subject: Attached" in decoded.attachments[0].content


def test_attached_message_preserves_exact_bytes_and_original_line_endings() -> None:
    original = (
        b"From: old@example.test\nSubject: folded\n  header\r\n\r\nOriginal\nbody\r\n"
    )
    raw = (
        b"MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=outer\r\n\r\n"
        b"--outer\r\nContent-Type: text/plain\r\n\r\nVisible body\r\n"
        b"--outer\r\nContent-Type: message/rfc822\r\n"
        b"Content-Disposition: attachment; filename=original.eml\r\n\r\n"
        + original
        + b"\r\n--outer--\r\n"
    )
    decoded = decode_message(raw)
    assert decoded.attachments[0].content == original


def test_attachment_bytes_survive_nested_multipart_boundaries() -> None:
    raw = (
        b"Content-Type: multipart/mixed; boundary=outer\r\n\r\n"
        b"--outer\r\nContent-Type: multipart/mixed; boundary=inner\r\n\r\n"
        b"--inner\r\nContent-Type: text/plain\r\n\r\nbody\r\n"
        b"--inner\r\nContent-Type: application/octet-stream\r\n"
        b"Content-Disposition: attachment; filename=x.bin\r\n\r\n"
        b"\x00\xff\r\n\n\r\n--inner--\r\n\r\n--outer--\r\n"
    )
    assert decode_message(raw).attachments[0].content == b"\x00\xff\r\n\n"


def test_received_rendering_is_literal_and_markdown_content_is_inert() -> None:
    incoming = _incoming()
    incoming.replace_header("Subject", "[click](https://bad.test) <img src=x>")
    incoming.set_content(
        "--cmd echo danger\n--- include begin ---\n<img src=x>\n![x](https://bad.test)\n"
    )
    text = render_message(
        decode_message(incoming.as_bytes()),
        identity=IDENTITY,
        read=False,
        attachment_links=[("../../[x].bin", "../.email/attachments/one (2).bin")],
    )
    assert text.startswith(f"{LITERAL_MARKER}\n<!-- lucy-email-id:{IDENTITY} -->\n")
    assert "<img" not in text
    assert "![x]" not in text
    assert "> \\-\\-cmd echo danger" in text
    assert "../.email/attachments/one%20%282%29.bin" in text
    assert "**Status:** unread" in text


@pytest.mark.parametrize(
    "target", ["https://bad.test/a", "/etc/passwd", r"\server\x", "file\nname"]
)
def test_received_rendering_rejects_external_or_unsafe_links(target: str) -> None:
    with pytest.raises(EmailError):
        render_message(
            decode_message(_incoming().as_bytes()),
            identity=IDENTITY,
            read=True,
            attachment_links=[("file", target)],
        )


def test_message_filename_is_unique_bounded_and_cannot_escape_directory() -> None:
    incoming = _incoming()
    incoming.replace_header("Subject", "../" + "🦊" * 500 + r"\?*")
    decoded = decode_message(incoming.as_bytes())
    name = message_filename(decoded, IDENTITY, read=False)
    assert name.startswith("● 2026-09-22 ")
    assert IDENTITY in name
    assert "/" not in name and "\\" not in name
    assert len(name.encode()) < 255
    assert not message_filename(decoded, IDENTITY, read=True).startswith("●")


def test_reply_prefers_reply_to_and_retains_thread_without_attachments() -> None:
    incoming = _incoming()
    incoming["Reply-To"] = "Reply <reply@example.test>"
    incoming["References"] = "<first@example.test>"
    incoming.add_attachment(
        b"data", maintype="application", subtype="octet-stream", filename="original.bin"
    )
    draft = reply_draft(IDENTITY, decode_message(incoming.as_bytes()))
    assert draft.to == "Reply <reply@example.test>"
    assert draft.subject == "Re: Привет, Lucy!"
    assert draft.in_reply_to == "<original@example.test>"
    assert draft.references == "<first@example.test> <original@example.test>"
    assert draft.attachments == ()
    assert "> Hello Lucy\\!" in draft.body
    assert parse_draft(render_draft(draft)) == draft


def test_reply_does_not_repeat_prefix_or_use_invalid_message_id() -> None:
    decoded = replace(
        decode_message(_incoming().as_bytes()),
        subject="RE: Test",
        message_id="not a message id",
        references="bad <good@example.test>",
    )
    draft = reply_draft(IDENTITY, decoded)
    assert draft.subject == "RE: Test"
    assert draft.in_reply_to == ""
    assert draft.references == "<good@example.test>"


def test_outgoing_plain_text_unicode_bcc_and_stable_message_id(tmp_path: Path) -> None:
    draft = replace(
        new_draft(IDENTITY),
        to="Мария <maria@example.test>",
        cc="copy@example.test",
        bcc="hidden@example.test, maria@example.test",
        subject="Привет",
        body="--cmd is body text\nこんにちは",
    )
    outgoing = _send(draft, tmp_path)
    assert outgoing.payload.isascii()
    parsed = BytesParser(policy=policy.default).parsebytes(outgoing.payload)
    assert parsed["From"].addresses[0].addr_spec == "lucy@example.test"
    assert parsed["To"].addresses[0].display_name == "Мария"
    assert parsed["Subject"] == "Привет"
    assert parsed.get("Bcc") is None
    assert b"hidden@example.test" not in outgoing.payload
    assert outgoing.recipients == (
        "maria@example.test",
        "copy@example.test",
        "hidden@example.test",
    )
    assert "--cmd is body text" in parsed.get_content()
    assert "こんにちは" in parsed.get_content()
    assert parsed.get_content_type() == "text/plain"
    assert _send(draft, tmp_path).message_id == outgoing.message_id
    assert (
        _send(
            replace(draft, identity="cdf624a013c8415daacfbff761538b4e"), tmp_path
        ).message_id
        != outgoing.message_id
    )


@pytest.mark.parametrize(
    "recipient",
    [
        "",
        "missing-domain",
        "a@",
        "a@example.test\r\nBcc: victim@example.test",
        "a@example.test,,",
        "@example.test",
        "Bad <a@example.test",
        "ü@example.test",
    ],
)
def test_outgoing_rejects_invalid_recipients(tmp_path: Path, recipient: str) -> None:
    with pytest.raises(EmailError):
        _send(replace(new_draft(IDENTITY), to=recipient), tmp_path)


def test_outgoing_allows_bcc_only_and_idna_domain(tmp_path: Path) -> None:
    outgoing = _send(replace(new_draft(IDENTITY), bcc="someone@пример.рф"), tmp_path)
    assert outgoing.recipients == ("someone@xn--e1afmkfd.xn--p1ai",)
    parsed = BytesParser(policy=policy.default).parsebytes(outgoing.payload)
    assert parsed.get("Bcc") is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("subject", "hello\nInjected: yes"),
        ("in_reply_to", "<x@test>\r\nInjected: yes"),
        ("references", "not-a-reference"),
    ],
)
def test_outgoing_rejects_header_injection(
    tmp_path: Path, field: str, value: str
) -> None:
    with pytest.raises(EmailError):
        _send(
            replace(new_draft(IDENTITY), to="valid@example.test", **{field: value}),
            tmp_path,
        )


def test_outgoing_reads_relative_attachments_and_preserves_bytes(
    tmp_path: Path,
) -> None:
    filename = 'Мария\'s "notes".bin'
    content = bytes(range(256))
    (tmp_path / filename).write_bytes(content)
    draft = replace(
        new_draft(IDENTITY), to="valid@example.test", attachments=(filename,)
    )
    parsed = BytesParser(policy=policy.default).parsebytes(
        _send(draft, tmp_path).payload
    )
    part = next(parsed.iter_attachments())
    assert part.get_payload(decode=True) == content
    assert part.get_filename() == filename


def test_outgoing_rejects_missing_symlink_and_parent_symlink_attachments(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "file.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(real / "file.txt")
    (tmp_path / "linked").symlink_to(real, target_is_directory=True)
    for attachment in ("missing", "link.txt", "linked/file.txt", "real"):
        with pytest.raises(EmailError):
            _send(
                replace(
                    new_draft(IDENTITY),
                    to="valid@example.test",
                    attachments=(attachment,),
                ),
                tmp_path,
            )


def test_outgoing_enforces_body_attachment_and_encoded_mime_size(
    tmp_path: Path,
) -> None:
    (tmp_path / "attachment.bin").write_bytes(b"x" * 1000)
    draft = replace(new_draft(IDENTITY), to="valid@example.test")
    for candidate, maximum in (
        (replace(draft, body="x" * 2000), 1000),
        (replace(draft, attachments=("attachment.bin",)), 999),
        (replace(draft, attachments=("attachment.bin",)), 1400),
        (draft, 20),
    ):
        with pytest.raises(EmailError, match="size limit") as error:
            _send(candidate, tmp_path, max_bytes=maximum)
        assert error.value.reason == "message_too_large"
