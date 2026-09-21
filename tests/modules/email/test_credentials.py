from dataclasses import replace
import getpass
from types import SimpleNamespace
import warnings

import pytest

from demon_lucy.modules.email import credentials
from demon_lucy.modules.email.errors import EmailError
from demon_lucy.modules.email.models import (
    AccountConfig,
    CredentialProvider,
    Security,
    SentCopyMode,
    ServerSettings,
)


SETTINGS = ServerSettings(
    "Mail.Example.TEST.",
    993,
    Security.TLS,
    "alice",
    "LUCY_EMAIL_TEST_PASSWORD",
    30,
    CredentialProvider.KEYRING,
)


class MemoryBackend:
    def __init__(self):
        self.calls = []
        self.saved = {}
        self.error = None

    def get_password(self, service, username):
        self.calls.append(("get", service, username))
        if self.error:
            raise self.error
        return self.saved.get((service, username))

    def set_password(self, service, username, password):
        if self.error:
            raise self.error
        self.calls.append(("set", service, username))
        self.saved[service, username] = password


class SecretService(MemoryBackend):
    pass


class KWallet(MemoryBackend):
    pass


class KWallet4(MemoryBackend):
    pass


class Chain(MemoryBackend):
    def __init__(self, backends):
        super().__init__()
        self.backends = backends


