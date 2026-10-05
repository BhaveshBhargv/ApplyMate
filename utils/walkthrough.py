"""First-login walkthrough: a short pop-up tour of the app.

Shown automatically once per account (the moment it opens it's recorded as
done, so closing it with the X never makes it nag again), and again whenever the
user clicks "Take the tour again" on My Account.
"""
from __future__ import annotations

import streamlit as st

from utils import db

_PENDING = "_tour_pending"
_STEP = "_tour_step"

_STEPS = [
    ("🧭", "Dashboard — build your résumé",
     "**Click here to upload your résumé or enter your details.** On the **Dashboard** page, use the "
     "**Import** tab to upload a PDF or Word file, or fill in Personal, Education, Experience, Projects and "
     "Skills yourself. The live preview updates as you go, and your résumé is saved to your account automatically."),
    ("🎯", "ATS Match — compare with a job description",
     "This is the **ATS page**. Paste a job description and see how well your résumé matches it: "
     "which skills you have, which are missing, and where you're strongest."),
    ("✨", "Recommendations — improve your résumé",
     "Below the match results, generate **recommendations for changing your résumé**: reworded bullets and "
     "a tailored summary for that job, written only from what's already true on your résumé."),
    ("💼", "Jobs — search or get recommendations",
     "On the **Jobs** page, search manually by role and location, or click one of the **recommended roles** "
     "built from your résumé. Press **Save** on any job to keep it on My Account."),
    ("✉️", "Cover Letter — one letter per job",
     "Write a cover letter for a specific job. It's built from your résumé and checked line by line, then you "
     "can edit it, download it as a PDF, or save it to your account."),
    ("👤", "My Account — everything you've saved",
     "**My Account** holds your saved résumés, cover letters and jobs. It's also where you can download or "
     "permanently delete all your data. You can replay this tour from there anytime."),
]


def start() -> None:
    """Queue the tour to open on the next run."""
    st.session_state[_PENDING] = True
    st.session_state[_STEP] = 0


def maybe_show() -> None:
    """Open the tour if it's queued, or if this account has never seen it."""
    if not st.session_state.get(_PENDING):
        try:
            if db.ensure_profile().get("onboarding_done", True):
                return
        except db.DataError:
            return
        st.session_state[_STEP] = 0
        try:
            db.mark_onboarding_done()
        except db.DataError:
            pass   # worst case it shows once more next login
    st.session_state[_PENDING] = False
    _tour()


@st.dialog("Welcome to ApplyMate", width="large")
def _tour() -> None:
    step = min(st.session_state.get(_STEP, 0), len(_STEPS) - 1)
    icon, title, body = _STEPS[step]
    st.progress((step + 1) / len(_STEPS), text=f"Step {step + 1} of {len(_STEPS)}")
    st.markdown(f"### {icon} {title}")
    st.markdown(body)

    back, skip, nxt = st.columns([1, 1, 1])
    if back.button("Back", disabled=step == 0, width="stretch", key=f"tour_back_{step}"):
        st.session_state[_STEP] = step - 1
        st.rerun(scope="fragment")
    if step < len(_STEPS) - 1:
        if skip.button("Skip tour", width="stretch", key=f"tour_skip_{step}"):
            st.rerun()
        if nxt.button("Next", type="primary", width="stretch", key=f"tour_next_{step}"):
            st.session_state[_STEP] = step + 1
            st.rerun(scope="fragment")
    elif nxt.button("Get started", type="primary", width="stretch", key="tour_done"):
        st.rerun()
