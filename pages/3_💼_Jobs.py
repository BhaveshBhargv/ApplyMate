"""Jobs: search live openings by title, location and work type; get apply links.

Inputs (job title, location, work type, country) are sent to free, official job
APIs via utils.job_search. Results are shown as cards, each linking to the real
posting. Each card lists which of the job's named skills the résumé already
covers and which are missing, and a one-click handoff loads the job's full
description into ATS Match for a semantic breakdown. Nothing is applied to or
sent on the user's behalf -- links only.
"""
from html import escape

import streamlit as st

from utils import ats_analyzer, job_search
from utils.job_search import (
    COUNTRIES,
    INDUSTRIES,
    WORK_TYPES,
    posted_days,
    search_jobs,
    skills_in_text,
)
from utils.session_manager import get_resume_data, init_session_state, set_job_description
from utils.theme import inject_theme, render_download_footer, render_header, render_hero

inject_theme()
render_header("jobs")
init_session_state()
resume = get_resume_data()
resume_text = resume.searchable_text()

render_hero("Find roles", "Find your next role",
            "Search live openings and jump straight to the official posting — we never apply for you, just links.")

# Prefill from the résumé: most recent role title + personal location.
default_title = resume.experience[0].job_title if resume.experience else ""
default_location = resume.personal_info.location or ""


def _safe_link(url: str) -> str:
    """Return an http(s) URL safe to use as a link target, else empty."""
    url = (url or "").strip()
    return url if url.startswith(("http://", "https://")) else ""


PAGE_SIZE = 10


def run_search(title: str, location: str, country: str, work_type: str,
               industry: str = "Any") -> None:
    """Fetch jobs; per job, split its named skills into matched vs missing; sort."""
    result = search_jobs(title, location, country, work_type, industry)
    has_resume = bool(resume_text.strip())
    for job in result.jobs:
        # The job's own named skills (from its title + description), then split by
        # what the résumé already covers. On short/absent descriptions (e.g. an
        # Adzuna snippet) this is naturally sparse -- honest, not inflated.
        jd_skills = ats_analyzer.extract_keywords(f"{job.title} {job.description}", max_keywords=12)
        if has_resume:
            job.matched_skills = skills_in_text(jd_skills, resume_text)
            covered = {s.lower() for s in job.matched_skills}
            job.missing_skills = [s for s in jd_skills if s.lower() not in covered]
        job.freshness_days = posted_days(job.posted)

    # Most of your skills covered first; ties broken by freshest.
    result.jobs.sort(key=lambda j: (
        -len(j.matched_skills),
        j.freshness_days if j.freshness_days is not None else 10**6,
    ))
    result.jobs = result.jobs[:120]  # keep the strongest ~12 pages
    st.session_state["job_results"] = result
    st.session_state["job_page"] = 0


def _freshness_label(days) -> str:
    if days is None:
        return ""
    if days <= 0:
        return "today"
    if days == 1:
        return "1d ago"
    if days < 30:
        return f"{days}d ago"
    if days < 365:
        return f"{days // 30}mo ago"
    return "1y+ ago"


# Auto-search handoff from the Dashboard's "Search jobs for this résumé" button.
if st.session_state.pop("auto_job_search", False) and default_title.strip():
    with st.spinner("Searching openings for your résumé..."):
        run_search(default_title, default_location, "gb", "Any")

# --- Search form --------------------------------------------------------------
with st.form("job_search_form"):
    c1, c2 = st.columns([1.2, 1])
    with c1:
        title = st.text_input("Job title", value=default_title,
                              placeholder="e.g. Data Engineer")
    with c2:
        location = st.text_input("Location", value=default_location,
                                placeholder="e.g. London")

    c3, c4, c5 = st.columns(3)
    with c3:
        work_type = st.selectbox("Work type", WORK_TYPES, index=0)
    with c4:
        industry = st.selectbox("Industry", list(INDUSTRIES.keys()), index=0,
                                help="Narrows results to a job category. Leave as Any to search all.")
    with c5:
        country_code = st.selectbox(
            "Country", list(COUNTRIES.keys()),
            format_func=lambda code: COUNTRIES[code],
            index=list(COUNTRIES.keys()).index("gb"),
            help="Which country Adzuna searches. Remote results aren't limited by this.",
        )

    searched = st.form_submit_button("Search jobs", type="primary")

if searched:
    if not title.strip():
        st.warning("Enter a job title to search.")
    else:
        with st.spinner("Searching openings..."):
            run_search(title, location, country_code, work_type, industry)

# --- Results ------------------------------------------------------------------
result = st.session_state.get("job_results")

if not job_search.adzuna_configured():
    job_search.render_setup_notice()

