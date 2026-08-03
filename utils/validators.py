"""Lightweight, dependency-free validators for resume form fields.

These are intentionally simple pattern checks (no external validation
library) since the goal is to catch obviously malformed input before it
lands in the resume model, not to fully verify deliverability.
"""
import re
from urllib.parse import urlparse

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^\+\d{8,15}$")


def is_valid_email(email: str) -> bool:
    """True if email looks like name@domain.tld."""
    return bool(_EMAIL_RE.fullmatch(email.strip()))


def is_valid_phone(phone: str) -> bool:
    """True if phone includes a country code, e.g. '+1 555-123-4567'.

    Formatting characters (spaces, dots, dashes, parentheses) are ignored;
    what remains must be a '+' followed by 8-15 digits (E.164-style).
    """
    stripped = re.sub(r"[\s().-]", "", phone.strip())
    return bool(_PHONE_RE.fullmatch(stripped))


def normalize_url(url: str) -> str:
    """Prepend https:// if the user omitted a scheme, e.g. 'linkedin.com/in/x'."""
    url = url.strip()
    if url and not re.match(r"^https?://", url, re.IGNORECASE):
        url = f"https://{url}"
    return url


def is_valid_url(url: str) -> bool:
    """True for an empty string (URL fields are optional) or a well-formed http(s) URL.

    Callers should pass the URL through normalize_url() first so a
    scheme-less domain like 'github.com/jordan' is validated correctly.
    """
    if not url:
        return True
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc) and "." in parsed.netloc
