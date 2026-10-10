"""Glue between the signed-in user's saved résumés and the live Streamlit session.

  bootstrap()  -- after login: unlock the user's key, load their latest résumé
  autosave()   -- end of every run: if the résumé changed, store it (encrypted)
  open_resume / new_resume -- switch which saved résumé is being edited

Pages keep working on `st.session_state["resume_data"]` exactly as before; this
module only mirrors it to the database. Nothing here runs for logged-out users.
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional

import streamlit as st

from models.resume_data import ResumeData
from utils import auth, db
from utils.session_manager import get_resume_data, refresh_field, set_resume_data

_ACTIVE_ID = "_active_resume_id"
_ACTIVE_LABEL = "_active_resume_label"
_SAVED_HASH = "_saved_resume_hash"
_BOOTSTRAPPED = "_bootstrapped_for"
_LAST_ERROR = "_autosave_last_error"
_ACTIVE_PRIMARY = "_active_resume_primary"


def _fingerprint(resume: ResumeData) -> str:
    return hashlib.sha256(json.dumps(resume.to_dict(), sort_keys=True).encode("utf-8")).hexdigest()


def _has_content(resume: ResumeData) -> bool:
    info = resume.personal_info
    return bool(resume.searchable_text().strip()
                or info.full_name.strip() or info.email.strip() or info.phone.strip())


def active_resume_id() -> Optional[str]:
    return st.session_state.get(_ACTIVE_ID)


def active_resume_label() -> str:
    return st.session_state.get(_ACTIVE_LABEL, "")


def active_is_primary() -> bool:
    """True if the résumé being edited is the user's primary one."""
    return bool(st.session_state.get(_ACTIVE_PRIMARY))


def bootstrap() -> Optional[str]:
    """Run once per login: loads the primary résumé (else the most recently edited).
    Returns an error message to show, or None."""
    user = auth.current_user()
    if not user or st.session_state.get(_BOOTSTRAPPED) == user["id"]:
        return None
    try:
        db.ensure_profile()
        saved = db.list_resumes()
    except db.DataError as exc:
        return str(exc)
    if saved:
        open_resume(saved[0])
    else:
        st.session_state[_SAVED_HASH] = _fingerprint(get_resume_data())
    st.session_state[_BOOTSTRAPPED] = user["id"]
    return None


def open_resume(saved: "db.SavedResume") -> None:
    """Make a saved résumé the one being edited in the builder."""
    set_resume_data(saved.resume)
    st.session_state[_ACTIVE_ID] = saved.id
    st.session_state[_ACTIVE_LABEL] = saved.label
    st.session_state[_ACTIVE_PRIMARY] = saved.is_primary
    st.session_state[_SAVED_HASH] = _fingerprint(saved.resume)
    refresh_field("personal_summary")   # the summary box is keyed; make it re-read the new text


def new_resume() -> None:
    """Start a blank résumé (it's stored as a new one once it has content)."""
    blank = ResumeData()
    set_resume_data(blank)
    st.session_state.pop(_ACTIVE_ID, None)
    st.session_state.pop(_ACTIVE_LABEL, None)
    st.session_state.pop(_ACTIVE_PRIMARY, None)
    st.session_state[_SAVED_HASH] = _fingerprint(blank)
    refresh_field("personal_summary")


def load_primary() -> str:
    """Replace the résumé in the builder with the user's primary one. Returns '' on
    success, else a message to show. Edits to the current résumé are already stored
    (autosave runs at the end of every run), so nothing is lost by switching."""
    try:
        autosave()   # make sure the résumé being left is stored first
        primary = db.primary_resume()
    except db.DataError as exc:
        return str(exc)
    if primary is None:
        return "You haven't set a primary résumé yet."
    open_resume(primary)
    return ""


def make_active_primary() -> str:
    """Mark the résumé being edited as the primary one, saving it first if it has
    never been stored. Returns '' on success, else a message to show."""
    resume = get_resume_data()
    if _ACTIVE_ID not in st.session_state and not _has_content(resume):
        return "Add some details to the résumé first."
    try:
        st.session_state.pop(_SAVED_HASH, None)   # force a write if anything is pending
        autosave()
        rid = active_resume_id()
        if rid is None:
            return "Couldn't save the résumé, so it can't be made primary."
        db.set_primary_resume(rid)
    except db.DataError as exc:
        return str(exc)
    st.session_state[_ACTIVE_PRIMARY] = True
    return ""


def set_primary(resume_id: str) -> str:
    """Make a stored résumé primary (from My Account). Returns '' or a message."""
    try:
        db.set_primary_resume(resume_id)
    except db.DataError as exc:
        return str(exc)
    st.session_state[_ACTIVE_PRIMARY] = (resume_id == active_resume_id())
    return ""


def refresh_primary_flag() -> None:
    """Re-read which résumé is primary (e.g. after deleting the old primary, which
    promotes another) so the builder's badge matches the database."""
    try:
        primary = db.primary_resume()
    except db.DataError:
        return
    st.session_state[_ACTIVE_PRIMARY] = bool(primary and primary.id == active_resume_id())


def rename_active(label: str) -> None:
    st.session_state[_ACTIVE_LABEL] = label.strip()[:80]
    st.session_state.pop(_SAVED_HASH, None)   # force the next autosave to write the new label


def autosave() -> None:
    """Store the current résumé if it changed since the last save."""
    if not auth.is_logged_in() or st.session_state.get(_BOOTSTRAPPED) != (auth.current_user() or {}).get("id"):
        return
    resume = get_resume_data()
    fingerprint = _fingerprint(resume)
    if fingerprint == st.session_state.get(_SAVED_HASH):
        return
    if _ACTIVE_ID not in st.session_state and not _has_content(resume):
        return
    try:
        existing = None if _ACTIVE_ID in st.session_state else db.count_resumes()
        label = st.session_state.get(_ACTIVE_LABEL) or (
            "My résumé" if not existing else f"Résumé {existing + 1}")
        # A user's first résumé becomes their primary one automatically.
        st.session_state[_ACTIVE_ID] = db.save_resume(
            st.session_state.get(_ACTIVE_ID), label, resume, primary=(existing == 0))
        if existing == 0 and db.primary_supported():
            st.session_state[_ACTIVE_PRIMARY] = True
        st.session_state[_ACTIVE_LABEL] = label
        st.session_state[_SAVED_HASH] = fingerprint
        st.session_state.pop(_LAST_ERROR, None)
    except db.DataError as exc:
        # Tell the user once per distinct problem instead of on every rerun.
        if st.session_state.get(_LAST_ERROR) != str(exc):
            st.session_state[_LAST_ERROR] = str(exc)
            st.toast(f"Your résumé wasn't saved: {exc}", icon="⚠️")
