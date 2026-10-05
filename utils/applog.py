"""Application logging into the `app_logs` table (via the log_event() database function).

Rules this module enforces so the log can't become a leak:
  * only event names and small scalar details are accepted -- no free-form
    objects, and strings are cut to 120 characters;
  * logging NEVER raises and never blocks the page (a daemon thread posts it);
  * the user id is attached by the database from the login token, not by us.

Callers must still not pass personal data (names, emails, résumé text) as details.
"""
from __future__ import annotations

import threading
import uuid
from typing import Any, Dict

import streamlit as st

from utils import supabase_client as sb

_SESSION_KEY = "_log_session_id"
_MAX_STR = 120
_MAX_KEYS = 12


def _session_id() -> str:
    try:
        if _SESSION_KEY not in st.session_state:
            st.session_state[_SESSION_KEY] = uuid.uuid4().hex[:16]
        return st.session_state[_SESSION_KEY]
    except Exception:
        return ""


def _clean(details: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in list(details.items())[:_MAX_KEYS]:
        if isinstance(value, (bool, int, float)) or value is None:
            out[str(key)[:40]] = value
        else:
            out[str(key)[:40]] = str(value)[:_MAX_STR]
    return out


def log(event: str, category: str = "app", level: str = "info", **details: Any) -> None:
    """Record one event. Safe to call anywhere on the main script thread."""
    try:
        url, key = sb.config()
        if not url or not key:
            return
        from utils import auth  # lazy: auth imports this module

        token = auth.peek_access_token()
        payload = {
            "p_level": level,
            "p_category": category,
            "p_event": event,
            "p_details": _clean(details),
            "p_session": _session_id(),
        }
        threading.Thread(
            target=sb.post_rpc_raw, args=(url, key, token, "log_event", payload), daemon=True
        ).start()
    except Exception:
        pass