@pytest.fixture
def backend(monkeypatch):
    selected = SimpleNamespace(current=SecretService())
    modules = {
        "keyring": SimpleNamespace(get_keyring=lambda: selected.current),
        "keyring.backends.SecretService": SimpleNamespace(Keyring=SecretService),
        "keyring.backends.kwallet": SimpleNamespace(
            DBusKeyring=KWallet, DBusKeyringKWallet4=KWallet4
        ),
        "keyring.backends.chainer": SimpleNamespace(ChainerBackend=Chain),
    }

    def imported(name):
        assert name in modules, f"Unexpected import: {name}"
        return modules[name]

    monkeypatch.setattr(credentials.importlib, "import_module", imported)
    monkeypatch.setattr(credentials.sys, "platform", "linux")
    monkeypatch.setattr(credentials.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    selected.modules = modules
    return selected


def config():
    smtp = replace(
        SETTINGS, host="smtp.example.test", port=587, security=Security.STARTTLS
    )
    return AccountConfig(
        "/unused/email",
        "alice@example.test",
        SETTINGS,
        smtp,
        {},
        100,
        1024,
        SentCopyMode.SERVER,
    )


@pytest.mark.parametrize("backend_type", [SecretService, KWallet, KWallet4])
def test_only_supported_os_backends_are_used(backend, backend_type):
    backend.current = backend_type()
    backend.current.saved[
        "demon-lucy.email:imap:mail.example.test:993:tls", "alice"
    ] = "synthetic-test-password"
    assert credentials.password_for(SETTINGS, "imap") == "synthetic-test-password"
    assert backend.current.calls == [
        ("get", "demon-lucy.email:imap:mail.example.test:993:tls", "alice")
    ]


def test_chainer_calls_only_approved_child_without_fallback(backend):
    unsupported = MemoryBackend()
    secure = SecretService()
    secure.saved["demon-lucy.email:imap:mail.example.test:993:tls", "alice"] = (
        "synthetic-test-password"
    )
    chain = Chain([unsupported, secure])
    backend.current = chain
    assert credentials.password_for(SETTINGS, "imap") == "synthetic-test-password"
    assert unsupported.calls == [] and chain.calls == []
    assert len(secure.calls) == 1


def test_locked_chain_child_never_tries_other_backend_or_environment(
    backend, monkeypatch
):
    locked = SecretService()
    locked.error = RuntimeError("synthetic-password-must-not-appear")
    fallback = KWallet()
    fallback.saved["demon-lucy.email:imap:mail.example.test:993:tls", "alice"] = (
        "wrong-store-secret"
    )
    backend.current = Chain([locked, fallback])
    monkeypatch.setenv(SETTINGS.password_env, "wrong-environment-secret")
    with pytest.raises(EmailError) as raised:
        credentials.password_for(SETTINGS, "imap")
    assert raised.value.reason == "credentials_keyring_unavailable"
    assert "synthetic-password" not in str(raised.value)
    assert fallback.calls == []


@pytest.mark.parametrize(
    "kind", ["unsupported", "empty_chain", "unsupported_chain", "subclass"]
)
def test_insecure_and_unknown_backends_are_rejected(backend, kind):
    class UnexpectedSubclass(SecretService):
        pass

    backend.current = {
        "unsupported": MemoryBackend(),
        "empty_chain": Chain([]),
        "unsupported_chain": Chain([MemoryBackend()]),
        "subclass": UnexpectedSubclass(),
    }[kind]
    with pytest.raises(EmailError) as raised:
        credentials.password_for(SETTINGS, "imap")
    assert raised.value.reason == "credentials_backend_unsupported"
    assert backend.current.calls == []


def test_missing_saved_secret_never_falls_back_to_environment(backend, monkeypatch):
    monkeypatch.setenv(SETTINGS.password_env, "environment-must-not-be-used")
    with pytest.raises(EmailError) as raised:
        credentials.password_for(SETTINGS, "imap")
    assert raised.value.reason == "credentials_missing"
    assert "--email-credentials-save" in str(raised.value)


def test_backend_initialization_error_is_redacted(backend):
    def fail():
        raise RuntimeError("synthetic-initialization-secret")

    backend.modules["keyring"].get_keyring = fail
    with pytest.raises(EmailError) as raised:
        credentials.password_for(SETTINGS, "imap")
    assert raised.value.reason == "credentials_keyring_unavailable"
    assert "synthetic-initialization-secret" not in str(raised.value)
    assert raised.value.__suppress_context__


def test_missing_keyring_has_actionable_optional_dependency_error(monkeypatch):
    monkeypatch.setattr(credentials.sys, "platform", "linux")

    def missing(name):
        raise ModuleNotFoundError("synthetic-package-error")

    monkeypatch.setattr(credentials.importlib, "import_module", missing)
    with pytest.raises(EmailError) as raised:
        credentials.password_for(SETTINGS, "imap")
    assert raised.value.reason == "credentials_dependency_missing"
    assert "modules/email/requirements.txt" in str(raised.value)
    assert "synthetic-package-error" not in str(raised.value)


def test_explicit_environment_provider_never_imports_keyring(monkeypatch):
    monkeypatch.setenv(SETTINGS.password_env, "explicit-environment-secret")
    monkeypatch.setattr(
        credentials.importlib,
        "import_module",
        lambda name: pytest.fail("keyring imported"),
    )
    settings = replace(SETTINGS, credential_provider=CredentialProvider.ENVIRONMENT)
    assert credentials.password_for(settings, "imap") == "explicit-environment-secret"


def test_environment_provider_requires_valid_named_variable(monkeypatch):
    settings = replace(SETTINGS, credential_provider=CredentialProvider.ENVIRONMENT)
    monkeypatch.delenv(SETTINGS.password_env, raising=False)
    with pytest.raises(EmailError) as raised:
        credentials.password_for(settings, "imap")
    assert raised.value.reason == "credentials_missing"
    with pytest.raises(EmailError) as raised:
        credentials.password_for(
            replace(settings, password_env="BAD\nVARIABLE"), "imap"
        )
    assert raised.value.reason == "credentials_invalid"


def test_credentials_are_scoped_by_protocol_host_port_security_and_username(backend):
    backend.current.saved[
        "demon-lucy.email:imap:mail.example.test:993:tls", "alice"
    ] = "only-for-this-service"
    mismatches = [
        (SETTINGS, "smtp"),
        (replace(SETTINGS, host="other.example.test"), "imap"),
        (replace(SETTINGS, port=143), "imap"),
        (replace(SETTINGS, security=Security.STARTTLS), "imap"),
        (replace(SETTINGS, username="bob"), "imap"),
    ]
    for settings, protocol in mismatches:
        with pytest.raises(EmailError) as raised:
            credentials.password_for(settings, protocol)
        assert raised.value.reason == "credentials_missing"
    assert (
        credentials.password_for(replace(SETTINGS, host="mail.example.test"), "imap")
        == "only-for-this-service"
    )


def test_service_normalizes_international_hostname(backend):
    settings = replace(SETTINGS, host="ПРИМЕР.РФ.")
    backend.current.saved[
        "demon-lucy.email:imap:xn--e1afmkfd.xn--p1ai:993:tls", "alice"
    ] = "idna-password"
    assert credentials.password_for(settings, "imap") == "idna-password"


def test_save_prompts_both_passwords_before_writing(backend, monkeypatch, capsys):
    answers = iter(["synthetic-imap-secret", "synthetic-smtp-secret"])
    prompts = []

    def prompt(message, *, stream):
        assert stream is credentials.sys.stderr
        assert backend.current.calls == []
        prompts.append(message)
        return next(answers)

    monkeypatch.setattr(credentials.getpass, "getpass", prompt)
    credentials.save_credentials(config())
    assert prompts == ["IMAP password: ", "SMTP password: "]
    assert backend.current.saved == {
        (
            "demon-lucy.email:imap:mail.example.test:993:tls",
            "alice",
        ): "synthetic-imap-secret",
        (
            "demon-lucy.email:smtp:smtp.example.test:587:starttls",
            "alice",
        ): "synthetic-smtp-secret",
    }
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_save_refuses_noninteractive_input_before_prompt(backend, monkeypatch):
    monkeypatch.setattr(credentials.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr(
        credentials.getpass,
        "getpass",
        lambda *args, **kwargs: pytest.fail("password prompt used"),
    )
    with pytest.raises(EmailError) as raised:
        credentials.save_credentials(config())
    assert raised.value.reason == "credentials_prompt_unavailable"
    assert backend.current.calls == []


def test_getpass_echo_warning_aborts_before_fallback_can_read(backend, monkeypatch):
    def unsafe_prompt(*args, **kwargs):
        warnings.warn("Password echo unavailable", getpass.GetPassWarning)
        pytest.fail("getpass fallback would echo password")

    monkeypatch.setattr(credentials.getpass, "getpass", unsafe_prompt)
    with pytest.raises(EmailError) as raised:
        credentials.save_credentials(config())
    assert raised.value.reason == "credentials_prompt_unavailable"
    assert backend.current.calls == []


@pytest.mark.parametrize("answer", ["", "with\nnewline", "with\x00nul"])
def test_invalid_second_password_writes_neither_secret(backend, monkeypatch, answer):
    answers = iter(["valid-first-password", answer])
    monkeypatch.setattr(
        credentials.getpass, "getpass", lambda *args, **kwargs: next(answers)
    )
    with pytest.raises(EmailError) as raised:
        credentials.save_credentials(config())
    assert raised.value.reason == "credentials_invalid"
    assert backend.current.calls == []


def test_cancelled_second_prompt_writes_neither_secret(backend, monkeypatch):
    prompts = 0

    def prompt(*args, **kwargs):
        nonlocal prompts
        prompts += 1
        if prompts == 2:
            raise KeyboardInterrupt
        return "valid-first-password"

    monkeypatch.setattr(credentials.getpass, "getpass", prompt)
    with pytest.raises(KeyboardInterrupt):
        credentials.save_credentials(config())
    assert backend.current.calls == []


def test_save_failure_is_redacted_without_logging_password(
    backend, monkeypatch, capsys, caplog
):
    backend.current.error = RuntimeError("synthetic-imap-secret synthetic-smtp-secret")
    monkeypatch.setattr(
        credentials.getpass, "getpass", lambda *args, **kwargs: "synthetic-imap-secret"
    )
    with pytest.raises(EmailError) as raised:
        credentials.save_credentials(config())
    assert raised.value.reason == "credentials_keyring_unavailable"
    assert "synthetic-imap-secret" not in str(raised.value)
    assert raised.value.__suppress_context__
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "" and caplog.text == ""


def test_save_requires_explicit_keyring_configuration(backend, monkeypatch):
    configured = config()
    configured = replace(
        configured,
        smtp=replace(
            configured.smtp, credential_provider=CredentialProvider.ENVIRONMENT
        ),
    )
    monkeypatch.setattr(
        credentials.getpass,
        "getpass",
        lambda *args, **kwargs: pytest.fail("prompt used"),
    )
    with pytest.raises(EmailError) as raised:
        credentials.save_credentials(configured)
    assert raised.value.reason == "credentials_provider_mismatch"
    assert backend.current.calls == []


def test_unsupported_platform_does_not_import_or_fallback(monkeypatch):
    monkeypatch.setattr(credentials.sys, "platform", "win32")
    monkeypatch.setattr(
        credentials.importlib,
        "import_module",
        lambda name: pytest.fail("keyring imported"),
    )
    monkeypatch.setenv(SETTINGS.password_env, "must-not-fallback")
    with pytest.raises(EmailError) as raised:
        credentials.password_for(SETTINGS, "imap")
    assert raised.value.reason == "credentials_keyring_unavailable"
