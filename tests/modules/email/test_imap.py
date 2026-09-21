from dataclasses import replace
from io import BytesIO
import imaplib
import re
import ssl

import pytest

from demon_lucy.modules.email import imap
from demon_lucy.modules.email.errors import EmailError
from demon_lucy.modules.email.models import (
    CredentialProvider,
    MoveResult,
    Security,
    ServerSettings,
)


SETTINGS = ServerSettings(
    "imap.example.test",
    993,
    Security.TLS,
    "alice",
    "LUCY_EMAIL_TEST_PASSWORD",
    30,
    CredentialProvider.ENVIRONMENT,
)


class FakeIMAP:
    def __init__(self):
        self.calls = []
        self.capability_value = b"IMAP4rev1 MOVE UIDPLUS"
        self.responses = {}
        self.messages = {
            41: b"Message-ID: <sent@example.test>\r\nSubject: test\r\n\r\nhello"
        }
        self.fetch_data = None
        self.supply_mapping = True

    def login(self, username, password):
        assert password == "private-password"
        self.calls.append(("LOGIN",))
        return "OK", [b"logged in"]

    def starttls(self, ssl_context):
        assert ssl_context.verify_mode == ssl.CERT_REQUIRED
        assert ssl_context.check_hostname
        self.calls.append(("STARTTLS",))
        return "OK", [b"secure"]

    def capability(self):
        self.calls.append(("CAPABILITY",))
        return "OK", [self.capability_value]

    def select(self, name, readonly=False):
        self.calls.append(("SELECT", name, readonly))
        self.responses["UIDVALIDITY"] = [b"7"]
        return "OK", [b"1"]

    def response(self, name):
        return name, self.responses.pop(name, [None])

    def list(self, *args):
        return "OK", [
            b'(\\HasNoChildren \\Sent) "/" "Sent messages"',
            b'(\\Archive) "/" "&BB8EPgRHBEIEMA-"',
            (b'(\\Trash) "/" {8}', b"Trash &-"),
            b"",
        ]

    def uid(self, command, *args):
        self.calls.append((command, *args))
        if command == "SEARCH":
            return "OK", [b" ".join(str(uid).encode() for uid in self.messages)]
        if command == "FETCH":
            if self.fetch_data is not None:
                return "OK", self.fetch_data
            if args[1] == "(UID FLAGS RFC822.SIZE)":
                return "OK", [
                    f"1 (UID {uid} FLAGS (\\Seen) RFC822.SIZE {len(self.messages[uid])})".encode()
                    for uid in map(int, args[0].split(","))
                    if uid in self.messages
                ]
            uid = int(args[0])
            match = re.search(r"BODY.PEEK\[(.*?)\]<(\d+)\.(\d+)>", args[1])
            section, offset, count = match.groups()
            data = self.messages[uid]
            if section == "HEADER":
                data = data.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
            data = data[int(offset) : int(offset) + int(count)]
            # A UID after the literal must work; sequence number 999 is unrelated.
            return "OK", [
                (f"999 (BODY[{section}]<{offset}> {{{len(data)}}}".encode(), data),
                f" UID {uid})".encode(),
            ]
        if command in ("MOVE", "COPY") and self.supply_mapping:
            self.responses["COPYUID"] = [f"9 {args[0]} 81".encode()]
        return "OK", [b"done"]

    def append(self, *args):
        self.calls.append(("APPEND", *args))
        self.responses["APPENDUID"] = [b"9 82"]
        return "OK", [b"done"]

    def logout(self):
        self.calls.append(("LOGOUT",))


@pytest.fixture
def client(monkeypatch):
    fake = FakeIMAP()
    monkeypatch.setenv(SETTINGS.password_env, "private-password")

    def connect(*args, **kwargs):
        assert kwargs["timeout"] == 30
        if "ssl_context" in kwargs:
            assert kwargs["ssl_context"].verify_mode == ssl.CERT_REQUIRED
            assert kwargs["ssl_context"].check_hostname
        return fake

    monkeypatch.setattr(imap, "_IMAP4_SSL", connect)
    monkeypatch.setattr(imap, "_IMAP4", connect)
    return fake


