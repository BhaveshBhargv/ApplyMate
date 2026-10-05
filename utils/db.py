"""Data access for the signed-in user: résumés, cover letters, saved jobs, privacy.

The ONLY module that talks to the user tables. Every row's personal content is
encrypted here before it is sent (see utils.crypto) and decrypted after it is
read; the database only ever sees ciphertext. Requests run as the user (their
own access token), so Row Level Security restricts them to their own rows.

All functions raise DataError with a message that is safe to show the user.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import streamlit as st

from models.resume_data import ResumeData
from utils import applog, auth, crypto
from utils import supabase_client as sb

SCHEMA_VERSION = 1
_DEK_KEY = "_dek"
_PROFILE_KEY = "_profile"
JOB_STATUSES = ["saved", "applied", "interviewing", "offer", "rejected"]

_LIMIT_NAMES = {"resumes": "résumés", "cover_letters": "cover letters", "saved_jobs": "saved jobs"}


class DataError(Exception):
    """A problem to show the user (limit reached, expired session, network...)."""


# --- Plumbing ----------------------------------------------------------------

def _secret(name: str) -> Optional[str]:
    try:
        value = st.secrets.get(name)
        if value:
            return str(value)
    except Exception:
        pass
    return os.environ.get(name)


def master_key_configured() -> bool:
    return bool(_secret("ENCRYPTION_MASTER_KEY"))


def _master() -> bytes:
    raw = _secret("ENCRYPTION_MASTER_KEY")
    if not raw:
        raise DataError("The encryption key isn't configured, so your data can't be saved safely.")
    try:
        return crypto.parse_master_key(raw)
    except crypto.CryptoError as exc:
        raise DataError(str(exc)) from exc


def _token() -> str:
    token = auth.access_token()
    if not token:
        raise DataError("Your session has expired. Please log in again.")
    return token


def _uid() -> str:
    user = auth.current_user()
    if not user:
        raise DataError("You're not logged in.")
    return user["id"]


def _call(method: str, table: str, **kwargs: Any) -> Any:
    """One table call as the user, with database errors turned into DataError."""
    try:
        return sb.rest(method, table, _token(), **kwargs)
    except sb.SupabaseError as exc:
        if "limit_reached" in exc.message:
            name = _LIMIT_NAMES.get(exc.message.split("limit_reached:")[-1].strip(), "items")
            raise DataError(f"You've reached the limit for stored {name}. Delete one to add another.") from exc
        applog.log("db_error", "error", "error", table=table, status=exc.status or 0, code=exc.code)
        if exc.status == 401:
            raise DataError("Your session has expired. Please log in again.") from exc
        raise DataError("Couldn't reach your saved data right now. Please try again.") from exc


def _rpc(name: str, payload: Optional[dict] = None) -> Any:
    try:
        return sb.rpc(name, _token(), payload)
    except sb.SupabaseError as exc:
        applog.log("rpc_error", "error", "error", fn=name, status=exc.status or 0, code=exc.code)
        raise DataError("Couldn't complete that right now. Please try again.") from exc


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Profile + encryption key ------------------------------------------------

def ensure_profile() -> Dict[str, Any]:
    """Load (or on first login create) the user's profile and unwrap their data
    key into the session. Returns {'onboarding_done': bool}."""
    if _PROFILE_KEY in st.session_state and _DEK_KEY in st.session_state:
        return st.session_state[_PROFILE_KEY]
    uid, master = _uid(), _master()
    params = {"user_id": f"eq.{uid}", "select": "wrapped_dek,onboarding_completed_at"}
    rows = _call("GET", "profiles", params=params) or []
    if not rows:
        dek = crypto.new_dek()
        _call("POST", "profiles",
              payload=[{"user_id": uid, "wrapped_dek": crypto.wrap_dek(master, uid, dek)}],
              prefer="resolution=ignore-duplicates,return=minimal")
        rows = _call("GET", "profiles", params=params) or []   # re-read: another tab may have won the race
        if not rows:
            raise DataError("Couldn't set up your account storage. Please try again.")
        applog.log("profile_created", "data")
    try:
        dek = crypto.unwrap_dek(master, uid, rows[0]["wrapped_dek"])
    except crypto.CryptoError as exc:
        applog.log("dek_unwrap_failed", "error", "error")
        raise DataError("Your saved data can't be unlocked with the current encryption key. "
                        "Contact support before saving anything new.") from exc
    st.session_state[_DEK_KEY] = dek
    profile = {"onboarding_done": bool(rows[0].get("onboarding_completed_at"))}
    st.session_state[_PROFILE_KEY] = profile
    return profile


def _dek() -> bytes:
    ensure_profile()
    return st.session_state[_DEK_KEY]


def mark_onboarding_done() -> None:
    _call("PATCH", "profiles", params={"user_id": f"eq.{_uid()}"},
          payload={"onboarding_completed_at": _now_iso()}, prefer="return=minimal")
    ensure_profile()["onboarding_done"] = True
    applog.log("onboarding_completed", "app")


# --- Résumés -----------------------------------------------------------------

@dataclass
class SavedResume:
    id: str
    label: str
    resume: ResumeData
    updated_at: str


def _decrypt(kind: str, row: dict) -> Optional[dict]:
    try:
        return crypto.decrypt_json(_dek(), _uid(), kind, row["id"], row["data_enc"])
    except (crypto.CryptoError, ValueError):
        applog.log("decrypt_failed", "error", "error", kind=kind)
        return None


def list_resumes() -> List[SavedResume]:
    rows = _call("GET", "resumes", params={"select": "id,data_enc,updated_at",
                                           "order": "updated_at.desc"}) or []
    out: List[SavedResume] = []
    for row in rows:
        payload = _decrypt("resume", row)
        if payload is None:
            continue
        out.append(SavedResume(row["id"], payload.get("label") or "Untitled résumé",
                               ResumeData.from_dict(payload.get("resume")), row["updated_at"]))
    return out


def count_resumes() -> int:
    return len(_call("GET", "resumes", params={"select": "id"}) or [])


def save_resume(resume_id: Optional[str], label: str, resume: ResumeData) -> str:
    """Create (resume_id None) or update a résumé. Returns its id."""
    uid, dek = _uid(), _dek()
    row_id = resume_id or str(uuid.uuid4())
    blob = crypto.encrypt_json(dek, uid, "resume", row_id,
                               {"label": label.strip()[:80] or "My résumé", "resume": resume.to_dict()})
    if resume_id:
        _call("PATCH", "resumes", params={"id": f"eq.{row_id}", "user_id": f"eq.{uid}"},
              payload={"data_enc": blob, "schema_version": SCHEMA_VERSION}, prefer="return=minimal")
    else:
        _call("POST", "resumes",
              payload=[{"id": row_id, "user_id": uid, "data_enc": blob, "schema_version": SCHEMA_VERSION}],
              prefer="return=minimal")
        applog.log("resume_created", "data")
    return row_id


def delete_resume(resume_id: str) -> None:
    _call("DELETE", "resumes", params={"id": f"eq.{resume_id}", "user_id": f"eq.{_uid()}"},
          prefer="return=minimal")
    applog.log("resume_deleted", "data")


# --- Cover letters -----------------------------------------------------------

@dataclass
class SavedLetter:
    id: str
    title: str
    company: str
    role: str
    text: str
    resume_id: Optional[str]
    updated_at: str


def list_cover_letters() -> List[SavedLetter]:
    rows = _call("GET", "cover_letters", params={"select": "id,resume_id,data_enc,updated_at",
                                                 "order": "updated_at.desc"}) or []
    out: List[SavedLetter] = []
    for row in rows:
        p = _decrypt("cover_letter", row)
        if p is None:
            continue
        out.append(SavedLetter(row["id"], p.get("title", ""), p.get("company", ""), p.get("role", ""),
                               p.get("text", ""), row.get("resume_id"), row["updated_at"]))
    return out


def save_cover_letter(letter_id: Optional[str], *, title: str, company: str, role: str,
                      text: str, resume_id: Optional[str]) -> str:
    uid, dek = _uid(), _dek()
    row_id = letter_id or str(uuid.uuid4())
    default_title = " – ".join(p for p in [role.strip(), company.strip()] if p) or "Cover letter"
    blob = crypto.encrypt_json(dek, uid, "cover_letter", row_id, {
        "title": (title.strip() or default_title)[:100], "company": company.strip()[:100],
        "role": role.strip()[:100], "text": text,
    })
    if letter_id:
        _call("PATCH", "cover_letters", params={"id": f"eq.{row_id}", "user_id": f"eq.{uid}"},
              payload={"data_enc": blob}, prefer="return=minimal")
    else:
        _call("POST", "cover_letters",
              payload=[{"id": row_id, "user_id": uid, "resume_id": resume_id, "data_enc": blob}],
              prefer="return=minimal")
        applog.log("cover_letter_saved", "data")
    return row_id


def delete_cover_letter(letter_id: str) -> None:
    _call("DELETE", "cover_letters", params={"id": f"eq.{letter_id}", "user_id": f"eq.{_uid()}"},
          prefer="return=minimal")
    applog.log("cover_letter_deleted", "data")


# --- Saved jobs --------------------------------------------------------------

_JOB_FIELDS = ("title", "company", "location", "url", "source", "salary", "job_type", "posted")


@dataclass
class SavedJob:
    id: str
    job: Dict[str, str]
    notes: str
    status: str
    saved_at: str


def job_hash(url: str) -> str:
    return crypto.keyed_hash(_dek(), url)


def saved_job_hashes() -> Set[str]:
    """Hashes of every saved job URL (no decryption) -- used to mark cards as saved."""
    rows = _call("GET", "saved_jobs", params={"select": "url_hash"}) or []
    return {r["url_hash"] for r in rows}


def save_job(job: Any) -> str:
    """Save a JobPosting (or dict). Saving the same URL twice is a no-op."""
    data = asdict(job) if hasattr(job, "__dataclass_fields__") else dict(job)
    snapshot = {k: str(data.get(k, "") or "")[:500] for k in _JOB_FIELDS}
    if not snapshot["url"]:
        raise DataError("This job has no link, so it can't be saved.")
    uid, dek = _uid(), _dek()
    row_id = str(uuid.uuid4())
    blob = crypto.encrypt_json(dek, uid, "saved_job", row_id, {"job": snapshot, "notes": ""})
    rows = _call("POST", "saved_jobs", params={"on_conflict": "user_id,url_hash"},
                 payload=[{"id": row_id, "user_id": uid, "url_hash": job_hash(snapshot["url"]),
                           "source": snapshot["source"][:40], "status": "saved", "data_enc": blob}],
                 prefer="resolution=ignore-duplicates,return=representation")
    if rows:
        applog.log("job_saved", "data", source=snapshot["source"][:40])
    return row_id


def list_saved_jobs() -> List[SavedJob]:
    rows = _call("GET", "saved_jobs", params={"select": "id,status,data_enc,saved_at",
                                              "order": "saved_at.desc"}) or []
    out: List[SavedJob] = []
    for row in rows:
        p = _decrypt("saved_job", row)
        if p is None:
            continue
        out.append(SavedJob(row["id"], p.get("job", {}), p.get("notes", ""), row["status"], row["saved_at"]))
    return out


def update_saved_job(saved: SavedJob, *, status: str, notes: str) -> None:
    if status not in JOB_STATUSES:
        raise DataError("Unknown status.")
    uid = _uid()
    blob = crypto.encrypt_json(_dek(), uid, "saved_job", saved.id,
                               {"job": saved.job, "notes": notes.strip()[:2000]})
    _call("PATCH", "saved_jobs", params={"id": f"eq.{saved.id}", "user_id": f"eq.{uid}"},
          payload={"status": status, "data_enc": blob}, prefer="return=minimal")


def delete_saved_job(job_id: str) -> None:
    _call("DELETE", "saved_jobs", params={"id": f"eq.{job_id}", "user_id": f"eq.{_uid()}"},
          prefer="return=minimal")
    applog.log("job_removed", "data")


# --- AI quota ----------------------------------------------------------------

def ai_quota() -> Dict[str, int]:
    """{'used', 'limit'} for today, or {} if it can't be read."""
    try:
        result = sb.rpc("ai_quota", _token())
        return dict(result) if isinstance(result, dict) else {}
    except (sb.SupabaseError, DataError):
        return {}


