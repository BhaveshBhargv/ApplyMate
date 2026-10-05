"""My Account: everything the signed-in user has saved, plus their privacy controls.

Tabs: Résumés, Cover letters, Saved jobs, Privacy & data. All content is read
from the user's own encrypted rows (utils.db) -- nothing here is visible to
anyone else. Deleting data is permanent, and always behind a typed confirmation.
"""
import json
from datetime import datetime
from html import escape

import streamlit as st

from utils import account, auth, cover_letter_pdf, db, job_search, walkthrough
from utils.job_search import JobPosting
from utils.session_manager import (
    get_resume_data,
    init_session_state,
    set_cover_letter_target,
    set_job_description,
)
from utils.theme import inject_theme, render_header, render_hero, section_picker

inject_theme()
render_header("account")
init_session_state()

user = auth.current_user()
if not user:
    st.info("Log in to see your account.")
    st.stop()


def _when(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%d %b %Y, %H:%M")
    except ValueError:
        return ""


def _load(fetch):
    """Run a list query; show a friendly error and return [] if it fails."""
    try:
        return fetch()
    except db.DataError as exc:
        st.error(str(exc))
        return []


def _attempt(action, success: str = "") -> bool:
    try:
        action()
    except db.DataError as exc:
        st.error(str(exc))
        return False
    if success:
        st.toast(success)
    return True


st.markdown(
    """
<style>
.rb-acct-meta { font-family:var(--mono); font-size:.74rem; color:var(--muted); margin:.1rem 0 .4rem; }
.rb-acct-title { font-weight:600; color:var(--text); font-size:.98rem; }
.rb-acct-sub { color:var(--muted); font-size:.82rem; margin:0 0 .4rem; }
</style>
""",
    unsafe_allow_html=True,
)

render_hero("My account", "Your saved work",
            "Your résumés, cover letters and saved jobs. Everything here is private to you "
            "and stored encrypted.")

quota = db.ai_quota()
info_col, quota_col, tour_col = st.columns([2.4, 1.6, 1.4])
info_col.markdown(f'<p class="rb-acct-meta">Signed in as <b>{escape(user["email"])}</b></p>',
                  unsafe_allow_html=True)
if quota:
    quota_col.markdown(f'<p class="rb-acct-meta">AI requests today: <b>{quota.get("used", 0)} / '
                       f'{quota.get("limit", "?")}</b></p>', unsafe_allow_html=True)
if tour_col.button("Take the tour again", key="acct_tour", width="stretch"):
    walkthrough.start()
    st.switch_page("pages/1_🧭_Dashboard.py")

section = section_picker(["Résumés", "Cover letters", "Saved jobs", "Privacy & data"], "acct_section")

# --- Résumés -------------------------------------------------------------------
if section == "Résumés":
    resumes = _load(db.list_resumes)
    active_id = account.active_resume_id()
    st.caption(f"{len(resumes)} of 5 résumés stored.")
    if st.button("New blank résumé", key="acct_new_resume", disabled=len(resumes) >= 5):
        account.new_resume()
        st.switch_page("pages/1_🧭_Dashboard.py")
    if not resumes:
        st.info("No saved résumés yet. Fill in your details on the **Dashboard** — "
                "they're saved to your account automatically.")
    for saved in resumes:
        is_active = saved.id == active_id
        with st.container(border=True):
            label = account.active_resume_label() if is_active else saved.label
            st.markdown(
                f'<div class="rb-acct-title">{escape(label or saved.label)}'
                f'{" · editing now" if is_active else ""}</div>'
                f'<div class="rb-acct-sub">Updated {escape(_when(saved.updated_at))}</div>',
                unsafe_allow_html=True,
            )
            c1, c2, c3 = st.columns([1.2, 2.2, 1.2])
            if c1.button("Open in builder", key=f"res_open_{saved.id}", width="stretch",
                         disabled=is_active):
                account.open_resume(saved)
                st.switch_page("pages/1_🧭_Dashboard.py")
            with c2.popover("Rename", width="stretch"):
                new_label = st.text_input("Name", value=label, key=f"res_label_{saved.id}", max_chars=80)
                if st.button("Save name", key=f"res_rename_{saved.id}"):
                    if is_active:
                        account.rename_active(new_label)
                        st.rerun()
                    elif _attempt(lambda: db.save_resume(saved.id, new_label, saved.resume), "Renamed."):
                        st.rerun()
            with c3.popover("Delete", width="stretch"):
                st.warning("This permanently deletes this résumé.")
                if st.button("Yes, delete it", key=f"res_del_{saved.id}", type="primary"):
                    if _attempt(lambda: db.delete_resume(saved.id)):
                        if is_active:
                            account.new_resume()
                        st.rerun()

# --- Cover letters -------------------------------------------------------------
elif section == "Cover letters":
    letters = _load(db.list_cover_letters)
    st.caption(f"{len(letters)} of 10 cover letters stored. Save new ones from the **Cover Letter** page.")
    if not letters:
        st.info("No saved cover letters yet.")
    for letter in letters:
        with st.expander(f"{letter.title}  ·  {_when(letter.updated_at)}"):
            text = st.text_area("Letter", value=letter.text, height=320, key=f"cl_text_{letter.id}")
            b1, b2, b3, _sp = st.columns([1.2, 1.2, 1.2, 1.4])
            if b1.button("Save changes", key=f"cl_save_{letter.id}", width="stretch"):
                if _attempt(lambda: db.save_cover_letter(
                        letter.id, title=letter.title, company=letter.company, role=letter.role,
                        text=text, resume_id=letter.resume_id), "Cover letter updated."):
                    st.rerun()
            source = next((r.resume for r in _load(db.list_resumes) if r.id == letter.resume_id), None) \
                if letter.resume_id else None
            b2.download_button(
                "Download PDF",
                data=cover_letter_pdf.build_cover_letter_pdf(
                    source or get_resume_data(), text, company=letter.company, role=letter.role),
                file_name=f"{cover_letter_pdf.file_stem(source or get_resume_data(), letter.company)}.pdf",
                mime="application/pdf", key=f"cl_pdf_{letter.id}", width="stretch",
                disabled=not text.strip(),
            )
            with b3.popover("Delete", width="stretch"):
                st.warning("This permanently deletes this letter.")
                if st.button("Yes, delete it", key=f"cl_del_{letter.id}", type="primary"):
                    if _attempt(lambda: db.delete_cover_letter(letter.id)):
                        st.rerun()

# --- Saved jobs ----------------------------------------------------------------
elif section == "Saved jobs":
    jobs = _load(db.list_saved_jobs)
    st.caption(f"{len(jobs)} of 50 saved jobs. Save jobs from the **Jobs** page.")
    if not jobs:
        st.info("No saved jobs yet.")
    for saved in jobs:
        job = saved.job
        link = job.get("url", "")
        with st.container(border=True):
            meta = " · ".join(p for p in [job.get("company", ""), job.get("location", ""),
                                          job.get("source", "")] if p)
            st.markdown(
                f'<div class="rb-acct-title">{escape(job.get("title", "") or "Untitled job")}</div>'
                f'<div class="rb-acct-sub">{escape(meta)} · saved {escape(_when(saved.saved_at))}</div>',
                unsafe_allow_html=True,
            )
            s1, s2 = st.columns([1, 2])
            status = s1.selectbox("Status", db.JOB_STATUSES, index=db.JOB_STATUSES.index(saved.status)
                                  if saved.status in db.JOB_STATUSES else 0,
                                  key=f"job_status_{saved.id}", format_func=str.title)
            notes = s2.text_area("Notes (private)", value=saved.notes, height=80,
                                 key=f"job_notes_{saved.id}", max_chars=2000)
            a, b, c, d, _sp = st.columns([1.1, 1.1, 1.2, 1.0, 0.6])
            if link.startswith(("http://", "https://")):
                a.link_button("View & apply", link, width="stretch")
            # Not disabled when nothing changed: a text box only commits on blur, so a
            # button greyed out until then would swallow the first click.
            if b.button("Save changes", key=f"job_save_{saved.id}", width="stretch"):
                if _attempt(lambda: db.update_saved_job(saved, status=status, notes=notes), "Job updated."):
                    st.rerun()
            if c.button("Cover letter", key=f"job_cl_{saved.id}", width="stretch"):
                posting = JobPosting(**{k: job.get(k, "") for k in
                                        ("title", "company", "location", "url", "source", "salary",
                                         "job_type", "posted")})
                with st.spinner("Loading the full job description..."):
                    set_job_description(job_search.fetch_full_description(posting))
                set_cover_letter_target(posting.company, posting.title)
                st.session_state["cover_letter_auto"] = True
                st.switch_page("pages/4_✉️_Cover_Letter.py")
            with d.popover("Remove", width="stretch"):
                st.warning("Remove this job from your saved list?")
                if st.button("Yes, remove", key=f"job_del_{saved.id}", type="primary"):
                    if _attempt(lambda: db.delete_saved_job(saved.id)):
                        st.rerun()

# --- Privacy & data ------------------------------------------------------------
else:
    st.markdown("#### What's stored")
    st.markdown(
        "- Your **email** (for login).\n"
        "- Your **résumés, cover letters, saved jobs and notes**, encrypted with a key unique to your account "
        "before they reach the database.\n"
        "- Technical **logs** (event names and error types — never résumé content) and a daily "
        "**AI-request counter**.\n"
        "- When you use an AI feature, the relevant résumé text is sent to our AI provider (OpenRouter) "
        "to produce the result."
    )

    st.markdown("#### Download your data")
    st.caption("A copy of everything stored about you, in a readable JSON file.")
    if st.button("Prepare my data export", key="acct_export"):
        with st.spinner("Collecting your data..."):
            try:
                st.session_state["_export_json"] = json.dumps(db.export_all(), indent=2, ensure_ascii=False)
            except db.DataError as exc:
                st.error(str(exc))
    if st.session_state.get("_export_json"):
        st.download_button("Download my data (JSON)", st.session_state["_export_json"],
                           file_name="applymate-my-data.json", mime="application/json",
                           key="acct_export_dl", type="primary")

    st.markdown("#### Delete your data")
    st.caption("Permanently deletes every résumé, cover letter and saved job. You keep your login.")
    with st.container(border=True):
        confirm_data = st.text_input("Type DELETE to confirm", key="acct_confirm_data")
        if st.button("Delete all my stored data", key="acct_delete_data",
                     disabled=confirm_data.strip() != "DELETE"):
            if _attempt(db.delete_all_data):
                account.new_resume()
                st.session_state.pop("_export_json", None)
                st.success("All your résumés, cover letters and saved jobs have been deleted.")

    st.markdown("#### Delete your account")
    st.caption("Permanently deletes your account, all your data and your encryption key. "
               "This cannot be undone.")
    with st.container(border=True):
        confirm_acct = st.text_input("Type DELETE to confirm", key="acct_confirm_account")
        if st.button("Delete my account and everything in it", key="acct_delete_account", type="primary",
                     disabled=confirm_acct.strip() != "DELETE"):
            if _attempt(db.delete_account):
                st.session_state["_login_notice"] = "Your account and all its data have been deleted."
                st.rerun()