def test_authenticated_capabilities_refresh_and_logout_without_close(client):
    with imap.ImapSession(SETTINGS) as session:
        assert "MOVE" in session.capabilities
        assert session.select("Inbox") == 7
        assert session.uids() == [41]
    assert client.calls[:2] == [("LOGIN",), ("CAPABILITY",)]
    assert client.calls[-1] == ("LOGOUT",)


def test_starttls_before_login(client):
    with imap.ImapSession(replace(SETTINGS, security=Security.STARTTLS)):
        pass
    assert client.calls[:3] == [("STARTTLS",), ("LOGIN",), ("CAPABILITY",)]


def test_imap_uses_selected_credential_provider_before_login(client, monkeypatch):
    settings = replace(SETTINGS, credential_provider=CredentialProvider.KEYRING)
    monkeypatch.delenv(SETTINGS.password_env, raising=False)

    def stored_password(value, protocol):
        assert value is settings and protocol == "imap"
        return "private-password"

    monkeypatch.setattr(imap, "password_for", stored_password)
    with imap.ImapSession(settings):
        pass
    assert client.calls[0] == ("LOGIN",)


def test_mailbox_names_and_special_use_flags(client):
    with imap.ImapSession(SETTINGS) as session:
        folders = session.mailboxes()
        assert [(folder.name, folder.flags) for folder in folders] == [
            ("Sent messages", frozenset({r"\HasNoChildren", r"\Sent"})),
            ("Почта", frozenset({r"\Archive"})),
            ("Trash &", frozenset({r"\Trash"})),
        ]
        session.select('Почта "A" & B')
    assert client.calls[-2][1] == b'"&BB8EPgRHBEIEMA- \\"A\\" &- B"'


def test_fetch_matches_uid_after_literal_and_uses_peek(client):
    with imap.ImapSession(SETTINGS) as session:
        assert session.fetch(41, 1000) == client.messages[41]
        assert session.headers(41).endswith(b"\r\n\r\n")
    assert all(
        "BODY.PEEK" in call[2]
        for call in client.calls
        if call[0] == "FETCH" and "BODY" in call[2]
    )


def test_large_fetch_uses_bounded_chunks(client):
    client.messages[41] = b"x" * (imap._CHUNK_BYTES * 2 + 17)
    with imap.ImapSession(SETTINGS) as session:
        assert session.fetch(41, len(client.messages[41])) == client.messages[41]
    bodies = [call for call in client.calls if call[0] == "FETCH" and "BODY" in call[2]]
    assert len(bodies) == 3
    assert bodies[-1][2].endswith("<131072.17>)")


def test_oversized_message_does_not_download_body(client):
    with imap.ImapSession(SETTINGS) as session:
        with pytest.raises(EmailError) as raised:
            session.fetch(41, 1)
    assert raised.value.reason == "message_too_large"
    assert not any("BODY" in call[2] for call in client.calls if call[0] == "FETCH")


def test_metadata_is_batched_and_ignores_unsolicited_messages(client):
    client.messages = {uid: b"body" for uid in range(1, 202)}
    with imap.ImapSession(SETTINGS) as session:
        assert len(session.metadata(list(client.messages))) == 201
        assert len([call for call in client.calls if call[0] == "FETCH"]) == 3
        client.fetch_data = [
            b"5 (UID 998 FLAGS (\\Seen) RFC822.SIZE 8)",
            b"41 (UID 99 FLAGS (\\Seen))",
            b"999 (UID 41 FLAGS () RFC822.SIZE 17)",
        ]
        assert session.metadata([41])[41].size == 17


