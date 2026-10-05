"""Thin Supabase client over plain HTTPS (requests): Auth, the table API, RPC.

Only the PUBLISHABLE key is ever used, together with the signed-in user's own
access token, so Postgres Row Level Security decides what each request may see.
The service_role key is deliberately not supported anywhere in this app.

Config comes from st.secrets / the environment (SUPABASE_URL, SUPABASE_KEY). It
is read through `config()`, which must be called on the main script thread --
worker threads receive plain values instead (see utils.applog).
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

import requests

_TIMEOUT = 12


class SupabaseError(Exception):
    """A failed Supabase call, with the status and (when given) the error code."""

    def __init__(self, message: str, status: Optional[int] = None, code: str = ""):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def _secret(name: str) -> Optional[str]:
    try:
        import streamlit as st

        value = st.secrets.get(name)
        if value:
            return str(value)
    except Exception:
        pass
    return os.environ.get(name)


def config() -> Tuple[Optional[str], Optional[str]]:
    """(project URL without trailing slash, publishable key) or (None, None)."""
    url, key = _secret("SUPABASE_URL"), _secret("SUPABASE_KEY")
    if not url or not key:
        return None, None
    return url.rstrip("/"), key


def is_configured() -> bool:
    return config()[0] is not None


def _headers(key: str, token: Optional[str], extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {token or key}",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def _error_from(response: requests.Response) -> SupabaseError:
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    message = (body.get("msg") or body.get("message") or body.get("error_description")
               or body.get("error") or response.text[:200] or f"HTTP {response.status_code}")
    code = str(body.get("error_code") or body.get("code") or "")
    return SupabaseError(str(message), response.status_code, code)


def _call(method: str, url: str, headers: Dict[str, str], *, params=None, payload=None) -> Any:
    try:
        response = requests.request(method, url, headers=headers, params=params,
                                    json=payload, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise SupabaseError("Couldn't reach the database. Check your connection and try again.",
                            None, "network") from exc
    if response.status_code >= 400:
        raise _error_from(response)
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def _need_config() -> Tuple[str, str]:
    url, key = config()
    if not url or not key:
        raise SupabaseError("The database isn't configured (SUPABASE_URL / SUPABASE_KEY).",
                            None, "not_configured")
    return url, key


# --- Auth --------------------------------------------------------------------

def sign_up(email: str, password: str) -> dict:
    url, key = _need_config()
    return _call("POST", f"{url}/auth/v1/signup", _headers(key, None),
                 payload={"email": email, "password": password}) or {}


def sign_in(email: str, password: str) -> dict:
    url, key = _need_config()
    return _call("POST", f"{url}/auth/v1/token", _headers(key, None),
                 params={"grant_type": "password"},
                 payload={"email": email, "password": password}) or {}


def refresh_session(refresh_token: str) -> dict:
    url, key = _need_config()
    return _call("POST", f"{url}/auth/v1/token", _headers(key, None),
                 params={"grant_type": "refresh_token"},
                 payload={"refresh_token": refresh_token}) or {}


def sign_out(access_token: str) -> None:
    url, key = _need_config()
    # scope=local: end only this device's session, not every device the user is on.
    _call("POST", f"{url}/auth/v1/logout", _headers(key, access_token), params={"scope": "local"})


# --- Tables + RPC ------------------------------------------------------------

def rest(method: str, table: str, token: str, *, params: Optional[dict] = None,
         payload: Any = None, prefer: str = "return=representation") -> Any:
    """One PostgREST call as the signed-in user. Returns parsed JSON (or None)."""
    url, key = _need_config()
    return _call(method, f"{url}/rest/v1/{table}", _headers(key, token, {"Prefer": prefer}),
                 params=params, payload=payload)


def rpc(name: str, token: Optional[str], payload: Optional[dict] = None) -> Any:
    url, key = _need_config()
    return _call("POST", f"{url}/rest/v1/rpc/{name}", _headers(key, token), payload=payload or {})


def post_rpc_raw(url: str, key: str, token: Optional[str], name: str, payload: dict) -> None:
    """Fire-and-forget RPC with already-resolved config (safe on worker threads)."""
    try:
        requests.post(f"{url}/rest/v1/rpc/{name}", headers=_headers(key, token),
                      json=payload, timeout=5)
    except requests.RequestException:
        pass
