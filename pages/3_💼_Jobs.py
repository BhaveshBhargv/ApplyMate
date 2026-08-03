"""Jobs: search live openings by title, location and work type; get apply links.

Inputs (job title, location, work type, country) are sent to free, official job
APIs via utils.job_search. Results are shown as cards, each linking to the real
posting. Each card also shows how well the résumé being built matches that job,
and a one-click handoff loads the job's description into ATS Match for a full
breakdown. Nothing is applied to or sent on the user's behalf -- links only.
"""
from html import escape

import streamlit as st

from utils import job_search
from utils.job_search import COUNTRIES, WORK_TYPES, search_jobs, skills_in_text
from utils.session_manager import get_resume_data, init_session_state, set_job_description
from utils.theme import inject_theme, render_download_footer, render_header

inject_theme()
render_header("jobs")
init_session_state()
resume = get_resume_data()
resume_text = resume.searchable_text()

st.markdown('<p class="rb-eyebrow">Discover</p>', unsafe_allow_html=True)
st.markdown('<p class="rb-panel-title">Find your next role</p>', unsafe_allow_html=True)
st.markdown('<p class="rb-sub">Search live openings and jump straight to the official posting. '
            'We never apply or send anything for you — just links.</p>', unsafe_allow_html=True)

# Prefill from the résumé: most recent role title + personal location.
default_title = resume.experience[0].job_title if resume.experience else ""
default_location = resume.personal_info.location or ""


def _safe_link(url: str) -> str:
    """Return an http(s) URL safe to use as a link target, else empty."""
    url = (url or "").strip()
    return url if url.startswith(("http://", "https://")) else ""


def run_search(title: str, location: str, country: str, work_type: str, limit: int) -> None:
    """Fetch jobs, tag each with which of the résumé's skills it mentions."""
    result = search_jobs(title, location, country, work_type, limit)
    my_skills = resume.all_skills_flat()
    if my_skills:
        for job in result.jobs:
            job.matched_skills = skills_in_text(my_skills, job.description)
    st.session_state["job_results"] = result


# Auto-search handoff from the Dashboard's "Search jobs for this résumé" button.
if st.session_state.pop("auto_job_search", False) and default_title.strip():
    with st.spinner("Searching openings for your résumé..."):
        run_search(default_title, default_location, "gb", "Any", 15)

# --- Search form --------------------------------------------------------------
with st.form("job_search_form"):
    c1, c2 = st.columns([1.2, 1])
    with c1:
        title = st.text_input("Job title", value=default_title,
                              placeholder="e.g. Data Engineer")
    with c2:
        location = st.text_input("Location", value=default_location,
                                placeholder="e.g. London")

    c3, c4, c5 = st.columns([1, 1, 1])
    with c3:
        work_type = st.selectbox("Work type", WORK_TYPES, index=0)
    with c4:
        country_code = st.selectbox(
            "Country", list(COUNTRIES.keys()),
            format_func=lambda code: COUNTRIES[code],
            index=list(COUNTRIES.keys()).index("gb"),
            help="Which country Adzuna searches. Remote results aren't limited by this.",
        )
    with c5:
        limit = st.slider("Results per source", 5, 30, 15)

    searched = st.form_submit_button("🔎 Search jobs", type="primary")

if searched:
    if not title.strip():
        st.warning("Enter a job title to search.")
    else:
        with st.spinner("Searching openings..."):
            run_search(title, location, country_code, work_type, limit)

# --- Results ------------------------------------------------------------------
result = st.session_state.get("job_results")

if not job_search.adzuna_configured():
    job_search.render_setup_notice()