def test_fetch_rejects_sequence_number_instead_of_requested_uid(client):
    with imap.ImapSession(SETTINGS) as session:
        client.fetch_data = [(b"41 (UID 998 BODY[]<0> {4}", b"body"), b")"]
        with pytest.raises(EmailError) as raised:
            session._body(41, "", 0, 4)
    assert raised.value.reason == "imap_response_invalid"


def test_fetch_ignores_unsolicited_literal_with_other_uid(client):
    with imap.ImapSession(SETTINGS) as session:
        client.fetch_data = [
            (b"1 (UID 998 BODY[]<0> {4}", b"oops"),
            b")",
            (b"99 (UID 41 BODY[]<0> {4}", b"body"),
            b")",
        ]
        assert session._body(41, "", 0, 4) == b"body"


def test_literal_budget_rejects_server_claim_before_reading():
    class Reader:
        def read(self, size):
            pytest.fail("oversized literal was read")

    class Bounded(imap._BoundedLiterals, Reader):
        literal_budget = 32

    with pytest.raises(EmailError) as raised:
        Bounded().read(1024 * 1024 * 1024)
    assert raised.value.reason == "imap_response_too_large"


def test_move_prefers_uid_move_and_checkpoints_before_mutation(client):
    checkpoints = []
    with imap.ImapSession(SETTINGS) as session:
        result = session.move(
            41,
            "Trash",
            lambda phase, details: checkpoints.append((phase, list(client.calls))),
        )
    assert result == MoveResult(9, 81)
    assert checkpoints[0][0] == "before_move"
    assert all(call[0] != "MOVE" for call in checkpoints[0][1])
    assert not any(
        call[0] in ("COPY", "STORE", "EXPUNGE", "CLOSE") for call in client.calls
    )


def test_uidplus_fallback_only_expunges_target_uid(client):
    client.capability_value = b"IMAP4rev1 UIDPLUS"
    checkpoints = []
    with imap.ImapSession(SETTINGS) as session:
        assert session.move(
            41, "Trash", lambda phase, details: checkpoints.append((phase, details))
        ) == MoveResult(9, 81)
    assert [phase for phase, _ in checkpoints] == [
        "before_copy",
        "copied",
        "before_delete",
        "before_expunge",
    ]
    assert checkpoints[1][1]["uid"] == 81
    assert client.calls[2:-1] == [
        ("COPY", "41", b'"Trash"'),
        ("STORE", "41", "+FLAGS.SILENT", r"(\Deleted)"),
        ("EXPUNGE", "41"),
    ]


def test_no_uid_mapping_never_deletes_original(client):
    client.capability_value = b"IMAP4rev1 UIDPLUS"
    client.supply_mapping = False
    with imap.ImapSession(SETTINGS) as session:
        with pytest.raises(EmailError) as raised:
            session.move(41, "Trash")
    assert raised.value.reason == "imap_copy_mapping_missing"
    assert not any(call[0] in ("STORE", "EXPUNGE") for call in client.calls)


def test_unsupported_move_does_not_mutate(client):
    client.capability_value = b"IMAP4rev1"
    with imap.ImapSession(SETTINGS) as session:
        with pytest.raises(EmailError) as raised:
            session.move(41, "Trash")
    assert raised.value.reason == "imap_move_unsupported"
    assert not any(
        call[0] in ("COPY", "MOVE", "STORE", "EXPUNGE") for call in client.calls
    )


def test_failed_checkpoint_prevents_deletion(client):
    client.capability_value = b"IMAP4rev1 UIDPLUS"

    def checkpoint(phase, details):
        if phase == "copied":
            raise OSError("journal failed")

    with imap.ImapSession(SETTINGS) as session:
        with pytest.raises(OSError):
            session.move(41, "Trash", checkpoint)
    assert not any(call[0] in ("STORE", "EXPUNGE") for call in client.calls)


def test_append_and_exact_message_id_reconciliation(client):
    client.messages[42] = b"Message-ID: <sent@example.test>.wrong\r\n\r\nbody"
    with imap.ImapSession(SETTINGS) as session:
        assert session.append("Sent", b"message") == MoveResult(9, 82)
        assert session.find_message("Sent", "<sent@example.test>") == [41]
    assert ("SELECT", b'"Sent"', True) in client.calls


