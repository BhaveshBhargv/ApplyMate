"""The few privileged Supabase operations that sign-up verification and password reset need.

Because ApplyMate now verifies emails and resets passwords itself, the server must be able to
create an already-confirmed account and set a password -- things only Supabase's admin API
allows. That takes the project's **service_role key** (SUPABASE_SERVICE_KEY), which bypasses
Row Level Security. So, deliberately:

  * it lives only in server-side secrets, never in the browser, a log or the database;
  * this module is the ONLY place that reads it, and exposes four narrow functions -- there is
    no general-purpose "run anything as admin" call;
  * nothing here touches user data tables (résumés etc.); those stay behind the user's own
    login token and are encrypted by the app anyway.

Pair this with turning OFF public sign-ups in Supabase (Authentication -> Sign In / Providers),
so the only way to create an account is the verified path in utils.auth.
"""
from __future__ import annotations

from typing import Optional

from utils import supabase_client as sb


def _service_key() -> Optional[str]:
    return sb._secret("SUPABASE_SERVICE_KEY")


def is_configured() -> bool:
    return bool(_service_key() and sb.is_configured())


def _headers() -> dict:
    key = _service_key()
    if not key:
        raise sb.SupabaseError("Account administration isn't configured (SUPABASE_SERVICE_KEY).",
                               None, "not_configured")
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _base() -> str:
    url, _ = sb.config()
    if not url:
        raise sb.SupabaseError("The database isn't configured.", None, "not_configured")
    return url


def create_confirmed_user(email: str, password: str) -> str:
    """Create an account whose email is already verified. Returns the new user id.
    Raises SupabaseError (code 'email_exists') if the address already has an account."""
    result = sb._call("POST", f"{_base()}/auth/v1/admin/users", _headers(),
                      payload={"email": email, "password": password, "email_confirm": True})
    return (result or {}).get("id", "")


def user_id_by_email(email: str) -> Optional[str]:
    """The id of the account with this email, or None (uses a SQL function only the
    service role can run -- see supabase/schema.sql)."""
    result = sb._call("POST", f"{_base()}/rest/v1/rpc/admin_user_id_by_email", _headers(),
                      payload={"p_email": email})
    return result or None


def set_password(user_id: str, new_password: str) -> None:
    sb._call("PUT", f"{_base()}/auth/v1/admin/users/{user_id}", _headers(),
             payload={"password": new_password})


def revoke_sessions(user_id: str) -> None:
    """Sign the user out everywhere (after a password reset, a stolen session must not survive)."""
    sb._call("POST", f"{_base()}/rest/v1/rpc/admin_revoke_sessions", _headers(),
             payload={"p_user": user_id})
