"""Sends ApplyMate's own emails (verification and password-reset codes) over SMTP.

Configured with secrets / environment variables:

    SMTP_HOST        e.g. smtp.gmail.com, smtp.resend.com, smtp-relay.brevo.com
    SMTP_PORT        587 (STARTTLS, default) or 465 (implicit SSL)
    SMTP_SECURITY    starttls | ssl | none -- optional; defaults from the port (none is for local testing)
    SMTP_USER        login for the SMTP provider
    SMTP_PASSWORD    its password / API key (an "app password" for Gmail)
    SMTP_FROM        the From address, e.g. "ApplyMate <no-reply@yourdomain.com>"
                     (defaults to SMTP_USER)

Any provider that speaks SMTP works. Nothing in here logs addresses or message bodies.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from typing import Optional

_TIMEOUT = 15


class MailError(Exception):
    """The email couldn't be sent (not configured, refused, or unreachable)."""


def _secret(name: str) -> Optional[str]:
    try:
        import streamlit as st

        value = st.secrets.get(name)
        if value:
            return str(value)
    except Exception:
        pass
    return os.environ.get(name)


def _host() -> Optional[str]:
    """SMTP_HOST, or Gmail's server when the login is a Gmail address and no host is set."""
    host = _secret("SMTP_HOST")
    if not host and (_secret("SMTP_USER") or "").lower().endswith(("@gmail.com", "@googlemail.com")):
        return "smtp.gmail.com"
    return host


def is_configured() -> bool:
    """True when there's a server, a sender, and -- if a login is set -- its password
    (so a half-configured Gmail account reports "not set up" instead of failing to send)."""
    has_login = bool(_secret("SMTP_USER"))
    return bool(_host() and (_secret("SMTP_FROM") or _secret("SMTP_USER"))
                and (not has_login or _secret("SMTP_PASSWORD")))


def send_email(to: str, subject: str, text: str, html: Optional[str] = None) -> None:
    """Send one email. Raises MailError on any failure."""
    host = _host()
    sender = _secret("SMTP_FROM") or _secret("SMTP_USER")
    if not host or not sender:
        raise MailError("Email isn't configured (SMTP_HOST / SMTP_FROM).")
    if any(c in to for c in "\r\n") or "@" not in to:
        raise MailError("Invalid recipient.")
    try:
        port = int(_secret("SMTP_PORT") or 587)
    except ValueError as exc:
        raise MailError("SMTP_PORT must be a number.") from exc
    user, password = _secret("SMTP_USER"), _secret("SMTP_PASSWORD")

    name, address = parseaddr(sender)
    message = EmailMessage()
    message["From"] = formataddr((name or "ApplyMate", address))
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")

    security = (_secret("SMTP_SECURITY") or ("ssl" if port == 465 else "starttls")).lower()
    context = ssl.create_default_context()
    try:
        if security == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=_TIMEOUT, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=_TIMEOUT)
        with server:
            if security == "starttls":
                server.starttls(context=context)
            if user and password:
                server.login(user, password)
            server.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError("The email service didn't accept the message.") from exc


# --- The two messages ApplyMate sends -------------------------------------------

def _code_html(heading: str, intro: str, code: str, minutes: int) -> str:
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;max-width:480px;margin:auto;color:#18181B">'
        f'<h2 style="margin:0 0 12px">{heading}</h2><p>{intro}</p>'
        f'<p style="font-size:32px;font-weight:700;letter-spacing:6px;background:#F4F4F5;'
        f'padding:14px 18px;border-radius:10px;text-align:center">{code}</p>'
        f'<p style="color:#71717A;font-size:13px">This code expires in {minutes} minutes. '
        "If you didn't ask for it, you can ignore this email.</p></div>"
    )


def send_signup_code(to: str, code: str, minutes: int) -> None:
    send_email(
        to, "Your ApplyMate verification code",
        f"Your ApplyMate verification code is {code}\n\nIt expires in {minutes} minutes. "
        "If you didn't create an account, ignore this email.",
        _code_html("Verify your email", "Enter this code on the sign-up screen to finish creating your ApplyMate account:",
                   code, minutes),
    )


def send_reset_code(to: str, code: str, minutes: int) -> None:
    send_email(
        to, "Your ApplyMate password reset code",
        f"Your ApplyMate password reset code is {code}\n\nIt expires in {minutes} minutes. "
        "If you didn't ask for this, ignore this email -- your password has not been changed.",
        _code_html("Reset your password", "Enter this code on the Forgot password screen to choose a new password:",
                   code, minutes),
    )
