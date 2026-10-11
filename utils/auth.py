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

from utils import applog, mailer, otp
from utils import supabase_admin as admin
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
_VERIFY_EMAIL = "_verify_email"      # address waiting for its sign-up code
_VERIFY_PASSWORD = "_verify_password"  # held in server memory only until the code is entered
_RESET_EMAIL = "_reset_email"        # address a password-reset code was sent to
_MINUTES = otp.CODE_TTL_SECONDS // 60


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
    if code in ("user_already_exists", "email_exists") or "already registered" in text \
            or "already been registered" in text:
        return "An account with this email already exists. Log in instead."
    if code == "weak_password" or "password" in text and signing_up:
        return exc.message
    if code == "same_password":
        return "Choose a password you haven't used before."
    if code == "email_address_invalid" or "invalid email" in text:
        return "That email address doesn't look valid."
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


def email_ready() -> bool:
    """True when the app can send codes and create/administer accounts."""
    return mailer.is_configured() and admin.is_configured()


_NOT_READY = ("Email verification isn't set up on this server yet, so accounts can't be created "
              "or reset right now. Please try again later.")


def _email_code(purpose: str, email: str) -> Tuple[bool, str]:
    """Issue a code and email it. Returns (sent, message-if-not)."""
    if not otp.allow_send(email):
        return False, "Too many codes were requested for this email. Please try again in an hour."
    code = otp.issue(purpose, email)
    try:
        (mailer.send_signup_code if purpose == "signup" else mailer.send_reset_code)(email, code, _MINUTES)
    except mailer.MailError:
        otp.clear(purpose)
        applog.log(f"{purpose}_email_failed", "auth", "error")
        return False, "We couldn't send the email. Please try again in a moment."
    applog.log(f"{purpose}_code_sent", "auth")
    return True, ""


def _signup_email(email: str) -> Tuple[bool, str]:
    """Send the right email for a sign-up attempt, without letting the screen reveal which.

    New address     -> a verification code.
    Existing account-> a "you already have an account" notice instead of a code. A code is still
                       issued internally (and never sent), so typing a wrong code behaves exactly
                       as it does for a new address and nothing on screen differs."""
    try:
        exists = bool(admin.user_id_by_email(email))
    except sb.SupabaseError as exc:
        applog.log("signup_lookup_failed", "auth", "error", code=exc.code, status=exc.status or 0)
        return False, "Something went wrong. Please try again."
    if not exists:
        return _email_code("signup", email)
    if not otp.allow_send(email):
        return False, "Too many codes were requested for this email. Please try again in an hour."
    otp.issue("signup", email)   # decoy: never emailed, so no code can ever succeed for this address
    try:
        mailer.send_account_exists(email)
    except mailer.MailError:
        otp.clear("signup")
        applog.log("signup_email_failed", "auth", "error")
        return False, "We couldn't send the email. Please try again in a moment."
    applog.log("signup_existing_account_notice", "auth")
    return True, ""


def sign_up(email: str, password: str) -> Tuple[bool, str]:
    """Step 1 of sign-up: email a code. The account is only created (already verified) once
    the code is entered -- see verify_signup. Never reveals whether the email is registered."""
    email = email.strip().lower()
    if not email_ready():
        return False, _NOT_READY
    sent, problem = _signup_email(email)
    if not sent:
        return False, problem
    st.session_state[_VERIFY_EMAIL] = email
    st.session_state[_VERIFY_PASSWORD] = password
    return False, ""


def send_signup_code(email: str) -> Tuple[bool, str]:
    """Send a fresh sign-up code (after the resend cooldown, which the UI enforces)."""
    return _signup_email(email)


def verify_signup(email: str, code: str) -> Tuple[bool, str]:
    """Check the emailed code; if right, create the verified account and sign the user in."""
    password = st.session_state.get(_VERIFY_PASSWORD)
    result = otp.check("signup", email, code)
    if result != otp.OK:
        applog.log("signup_verify_failed", "auth", "warning", reason=result)
        return False, otp.explain(result)
    if not password:
        return False, "Your session expired. Please start sign-up again."
    try:
        admin.create_confirmed_user(email, password)
    except sb.SupabaseError as exc:
        applog.log("signup_create_failed", "auth", "warning", code=exc.code, status=exc.status or 0)
        for key in (_VERIFY_EMAIL, _VERIFY_PASSWORD):
            st.session_state.pop(key, None)
        if exc.code == "email_exists" or exc.status == 422:
            return False, ("An account with this email already exists. Log in, or use Forgot "
                           "password if you can't remember it.")
        return False, _friendly(exc, signing_up=True)
    for key in (_VERIFY_EMAIL, _VERIFY_PASSWORD):
        st.session_state.pop(key, None)
    applog.log("signup_verified", "auth")
    ok, message = log_in(email, password)
    return ok, message


def request_password_reset(email: str) -> Tuple[bool, str]:
    """Email a reset code -- but only if the address has an account. The answer is the same
    either way, so the form can't be used to find out who is registered."""
    email = email.strip().lower()
    if not email_ready():
        return False, _NOT_READY
    try:
        exists = bool(admin.user_id_by_email(email))
    except sb.SupabaseError as exc:
        applog.log("reset_lookup_failed", "auth", "error", code=exc.code, status=exc.status or 0)
        return False, "Something went wrong. Please try again."
    if exists:
        sent, problem = _email_code("reset", email)
        if not sent:
            return False, problem
    else:
        # Same look and timing-ish behaviour; still count it so the form can't be hammered.
        if not otp.allow_send(email):
            return False, "Too many codes were requested for this email. Please try again in an hour."
        otp.clear("reset")
        applog.log("reset_unknown_email", "auth")
    st.session_state[_RESET_EMAIL] = email
    return True, ""


