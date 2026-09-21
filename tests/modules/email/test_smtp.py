from dataclasses import replace
import smtplib
import ssl

import pytest

from demon_lucy.modules.email import smtp
from demon_lucy.modules.email.errors import EmailError
from demon_lucy.modules.email.models import CredentialProvider, Security, ServerSettings


SETTINGS = ServerSettings(
    "smtp.example.test",
    587,
    Security.STARTTLS,
    "alice",
    "LUCY_EMAIL_TEST_PASSWORD",
    30,
    CredentialProvider.ENVIRONMENT,
)


class FakeSMTP:
    def __init__(self):
        self.events = []
        self.refused = set()
        self.mail_code = 250
        self.data_code = 250
        self.data_error = None
        self.quit_error = None

    def ehlo(self):
        self.events.append("ehlo")
        return 250, b"hello"

    def starttls(self, context):
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname
        self.events.append("tls")

    def login(self, username, password):
        assert username == "alice"
        assert password == "private-password"
        self.events.append("login")

    def mail(self, sender):
        self.events.append("mail")
        return self.mail_code, b"private-server-message"

    def rcpt(self, recipient):
        self.events.append("rcpt")
        return (550 if recipient in self.refused else 250), b"private-server-message"

    def data(self, payload):
        self.events.append("data")
        if self.data_error:
            raise self.data_error
        return self.data_code, b"private-server-message"

    def quit(self):
        self.events.append("quit")
        if self.quit_error:
            raise self.quit_error

    def close(self):
        self.events.append("close")


@pytest.fixture
def client(monkeypatch):
    fake = FakeSMTP()
    monkeypatch.setenv(SETTINGS.password_env, "private-password")
    monkeypatch.setattr(smtp.smtplib, "SMTP", lambda *args, **kwargs: fake)
    return fake


def deliver(client, recipients=("bob@example.test",)):
    return smtp.send(
        SETTINGS,
        "alice@example.test",
        recipients,
        b"Subject: test\r\n\r\nbody",
        before_data=lambda: client.events.append("journal"),
    )


def test_starttls_and_durable_boundary_before_data(client):
    result = deliver(client)
    assert result.accepted == ("bob@example.test",)
    assert not result.refused
    assert client.events == [
        "ehlo",
        "tls",
        "ehlo",
        "login",
        "mail",
        "rcpt",
        "journal",
        "data",
        "quit",
    ]


def test_smtp_uses_selected_credential_provider_before_login(client, monkeypatch):
    settings = replace(SETTINGS, credential_provider=CredentialProvider.KEYRING)
    monkeypatch.delenv(SETTINGS.password_env, raising=False)

    def stored_password(value, protocol):
        assert value is settings and protocol == "smtp"
        return "private-password"

    monkeypatch.setattr(smtp, "password_for", stored_password)
    result = smtp.send(
        settings,
        "alice@example.test",
        ("bob@example.test",),
        b"message",
        before_data=lambda: None,
    )
    assert result.accepted == ("bob@example.test",)
    assert "login" in client.events


def test_partial_rcpt_rejections_are_returned_without_resending(client):
    client.refused.add("invalid@example.test")
    result = deliver(client, ("bob@example.test", "invalid@example.test"))
    assert result.accepted == ("bob@example.test",)
    assert result.refused == ("invalid@example.test",)
    assert client.events.count("data") == 1


def test_all_recipients_refused_never_sends_data(client):
    client.refused.add("bob@example.test")
    with pytest.raises(EmailError) as raised:
        deliver(client)
    assert raised.value.reason == "smtp_recipients_rejected"
    assert raised.value.refused == ("bob@example.test",)
    assert "journal" not in client.events
    assert "data" not in client.events
    assert "private-server" not in str(raised.value)


@pytest.mark.parametrize(
    "error",
    [
        smtplib.SMTPServerDisconnected("secret"),
        TimeoutError("secret"),
        ConnectionResetError("secret"),
    ],
)
def test_lost_data_reply_is_uncertain_and_never_retried(client, error):
    client.data_error = error
    with pytest.raises(EmailError) as raised:
        deliver(client)
    assert raised.value.delivery_uncertain
    assert not raised.value.retryable
    assert raised.value.accepted == ("bob@example.test",)
    assert "secret" not in str(raised.value)
    assert client.events.count("data") == 1


@pytest.mark.parametrize("code", [450, 550])
def test_data_rejection_is_definitive(client, code):
    client.data_code = code
    with pytest.raises(EmailError) as raised:
        deliver(client)
    assert raised.value.reason == "smtp_data_rejected"
    assert not raised.value.delivery_uncertain
    assert raised.value.retryable == (code == 450)


def test_initial_data_rejection_is_definitive(client):
    client.data_error = smtplib.SMTPDataError(554, b"secret")
    with pytest.raises(EmailError) as raised:
        deliver(client)
    assert raised.value.reason == "smtp_data_rejected"
    assert not raised.value.delivery_uncertain


def test_journal_failure_prevents_data(client):
    def journal():
        raise RuntimeError("journal unavailable")

    with pytest.raises(RuntimeError):
        smtp.send(
            SETTINGS,
            "alice@example.test",
            ("bob@example.test",),
            b"message",
            before_data=journal,
        )
    assert "data" not in client.events


def test_quit_failure_does_not_lose_acknowledged_delivery(client):
    client.quit_error = smtplib.SMTPServerDisconnected("secret")
    assert deliver(client).accepted == ("bob@example.test",)
    assert client.events[-1] == "close"


def test_tls_uses_verified_context(client, monkeypatch):
    def connect(*args, **kwargs):
        assert kwargs["context"].verify_mode == ssl.CERT_REQUIRED
        assert kwargs["context"].check_hostname
        assert kwargs["timeout"] == 30
        return client

    monkeypatch.setattr(smtp.smtplib, "SMTP_SSL", connect)
    smtp.send(
        replace(SETTINGS, security=Security.TLS),
        "alice@example.test",
        ("bob@example.test",),
        b"message",
        before_data=lambda: None,
    )
    assert "tls" not in client.events


def test_missing_credentials_never_connects(monkeypatch):
    monkeypatch.delenv(SETTINGS.password_env, raising=False)
    monkeypatch.setattr(
        smtp.smtplib, "SMTP", lambda *args, **kwargs: pytest.fail("network used")
    )
    with pytest.raises(EmailError, match="environment variable"):
        smtp.send(
            SETTINGS,
            "alice@example.test",
            ("bob@example.test",),
            b"message",
            before_data=lambda: None,
        )


def test_header_injection_rejected_before_network(client):
    with pytest.raises(EmailError) as raised:
        deliver(client, ("bob@example.test\r\nRCPT TO:other@example.test",))
    assert raised.value.reason == "recipients_invalid"
    assert client.events == []
