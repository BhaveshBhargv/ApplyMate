"""Login / sign-up / session handling on top of Supabase Auth.

The session (access token, refresh token, user id, email) lives in
st.session_state only. To survive a browser refresh the refresh token is also
kept in a cookie (`am_rt`) that the app writes through a tiny hidden component
and reads back with st.context.cookies. Logging out revokes the session on the
server, clears the cookie and wipes the whole Streamlit session so nothing from
one user can ever show up for the next person on the same browser.
"""
from __future__ import annotations

import json
import time
from typing import Optional, Tuple

import streamlit as st
import streamlit.components.v1 as components

from utils import applog
from utils import supabase_client as sb
from utils.theme import section_picker
from utils.validators import is_valid_email

_AUTH_KEY = "auth"
_COOKIE = "am_rt"
_COOKIE_MAX_AGE = 14 * 24 * 3600
_PENDING_COOKIE = "_cookie_pending"
_RESTORE_TRIED = "_restore_tried"
_LOGGED_OUT = "_logged_out"
_MIN_PASSWORD = 8


# --- Session state -----------------------------------------------------------

def _session() -> Optional[dict]:
    return st.session_state.get(_AUTH_KEY)


def is_logged_in() -> bool:
    return bool(_session())


def current_user() -> Optional[dict]:
    """{'id', 'email'} of the signed-in user, or None."""
    s = _session()
    return {"id": s["user_id"], "email": s["email"]} if s else None


def peek_access_token() -> Optional[str]:
    """The current access token without refreshing it (used by logging)."""
    s = _session()
    return s["access_token"] if s else None


def _store(response: dict) -> bool:
    """Save a token response from Supabase. False if it carries no session."""
    token = response.get("access_token")
    user = response.get("user") or {}
    if not token or not user.get("id"):
        return False
    expires_at = response.get("expires_at") or (time.time() + int(response.get("expires_in", 3600)))
    st.session_state[_AUTH_KEY] = {
        "access_token": token,
        "refresh_token": response.get("refresh_token", ""),
        "expires_at": float(expires_at),
        "user_id": user["id"],
        "email": user.get("email", ""),
    }
    if response.get("refresh_token"):
        st.session_state[_PENDING_COOKIE] = ("set", response["refresh_token"])
    return True


def access_token() -> Optional[str]:
    """A valid access token, refreshing it when it's about to expire. None means
    the session is gone and the user must log in again."""
    s = _session()
    if not s:
        return None
    if s["expires_at"] - time.time() > 60:
        return s["access_token"]
    try:
        if _store(sb.refresh_session(s["refresh_token"])):
            return st.session_state[_AUTH_KEY]["access_token"]
    except sb.SupabaseError as exc:
        applog.log("session_refresh_failed", "auth", "warning", status=exc.status or 0)
    _drop_session()
    return None


def _drop_session() -> None:
    st.session_state.pop(_AUTH_KEY, None)
    st.session_state[_PENDING_COOKIE] = ("clear", None)


# --- Cookie persistence ------------------------------------------------------

def restore_from_cookie() -> bool:
    """Try to resume a session from the refresh-token cookie (once per session)."""
    if is_logged_in() or st.session_state.get(_RESTORE_TRIED) or st.session_state.get(_LOGGED_OUT):
        return is_logged_in()
    st.session_state[_RESTORE_TRIED] = True
    try:
        token = st.context.cookies.get(_COOKIE)
    except Exception:
        token = None
    if not token:
        return False
    try:
        if _store(sb.refresh_session(token)):
            applog.log("session_restored", "auth")
            return True
    except sb.SupabaseError:
        pass
    st.session_state[_PENDING_COOKIE] = ("clear", None)
    return False


def flush_cookie() -> None:
    """Write/clear the cookie if a change is pending. Renders a zero-height,
    off-flow component, so call it once per run from the entry script."""
    action = st.session_state.pop(_PENDING_COOKIE, None)
    if not action:
        return
    kind, token = action
    if kind == "set":
        value = json.dumps(token)
        script = (
            "const p = window.parent; const secure = p.location.protocol === 'https:' ? '; Secure' : '';"
            f"p.document.cookie = '{_COOKIE}=' + encodeURIComponent({value}) + "
            f"'; path=/; max-age={_COOKIE_MAX_AGE}; SameSite=Lax' + secure;"
        )
    else:
        script = f"window.parent.document.cookie = '{_COOKIE}=; path=/; max-age=0; SameSite=Lax';"
    with st.container(key="am_cookie"):
        components.html(f"<script>try {{ {script} }} catch (e) {{}}</script>", height=0)


# --- Actions -----------------------------------------------------------------

def _friendly(exc: sb.SupabaseError, *, signing_up: bool) -> str:
    code, text = exc.code, exc.message.lower()
    if code == "network":
        return exc.message
    if exc.status == 429 or "rate" in code:
        return "Too many attempts. Please wait a minute and try again."
    if code in ("user_already_exists", "email_exists") or "already registered" in text:
        return "An account with this email already exists. Log in instead."
    if code == "weak_password" or "password" in text and signing_up:
        return exc.message
    if code == "email_address_invalid" or "invalid email" in text:
        return "That email address doesn't look valid."
    if code == "signup_disabled":
        return "New sign-ups are switched off right now."
    if not signing_up and (code == "invalid_credentials" or exc.status in (400, 401)):
        return "Incorrect email or password."
    return "Something went wrong. Please try again."


