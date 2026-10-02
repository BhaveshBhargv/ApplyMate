"""Work that depends only on the saved résumé, done when it is saved.

Anything computed from the résumé alone -- no job description, no extra
input -- shouldn't wait for a page to open. The Dashboard calls
on_resume_saved() after it renders; when the résumé has changed since the last
call, it prepares two things:

- **Job-role suggestions.** The keyword ranking (utils.job_roles.fallback_roles)
  is stored immediately, so the Jobs page always has roles. If an OpenRouter key
  is configured, the AI call then runs in the background and replaces them when
  it lands.
- **Résumé-side embeddings.** The ATS match and tailoring agents embed the
  skills / experience / education / bullet texts every run; embedding them at
  save warms the shared in-process cache (utils.embeddings), so those pages
  only need to embed the job-description side.

The Jobs page never calls an AI itself -- it only reads suggested_roles().

The worker thread is handed plain strings and pre-resolved config and never
touches Streamlit: st.session_state needs a script-run context, and st.secrets
is unreliable off the main thread (see utils.embeddings).
"""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import streamlit as st

from models.resume_data import ResumeData
from utils import agents, ai_assistant, ats_analyzer, embeddings, job_roles

_KEY = "resume_precompute"


@dataclass
class _State:
    lock: threading.Lock = field(default_factory=threading.Lock)
    requested: str = ""                 # fingerprint of the résumé last handled
    roles: Optional[List[str]] = None   # what the Jobs page shows
    ai_pending: bool = False            # an AI call for `requested` is running


def _state() -> _State:
    state = st.session_state.get(_KEY)
    if state is None:
        state = st.session_state[_KEY] = _State()
    return state


def _embedding_texts(resume: ResumeData) -> List[str]:
    """The résumé-side texts the ATS match and tailoring agents embed, so the
    cache entries warmed here are the exact ones those pages look up."""
    texts = list(resume.all_skills_flat())
    texts += ats_analyzer._resume_experience_units(resume)
    texts += ats_analyzer._resume_education_units(resume)
    texts += [unit.text for unit in agents.collect_units(resume)]
    seen = set()
    unique: List[str] = []
    for text in texts:
        if text and text not in seen:
            seen.add(text)
            unique.append(text)
    return unique


def _worker(state: _State, fingerprint: str, facts: str, texts: List[str],
            ai_config: Optional[dict], embedding_config: Optional[Dict[str, str]]) -> None:
    # Broad excepts: this is a background boundary. A failed call must never
    # surface -- the heuristic roles are already stored, and the ATS pages embed
    # on demand if the cache wasn't warmed.
    if ai_config:
        roles = None
        try:
            roles = ai_assistant.suggest_job_roles(facts, config=ai_config)
        except Exception:  # noqa: BLE001
            roles = None
        with state.lock:
            if state.requested == fingerprint:   # ignore if the résumé changed since
                if roles:
                    state.roles = roles
                state.ai_pending = False
    if embedding_config and texts:
        try:
            embeddings.embed(texts, **embedding_config)
        except Exception:  # noqa: BLE001
            pass


def on_resume_saved(resume: ResumeData) -> None:
    """Prepare everything résumé-only. Cheap and idempotent: does nothing
    unless the résumé differs from the last call. Main thread only."""
    state = _state()
    facts = job_roles.timeline_facts(resume)
    texts = _embedding_texts(resume)
    fingerprint = hashlib.sha1("\x00".join([facts, *texts]).encode("utf-8")).hexdigest()

    with state.lock:
        if fingerprint == state.requested:
            return
        state.requested = fingerprint
        state.roles = job_roles.fallback_roles(resume) if job_roles.has_enough_to_suggest(resume) else []
        state.ai_pending = False

    if not job_roles.has_enough_to_suggest(resume):
        return
    ai_config = ai_assistant.snapshot_config()
    embedding_config = embeddings.snapshot_config() if texts else None
    if not ai_config and not embedding_config:
        return
    with state.lock:
        state.ai_pending = bool(ai_config)
    threading.Thread(
        target=_worker,
        args=(state, fingerprint, facts, texts, ai_config, embedding_config),
        daemon=True,
    ).start()


def suggested_roles(resume: ResumeData) -> List[str]:
    """The stored role suggestions, for the Jobs page. Falls back to the
    keyword ranking if on_resume_saved() hasn't run this session."""
    state = st.session_state.get(_KEY)
    if state is not None:
        with state.lock:
            if state.roles is not None:
                return list(state.roles)
    return job_roles.fallback_roles(resume) if job_roles.has_enough_to_suggest(resume) else []


def is_refining() -> bool:
    """True while the AI is still working on the current résumé's roles."""
    state = st.session_state.get(_KEY)
    if state is None:
        return False
    with state.lock:
        return state.ai_pending