def finish_password_reset(email: str, code: str, new_password: str) -> Tuple[bool, str]:
    """Check the code, set the new password, and sign the account out everywhere. The data
    stays readable: encryption keys don't depend on the password."""
    result = otp.check("reset", email, code)
    if result != otp.OK:
        applog.log("reset_verify_failed", "auth", "warning", reason=result)
        return False, otp.explain(result)
    try:
        user_id = admin.user_id_by_email(email)
        if not user_id:
            return False, "That code didn't work. Ask for a new one."
        admin.set_password(user_id, new_password)
        admin.revoke_sessions(user_id)
    except sb.SupabaseError as exc:
        applog.log("reset_update_failed", "auth", "error", code=exc.code, status=exc.status or 0)
        return False, (_friendly(exc, signing_up=True)
                       + " (That code has now been used, so request a new one to try again.)")
    st.session_state.pop(_RESET_EMAIL, None)
    applog.log("password_reset_done", "auth")
    return True, ""


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

        if st.session_state.get(_VERIFY_EMAIL):
            _render_verify_form()
            return

        mode = section_picker(["Log in", "Create account", "Forgot password"], "login_mode")
        if mode == "Forgot password":
            _render_reset_flow()
        elif mode == "Log in":
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
                    if ok or st.session_state.get(_VERIFY_EMAIL):
                        st.rerun()   # logged in, or on to the "enter your code" step
                    st.error(message)


def _render_verify_form() -> None:
    """Step 2 of sign-up: enter the code emailed to the new address."""
    email = st.session_state[_VERIFY_EMAIL]
    st.markdown("#### Check your email")
    st.markdown(f"We've sent a verification code to **{email}**. Enter it below to finish creating "
                f"your account. It's valid for {_MINUTES} minutes.")
    with st.form("verify_form"):
        code = st.text_input("Verification code", key="verify_code", max_chars=10, autocomplete="one-time-code")
        submitted = st.form_submit_button("Verify and continue", type="primary", width="stretch")
    if submitted:
        if not code.strip():
            st.error("Enter the code from the email.")
        else:
            ok, message = verify_signup(email, code)
            if ok:
                st.rerun()
            st.error(message)
    c1, c2 = st.columns(2)
    if c1.button("Send a new code", key="verify_resend", width="stretch"):
        wait = otp.seconds_until_resend("signup")
        if wait:
            st.warning(f"Please wait {wait}s before asking for another code.")
        else:
            ok, message = send_signup_code(email)
            (st.success("A new code is on its way.") if ok else st.error(message))
    if c2.button("Use a different email", key="verify_back", width="stretch"):
        for key in (_VERIFY_EMAIL, _VERIFY_PASSWORD):
            st.session_state.pop(key, None)
        otp.clear("signup")
        st.rerun()
    st.caption("Can't find it? Check your spam folder. Already have an account? Go back and use "
               "**Log in** or **Forgot password**.")


def _render_reset_flow() -> None:
    """Forgot password: ask for the email, then the emailed code + a new password."""
    email = st.session_state.get(_RESET_EMAIL)
    if not email:
        st.markdown("Enter the email you signed up with and we'll send you a code to reset your password.")
        with st.form("reset_request_form"):
            addr = st.text_input("Email", key="reset_email", autocomplete="email")
            sent = st.form_submit_button("Send reset code", type="primary", width="stretch")
        if sent:
            if not is_valid_email(addr):
                st.error("Enter a valid email address.")
            else:
                ok, message = request_password_reset(addr)
                if ok:
                    st.rerun()
                st.error(message)
        return

    st.success(f"If **{email}** has an account, a reset code is on its way.")
    with st.form("reset_finish_form"):
        code = st.text_input("Reset code", key="reset_code", max_chars=10, autocomplete="one-time-code")
        pw1 = st.text_input(f"New password (at least {_MIN_PASSWORD} characters)", type="password",
                            key="reset_pw1", autocomplete="new-password")
        pw2 = st.text_input("Confirm new password", type="password", key="reset_pw2",
                            autocomplete="new-password")
        done = st.form_submit_button("Set new password", type="primary", width="stretch")
    if done:
        if not code.strip():
            st.error("Enter the code from the email.")
        elif len(pw1) < _MIN_PASSWORD:
            st.error(f"Your password needs at least {_MIN_PASSWORD} characters.")
        elif pw1 != pw2:
            st.error("The two passwords don't match.")
        else:
            ok, message = finish_password_reset(email, code, pw1)
            if ok:
                st.session_state["_login_notice"] = "Password updated. Log in with your new password."
                st.session_state["login_mode__last"] = "Log in"
                st.rerun()
            st.error(message)
    c1, c2 = st.columns(2)
    if c1.button("Send a new code", key="reset_resend", width="stretch"):
        wait = otp.seconds_until_resend("reset")
        if wait:
            st.warning(f"Please wait {wait}s before asking for another code.")
        else:
            ok, message = request_password_reset(email)
            (st.success("A new code is on its way.") if ok else st.error(message))
    if c2.button("Use a different email", key="reset_back", width="stretch"):
        st.session_state.pop(_RESET_EMAIL, None)
        otp.clear("reset")
        st.rerun()