def log_in(email: str, password: str) -> Tuple[bool, str]:
    try:
        ok = _store(sb.sign_in(email.strip().lower(), password))
    except sb.SupabaseError as exc:
        applog.log("login_failed", "auth", "warning", code=exc.code, status=exc.status or 0)
        return False, _friendly(exc, signing_up=False)
    if not ok:
        return False, "Login didn't return a session. Please try again."
    st.session_state.pop(_LOGGED_OUT, None)
    applog.log("login_success", "auth")
    return True, ""


def sign_up(email: str, password: str) -> Tuple[bool, str]:
    try:
        response = sb.sign_up(email.strip().lower(), password)
    except sb.SupabaseError as exc:
        applog.log("signup_failed", "auth", "warning", code=exc.code, status=exc.status or 0)
        return False, _friendly(exc, signing_up=True)
    if _store(response):
        st.session_state.pop(_LOGGED_OUT, None)
        applog.log("signup_success", "auth")
        return True, ""
    # Email confirmation is switched on in Supabase: the account exists but has no session yet.
    applog.log("signup_pending_confirmation", "auth")
    return False, "Account created. Check your email to confirm it, then log in."


def log_out() -> None:
    """End the session everywhere it lives, then start from a clean slate."""
    token = peek_access_token()
    applog.log("logout", "auth")
    if token:
        try:
            sb.sign_out(token)
        except sb.SupabaseError:
            pass
    st.session_state.clear()
    st.session_state[_LOGGED_OUT] = True
    st.session_state[_PENDING_COOKIE] = ("clear", None)


# --- UI ----------------------------------------------------------------------

_PRIVACY_NOTICE = """
**What we store.** Your email (for login), and the résumés, cover letters and saved jobs you choose to keep.

**How it's protected.** Your résumé content, cover letters, job notes and saved-job details are encrypted
before they reach the database, with a key unique to your account. People with access to the database
see only scrambled text.

**AI features.** When you use an AI feature (tailoring, cover letters, role suggestions, AI import), the relevant
résumé text is sent to our AI provider (OpenRouter) to produce the result. It is not stored by ApplyMate
beyond what you save.

**Your rights (UK GDPR).** You can download everything we hold about you, or permanently delete it,
at any time from **My Account**. Deleting your account removes your data and your encryption key.

**Logs.** We keep technical logs (event names, error types, no résumé content) to run and secure the service.
"""


def render_login_page() -> None:
    """The logged-out screen: log in or create an account."""
    st.markdown(
        '<div class="rb-header"><span class="rb-word">Apply<em>Mate</em></span>'
        '<span class="rb-tag">Résumé + Jobs</span></div><div class="rb-rule"></div>',
        unsafe_allow_html=True,
    )
    _l, mid, _r = st.columns([1, 1.6, 1])
    with mid:
        st.markdown(
            '<div class="rb-hero"><p class="rb-kicker">Welcome</p>'
            '<h1 class="rb-hero-title">Log in to ApplyMate</h1>'
            '<p class="rb-sub">Your résumés, cover letters and saved jobs, kept private and encrypted.</p></div>',
            unsafe_allow_html=True,
        )
        notice = st.session_state.pop("_login_notice", "")
        if notice:
            st.success(notice)

        mode = section_picker(["Log in", "Create account"], "login_mode")
        if mode == "Log in":
            with st.form("login_form"):
                email = st.text_input("Email", key="login_email", autocomplete="email")
                password = st.text_input("Password", type="password", key="login_password",
                                         autocomplete="current-password")
                submitted = st.form_submit_button("Log in", type="primary", width="stretch")
            if submitted:
                if not email.strip() or not password:
                    st.error("Enter your email and password.")
                else:
                    ok, message = log_in(email, password)
                    if ok:
                        st.rerun()
                    st.error(message)

        else:
            with st.form("signup_form"):
                email2 = st.text_input("Email", key="signup_email", autocomplete="email")
                pw1 = st.text_input(f"Password (at least {_MIN_PASSWORD} characters)", type="password",
                                    key="signup_password", autocomplete="new-password")
                pw2 = st.text_input("Confirm password", type="password", key="signup_password2",
                                    autocomplete="new-password")
                agree = st.checkbox("I've read the privacy notice below and agree to my data being "
                                    "stored and processed as described.", key="signup_agree")
                created = st.form_submit_button("Create account", type="primary", width="stretch")
            with st.expander("Privacy notice"):
                st.markdown(_PRIVACY_NOTICE)
            if created:
                if not is_valid_email(email2):
                    st.error("Enter a valid email address.")
                elif len(pw1) < _MIN_PASSWORD:
                    st.error(f"Your password needs at least {_MIN_PASSWORD} characters.")
                elif pw1 != pw2:
                    st.error("The two passwords don't match.")
                elif not agree:
                    st.error("Please tick the box to confirm you've read the privacy notice.")
                else:
                    ok, message = sign_up(email2, pw1)
                    if ok:
                        st.rerun()
                    if message.startswith("Account created"):
                        st.success(message)
                    else:
                        st.error(message)