def consume_ai_call() -> Dict[str, Any]:
    """Count one AI call against today's limit. {'allowed': bool, 'used', 'limit'}.
    Fails open (allowed) if the counter can't be reached, so an outage of the
    counter never takes the AI features down with it."""
    try:
        result = sb.rpc("consume_ai_call", _token())
        if isinstance(result, dict) and "allowed" in result:
            return result
    except (sb.SupabaseError, DataError):
        pass
    return {"allowed": True}


# --- Privacy: export + delete ------------------------------------------------

def export_all() -> Dict[str, Any]:
    """Everything stored about the user, decrypted (UK GDPR right of access)."""
    user = auth.current_user() or {}
    return {
        "exported_at": _now_iso(),
        "email": user.get("email", ""),
        "resumes": [{"label": r.label, "updated_at": r.updated_at, **r.resume.to_dict()}
                    for r in list_resumes()],
        "cover_letters": [asdict(c) for c in list_cover_letters()],
        "saved_jobs": [{"status": j.status, "saved_at": j.saved_at, "notes": j.notes, **j.job}
                       for j in list_saved_jobs()],
    }


def delete_all_data() -> None:
    """Delete every résumé, cover letter and saved job; the login stays."""
    uid = _uid()
    for table in ("cover_letters", "saved_jobs", "resumes"):
        _call("DELETE", table, params={"user_id": f"eq.{uid}"}, prefer="return=minimal")
    applog.log("user_data_deleted", "privacy")


def delete_account() -> None:
    """Permanently delete the account and, by cascade, all of the user's data and
    their encryption key. Ends the local session."""
    _rpc("delete_my_account")
    st.session_state.clear()
    st.session_state["_logged_out"] = True
    st.session_state["_cookie_pending"] = ("clear", None)
