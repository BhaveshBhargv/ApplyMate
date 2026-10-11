"""One-time email codes, generated and checked by ApplyMate itself.

A code is created here, emailed by utils.mailer, and checked here. Only an HMAC of it is
kept, in the user's own server-side Streamlit session -- never in the browser and never in
the database -- so there is nothing for a user to read or tamper with. Rules:

  * 6 digits from a cryptographic RNG, valid for 10 minutes, single use;
  * at most 5 wrong guesses per code, then it's dead (a new one must be requested);
  * one email per address per minute from a session, and a cap per address per hour across
    ALL sessions (a process-wide counter), so the form can't be used to spam someone.

No Streamlit calls outside `st.session_state`/`st.cache_resource`; pure otherwise.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from typing import Dict, List

import streamlit as st

CODE_TTL_SECONDS = 600
MAX_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60
MAX_EMAILS_PER_ADDRESS_PER_HOUR = 5

OK, WRONG, EXPIRED, LOCKED, NONE = "ok", "wrong", "expired", "locked", "none"


@st.cache_resource
def _sends() -> tuple:
    """Process-wide {address: [send times]} and its lock (shared by every session)."""
    return {}, threading.Lock()


def _hmac_key() -> bytes:
    from utils import db

    raw = db._secret("OTP_SECRET") or db._secret("ENCRYPTION_MASTER_KEY") or "applymate-dev-otp"
    return hashlib.sha256(b"applymate-otp:" + raw.encode("utf-8")).digest()


def _digest(purpose: str, email: str, code: str) -> str:
    return hmac.new(_hmac_key(), f"{purpose}|{email}|{code}".encode("utf-8"), hashlib.sha256).hexdigest()


def _slot(purpose: str) -> str:
    return f"_otp_{purpose}"


def allow_send(email: str, now: float | None = None) -> bool:
    """Record an email to `email` unless it has hit the hourly cap (all sessions)."""
    now = time.time() if now is None else now
    sends, lock = _sends()
    with lock:
        recent: List[float] = [t for t in sends.get(email, []) if now - t < 3600]
        if len(recent) >= MAX_EMAILS_PER_ADDRESS_PER_HOUR:
            sends[email] = recent
            return False
        recent.append(now)
        sends[email] = recent
        if len(sends) > 5000:   # don't grow forever
            for key in [k for k, v in sends.items() if not v or now - v[-1] > 3600]:
                sends.pop(key, None)
        return True


def seconds_until_resend(purpose: str) -> int:
    entry = st.session_state.get(_slot(purpose))
    if not entry:
        return 0
    return max(0, int(RESEND_COOLDOWN_SECONDS - (time.time() - entry["issued"])))


def issue(purpose: str, email: str) -> str:
    """Create a fresh code for (purpose, email) -- replacing any earlier one -- and return it
    so the caller can email it. The plain code is not stored."""
    code = f"{secrets.randbelow(10 ** 6):06d}"
    st.session_state[_slot(purpose)] = {
        "email": email, "digest": _digest(purpose, email, code),
        "issued": time.time(), "expires": time.time() + CODE_TTL_SECONDS, "attempts": 0,
    }
    return code


def check(purpose: str, email: str, code: str) -> str:
    """Compare a typed code with the one issued. Returns OK, WRONG, EXPIRED, LOCKED or NONE.
    A correct code is consumed (single use)."""
    entry = st.session_state.get(_slot(purpose))
    if not entry or entry["email"] != email:
        return NONE
    if time.time() > entry["expires"]:
        st.session_state.pop(_slot(purpose), None)
        return EXPIRED
    if entry["attempts"] >= MAX_ATTEMPTS:
        return LOCKED
    entry["attempts"] += 1
    if hmac.compare_digest(entry["digest"], _digest(purpose, email, code.strip().replace(" ", ""))):
        st.session_state.pop(_slot(purpose), None)
        return OK
    if entry["attempts"] >= MAX_ATTEMPTS:
        return LOCKED
    return WRONG


def clear(purpose: str) -> None:
    st.session_state.pop(_slot(purpose), None)


def explain(result: str) -> str:
    return {
        WRONG: "That code isn't right. Check it and try again.",
        EXPIRED: "That code has expired. Ask for a new one.",
        LOCKED: "Too many wrong attempts. Ask for a new code.",
        NONE: "Ask for a code first.",
    }.get(result, "That code didn't work.")