if result is not None:
    for note in result.notices:
        st.caption(f"Note — {note}")

    if not result.jobs:
        st.info("No matching openings found. Try a broader title, a different location, "
                "or switch Work type to **Remote**.")
    else:
        st.markdown(
            '<style>'
            '.rb-job-top{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;}'
            '.rb-job-title{font-family:var(--display);font-weight:700;font-size:1.05rem;'
            'letter-spacing:-.02em;color:var(--text);line-height:1.2;}'
            '.rb-job-co{color:var(--muted);font-weight:500;font-size:.88rem;margin-top:2px;}'
            '.rb-tags{display:flex;flex-direction:column;align-items:flex-end;gap:5px;flex:none;}'
            '.rb-badge{font-family:var(--mono);font-size:.6rem;font-weight:500;letter-spacing:.12em;'
            'text-transform:uppercase;padding:3px 8px;border-radius:6px;border:1px solid var(--line);color:var(--muted);white-space:nowrap;}'
            '.rb-badge.adzuna{color:var(--accent-ink);border-color:var(--chip-border);background:var(--wash);}'
            '.rb-job-meta{font-family:var(--mono);margin-top:9px;color:var(--muted);font-size:.75rem;letter-spacing:.01em;}'
            '.rb-job-meta .rb-fresh{color:var(--accent-ink);font-weight:600;}'
            '.rb-skills{display:flex;flex-wrap:wrap;gap:6px;margin-top:9px;align-items:center;}'
            '.rb-skills-lbl{font-family:var(--mono);font-size:.62rem;font-weight:500;color:var(--muted);'
            'letter-spacing:.12em;text-transform:uppercase;min-width:58px;}'
            '.rb-chip{font-size:.72rem;font-weight:500;color:var(--accent-ink);background:var(--wash);'
            'border:1px solid var(--chip-border);border-radius:6px;padding:2px 8px;}'
            '.rb-chip-miss{font-size:.72rem;font-weight:500;color:var(--gap);background:#FFF1F2;'
            'border:1px solid #FECDD3;border-radius:6px;padding:2px 8px;}'
            '.rb-chip-more{font-family:var(--mono);font-size:.68rem;color:var(--muted);}'
            '</style>',
            unsafe_allow_html=True,
        )
        total = len(result.jobs)
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(max(st.session_state.get("job_page", 0), 0), total_pages - 1)
        st.session_state["job_page"] = page
        start = page * PAGE_SIZE
        page_jobs = result.jobs[start:start + PAGE_SIZE]

        st.markdown(f'<p class="rb-eyebrow" style="margin-top:.6rem">'
                    f'{total} openings &middot; showing {start + 1}–{start + len(page_jobs)}</p>',
                    unsafe_allow_html=True)
        if resume_text.strip():
            st.caption("Sorted by **most skills covered**, then **freshest**. Green chips are the job's "
                       "skills you already have; red are ones it names that you don't. For the full "
                       "semantic breakdown use **Match in ATS**.")
        else:
            st.caption("Sorted by **freshest** first. Fill in your résumé on the Dashboard to see which "
                       "of each job's skills you match and which are missing.")

        for offset, job in enumerate(page_jobs):
            i = start + offset
            with st.container(border=True):
                badge_cls = "adzuna" if job.source == "Adzuna" else ""

                meta = []
                if job.location:
                    meta.append(escape(job.location))
                if job.salary:
                    meta.append(escape(job.salary))
                if job.job_type:
                    meta.append(escape(job.job_type.title()))
                fresh = _freshness_label(job.freshness_days)
                if fresh:
                    css = "rb-fresh" if (job.freshness_days is not None and job.freshness_days <= 7) else ""
                    meta.append(f'<span class="{css}">{escape(fresh)}</span>')
                elif job.posted:
                    meta.append(escape(job.posted))
                meta_html = " · ".join(meta)

                def _skill_row(label: str, skills: list, chip_cls: str) -> str:
                    if not skills:
                        return ""
                    shown = skills[:6]
                    chips = "".join(f'<span class="{chip_cls}">{escape(s)}</span>' for s in shown)
                    extra = len(skills) - len(shown)
                    more = f'<span class="rb-chip-more">+{extra}</span>' if extra > 0 else ""
                    return (f'<div class="rb-skills"><span class="rb-skills-lbl">{label}</span>'
                            f'{chips}{more}</div>')

                skills_html = (_skill_row("Matches", job.matched_skills, "rb-chip")
                               + _skill_row("Missing", job.missing_skills, "rb-chip-miss"))

                st.markdown(
                    '<div class="rb-job-top"><div>'
                    f'<div class="rb-job-title">{escape(job.title)}</div>'
                    f'<div class="rb-job-co">{escape(job.company) or "—"}</div></div>'
                    f'<div class="rb-tags">'
                    f'<span class="rb-badge {badge_cls}">{escape(job.source)}</span></div>'
                    '</div>'
                    f'<div class="rb-job-meta">{meta_html}</div>'
                    f'{skills_html}',
                    unsafe_allow_html=True,
                )

                a, b, _sp = st.columns([1.3, 1.3, 2.4])
                link = _safe_link(job.url)
                if link:
                    a.link_button("View & apply", link, width="stretch")
                if resume_text.strip() and (job.description or link):
                    if b.button("Match in ATS", key=f"match_{i}", width="stretch"):
                        with st.spinner("Loading the full job description..."):
                            set_job_description(job_search.fetch_full_description(job))
                        st.switch_page("pages/2_🎯_ATS_Match.py")

        # --- Pager ---------------------------------------------------------
        if total_pages > 1:
            prev_col, mid_col, next_col = st.columns([1, 2, 1])
            if prev_col.button("Previous", key="job_prev", disabled=page == 0, width="stretch"):
                st.session_state["job_page"] = page - 1
                st.rerun()
            mid_col.markdown(
                f'<p class="rb-num" style="text-align:center;color:var(--muted);margin:0.5rem 0;font-size:.82rem">'
                f'Page {page + 1} / {total_pages}</p>', unsafe_allow_html=True)
            if next_col.button("Next", key="job_next", disabled=page >= total_pages - 1, width="stretch"):
                st.session_state["job_page"] = page + 1
                st.rerun()
else:
    st.caption("Enter a title and hit **Search jobs** to see live openings.")

render_download_footer(resume)
