"""Authenticated SMTP delivery with an explicit durable DATA boundary."""

from __future__ import annotations

import smtplib
import ssl
from collections.abc import Callable

from demon_lucy.modules.email.errors import EmailError
from demon_lucy.modules.email.credentials import password_for
from demon_lucy.modules.email.models import DeliveryResult, Security, ServerSettings


def send(
    settings: ServerSettings,
    sender: str,
    recipients: tuple[str, ...],
    payload: bytes,
    *,
    before_data: Callable[[], None],
) -> DeliveryResult:
    """Send once, recording the delivery boundary before transmitting DATA.

    A lost connection during DATA is ambiguous even if no final reply arrived.
    The caller must never automatically retry that draft generation.
    """
    password = password_for(settings, "smtp")
    if not recipients or any(
        not address
        or not address.isascii()
        or any(ord(char) < 32 or ord(char) == 127 for char in address)
        for address in (sender, *recipients)
    ):
        raise EmailError(
            "SMTP requires valid ASCII envelope addresses.", reason="recipients_invalid"
        )
    if any(char in settings.username for char in "\r\n\x00"):
        raise EmailError("Invalid SMTP username.", reason="credentials_invalid")
    if settings.security not in (Security.TLS, Security.STARTTLS):
        raise EmailError("SMTP requires TLS or STARTTLS.", reason="security_invalid")

    client = None
    accepted: list[str] = []
    refused: list[str] = []
    in_data = False
    try:
        context = ssl.create_default_context()
        if settings.security is Security.TLS:
            client = smtplib.SMTP_SSL(
                settings.host,
                settings.port,
                timeout=settings.timeout_seconds,
                context=context,
            )
        else:
            client = smtplib.SMTP(
                settings.host, settings.port, timeout=settings.timeout_seconds
            )
        code, _ = client.ehlo()
        if code != 250:
            raise EmailError(
                "SMTP greeting was rejected.",
                reason="smtp_greeting_failed",
                retryable=400 <= code < 500,
            )
        if settings.security is Security.STARTTLS:
            client.starttls(context=context)
            code, _ = client.ehlo()
            if code != 250:
                raise EmailError(
                    "SMTP greeting was rejected.",
                    reason="smtp_greeting_failed",
                    retryable=400 <= code < 500,
                )
        client.login(settings.username, password)
        code, _ = client.mail(sender)
        if code != 250:
            raise EmailError(
                "SMTP rejected the sender.",
                reason="smtp_sender_rejected",
                retryable=400 <= code < 500,
            )
        for recipient in recipients:
            code, _ = client.rcpt(recipient)
            (accepted if code in (250, 251, 252) else refused).append(recipient)
        if not accepted:
            raise EmailError(
                "SMTP rejected every recipient.",
                reason="smtp_recipients_rejected",
                refused=tuple(refused),
            )
        before_data()
        in_data = True
        code, _ = client.data(payload)
        in_data = False
        if not 200 <= code < 300:
            raise EmailError(
                "SMTP rejected the message.",
                reason="smtp_data_rejected",
                retryable=400 <= code < 500,
                accepted=tuple(accepted),
                refused=tuple(refused),
            )
        return DeliveryResult(accepted=tuple(accepted), refused=tuple(refused))
    except smtplib.SMTPAuthenticationError:
        raise EmailError(
            "SMTP authentication failed.", reason="smtp_authentication_failed"
        ) from None
    except UnicodeError:
        raise EmailError(
            "SMTP credentials use an unsupported encoding.",
            reason="credentials_invalid",
        ) from None
    except smtplib.SMTPDataError as error:
        # A protocol rejection is definitive; only a lost reply is uncertain.
        raise EmailError(
            "SMTP rejected the message.",
            reason="smtp_data_rejected",
            retryable=400 <= error.smtp_code < 500,
            accepted=tuple(accepted),
            refused=tuple(refused),
        ) from None
    except ssl.SSLCertVerificationError:
        raise EmailError(
            "SMTP TLS certificate verification failed.",
            reason="smtp_certificate_invalid",
        ) from None
    except (OSError, smtplib.SMTPException):
        raise EmailError(
            "SMTP delivery is uncertain; check the server before creating another draft."
            if in_data
            else "SMTP connection or protocol failed.",
            reason="smtp_delivery_uncertain" if in_data else "smtp_unavailable",
            retryable=not in_data,
            delivery_uncertain=in_data,
            accepted=tuple(accepted),
            refused=tuple(refused),
        ) from None
    finally:
        if client is not None:
            try:
                client.quit()
            except (OSError, smtplib.SMTPException):
                # QUIT failure cannot undo an acknowledged DATA transaction.
                try:
                    client.close()
                except OSError:
                    pass