def test_set_seen_never_replaces_other_flags(client):
    with imap.ImapSession(SETTINGS) as session:
        session.set_seen(41, True)
        session.set_seen(41, False)
    assert client.calls[2:4] == [
        ("STORE", "41", "+FLAGS.SILENT", r"(\Seen)"),
        ("STORE", "41", "-FLAGS.SILENT", r"(\Seen)"),
    ]


def test_protocol_error_hides_server_response(client, monkeypatch):
    def bad(*args):
        raise imaplib.IMAP4.error("secret credentials and server response")

    monkeypatch.setattr(client, "uid", bad)
    with imap.ImapSession(SETTINGS) as session:
        with pytest.raises(EmailError) as raised:
            session.uids()
    assert "secret" not in str(raised.value)


class ScriptedSocket:
    """Exercise the real imaplib parser without making a network connection."""

    def __init__(self, responses):
        self.incoming = BytesIO(responses)
        self.outgoing = []

    def recv(self, size):
        return self.incoming.read(size)

    def makefile(self, mode):
        return self.incoming

    def sendall(self, data):
        self.outgoing.append(data)

    def shutdown(self, how):
        pass

    def close(self):
        pass


def test_real_imaplib_uid_fetch_and_move_responses(monkeypatch):
    wire = ScriptedSocket(
        b"* PREAUTH ready\r\n"
        b"* CAPABILITY IMAP4rev1 MOVE UIDPLUS\r\nT0 OK capabilities\r\n"
        b"* 1 EXISTS\r\n* OK [UIDVALIDITY 7] valid\r\nT1 OK selected\r\n"
        b"* 999 FETCH (BODY[]<0> {4}\r\nbody UID 41)\r\nT2 OK fetched\r\n"
        b"* OK [COPYUID 9 41 81] mapped\r\nT3 OK moved\r\n"
        b"* BYE farewell\r\nT4 OK logged out\r\n"
    )
    monkeypatch.setattr(imaplib, "Int2AP", lambda value: b"T")
    monkeypatch.setattr(imap._IMAP4, "_create_socket", lambda self, timeout: wire)
    session = imap.ImapSession(SETTINGS)
    session.client = imap._IMAP4("unused", timeout=30)
    session.capabilities = frozenset({"MOVE", "UIDPLUS"})
    assert session.select("Inbox") == 7
    assert session._body(41, "", 0, 4) == b"body"
    assert session.move(41, 'Trash "A"') == MoveResult(9, 81)
    session.__exit__(None, None, None)
    assert wire.outgoing == [
        b"T0 CAPABILITY\r\n",
        b'T1 SELECT "Inbox"\r\n',
        b"T2 UID FETCH 41 (UID BODY.PEEK[]<0.4>)\r\n",
        b'T3 UID MOVE 41 "Trash \\"A\\""\r\n',
        b"T4 LOGOUT\r\n",
    ]


def test_real_imaplib_rejects_announced_oversized_literal(monkeypatch):
    wire = ScriptedSocket(
        b"* PREAUTH ready\r\n"
        b"* CAPABILITY IMAP4rev1\r\nT0 OK capabilities\r\n"
        b"* 999 FETCH (UID 41 BODY[]<0> {1073741824}\r\n"
    )
    monkeypatch.setattr(imaplib, "Int2AP", lambda value: b"T")
    monkeypatch.setattr(imap._IMAP4, "_create_socket", lambda self, timeout: wire)
    session = imap.ImapSession(SETTINGS)
    session.client = imap._IMAP4("unused", timeout=30)
    session.client.state = "SELECTED"
    with pytest.raises(EmailError) as raised:
        session._body(41, "", 0, 4)
    assert raised.value.reason == "imap_response_too_large"
    session.client.shutdown()