if result is not None:
    for note in result.notices:
        st.caption(f"⚠️ {note}")

    if not result.jobs:
        st.info("No matching openings found. Try a broader title, a different location, "
                "or switch Work type to **Remote**.")
    else:
        st.markdown(
            '<style>'
            '.rb-job-top{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;}'
            '.rb-job-title{font-weight:600;font-size:1.02rem;color:#16233E;line-height:1.25;}'
            '.rb-job-co{color:#2B5A9E;font-size:.92rem;margin-top:1px;}'
            '.rb-badge{flex:none;font-size:.64rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;'
            'padding:3px 9px;border-radius:999px;border:1px solid #cdd6e4;color:#5B6472;background:#f4f6fa;}'
            '.rb-badge.adzuna{color:#1E3F70;border-color:#b9cbe6;background:#eef4fc;}'
            '.rb-badge.remotive{color:#1F7A54;border-color:#bfe3d2;background:#eefaf3;}'
            '.rb-job-meta{display:flex;flex-wrap:wrap;gap:6px 14px;margin-top:8px;color:#5B6472;font-size:.82rem;}'
            '.rb-skills{display:flex;flex-wrap:wrap;gap:6px;margin-top:9px;align-items:center;}'
            '.rb-skills-lbl{font-size:.72rem;font-weight:700;color:#1F7A54;letter-spacing:.04em;}'
            '.rb-chip{font-size:.72rem;font-weight:600;color:#1F7A54;background:#e7f6ee;'
            'border:1px solid #bfe3d2;border-radius:999px;padding:2px 9px;}'
            '.rb-chip-more{font-size:.72rem;color:#5B6472;}'
            '</style>',
            unsafe_allow_html=True,
        )
        st.markdown(f'<p class="rb-eyebrow" style="margin-top:.6rem">'
                    f'{len(result.jobs)} openings</p>', unsafe_allow_html=True)
        if resume.all_skills_flat():
            st.caption("Green chips show which of **your skills** each listing mentions. "
                       "For a full matched-vs-missing breakdown, use **🎯 Match in ATS**.")
        else:
            st.caption("Add skills on the Dashboard to see which of your skills each listing mentions.")

        for i, job in enumerate(result.jobs):
            with st.container(border=True):
                badge_cls = "adzuna" if job.source == "Adzuna" else "remotive"

                meta = []
                if job.location:
                    meta.append(f"📍 {escape(job.location)}")
                if job.salary:
                    meta.append(f"💷 {escape(job.salary)}")
                if job.job_type:
                    meta.append(f"🕑 {escape(job.job_type.title())}")
                if job.posted:
                    meta.append(f"📅 {escape(job.posted)}")
                meta_html = "".join(f"<span>{m}</span>" for m in meta)

                skills_html = ""
                if job.matched_skills:
                    shown = job.matched_skills[:5]
                    chips = "".join(f'<span class="rb-chip">✓ {escape(s)}</span>' for s in shown)
                    extra = len(job.matched_skills) - len(shown)
                    more = f'<span class="rb-chip-more">+{extra} more</span>' if extra > 0 else ""
                    skills_html = ('<div class="rb-skills"><span class="rb-skills-lbl">YOUR SKILLS:</span>'
                                   f'{chips}{more}</div>')

                st.markdown(
                    '<div class="rb-job-top"><div>'
                    f'<div class="rb-job-title">{escape(job.title)}</div>'
                    f'<div class="rb-job-co">{escape(job.company) or "—"}</div></div>'
                    f'<span class="rb-badge {badge_cls}">{escape(job.source)}</span>'
                    '</div>'
                    f'<div class="rb-job-meta">{meta_html}</div>'
                    f'{skills_html}',
                    unsafe_allow_html=True,
                )

                a, b, _sp = st.columns([1.3, 1.3, 2.4])
                link = _safe_link(job.url)
                if link:
                    a.link_button("View & apply ↗", link, width="stretch")
                if resume_text.strip() and job.description:
                    if b.button("🎯 Match in ATS", key=f"match_{i}", width="stretch"):
                        set_job_description(job.description)
                        st.switch_page("pages/2_🎯_ATS_Match.py")
else:
    st.caption("Enter a title and hit **Search jobs** to see live openings.")

render_download_footer(resume)
