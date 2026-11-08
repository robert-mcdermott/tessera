"""Sending email: digests of pending work (CUR-08), through the SMTP server in ``[mail]``.

Tessera sends plain-text messages only. The password comes from the environment variable
named by ``mail.password_env``, never from the configuration file.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from tessera.config import MailConfig
from tessera.core.errors import TesseraError


class MailError(TesseraError):
    pass


def message(mail: MailConfig, to: str, subject: str, text: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = mail.sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=mail.sender.rsplit("@", 1)[-1].rstrip(">") or None)
    msg["Auto-Submitted"] = "auto-generated"  # RFC 3834: no auto-replies, please
    msg.set_content(text)
    return msg


def send(mail: MailConfig, messages: list[EmailMessage]) -> int:
    """Send messages over one connection; returns how many were sent."""
    if not mail.host:
        raise MailError("No mail server: set [mail] host (and sender) to send email.")
    password = os.environ.get(mail.password_env, "") if mail.password_env else ""
    context = ssl.create_default_context()
    try:
        server: smtplib.SMTP = (
            smtplib.SMTP_SSL(mail.host, mail.port, context=context, timeout=30)
            if mail.ssl
            else smtplib.SMTP(mail.host, mail.port, timeout=30)
        )
        with server:
            if mail.starttls and not mail.ssl:
                server.starttls(context=context)
            if mail.username:
                server.login(mail.username, password)
            for msg in messages:
                server.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        raise MailError(f"Could not send email through {mail.host}:{mail.port}: {exc}") from exc
    return len(messages)


__all__ = ["MailError", "message", "send"]
