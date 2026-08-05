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
    """Fetch jobs, score each for résumé match + freshness, sort, reset paging."""
    result = search_jobs(title, location, country, work_type, industry)
    my_skills = resume.all_skills_flat()
    # Matching 5+ of your skills counts as a full match, so users with long skill
    # lists aren't penalised. Score is skill-overlap over title + description.
    target = max(1, min(len(my_skills), 5))
    for job in result.jobs:
        if my_skills:
            job.matched_skills = skills_in_text(my_skills, f"{job.title} {job.description}")
            job.match_score = min(100, round(100 * len(job.matched_skills) / target))
        job.freshness_days = posted_days(job.posted)

    # Best matches first; ties broken by freshest. Jobs with no score sink.
    result.jobs.sort(key=lambda j: (
        -(j.match_score if j.match_score is not None else -1),
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
            '.rb-match{font-family:var(--mono);font-size:.64rem;font-weight:600;letter-spacing:.04em;'
            'padding:3px 8px;border-radius:6px;white-space:nowrap;}'
            '.rb-match.hi{color:#fff;background:var(--accent);border:1px solid var(--accent);}'
            '.rb-match.mid{color:var(--accent-ink);background:var(--wash);border:1px solid var(--chip-border);}'
            '.rb-match.lo{color:var(--muted);border:1px solid var(--line);}'
            '.rb-job-meta{font-family:var(--mono);margin-top:9px;color:var(--muted);font-size:.75rem;letter-spacing:.01em;}'
            '.rb-job-meta .rb-fresh{color:var(--accent-ink);font-weight:600;}'
            '.rb-skills{display:flex;flex-wrap:wrap;gap:6px;margin-top:11px;align-items:center;}'
            '.rb-skills-lbl{font-family:var(--mono);font-size:.62rem;font-weight:500;color:var(--muted);'
            'letter-spacing:.12em;text-transform:uppercase;}'
            '.rb-chip{font-size:.72rem;font-weight:500;color:var(--accent-ink);background:var(--wash);'
            'border:1px solid var(--chip-border);border-radius:6px;padding:2px 8px;}'
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
        if resume.all_skills_flat():
            st.caption("Sorted by **best match** to your résumé, then **freshest**. Chips show your "
                       "skills each listing mentions; for a full breakdown use **Match in ATS**.")
        else:
            st.caption("Sorted by **freshest** first. Add skills on the Dashboard to rank by how well "
                       "each listing matches you.")

        for offset, job in enumerate(page_jobs):
            i = start + offset
            with st.container(border=True):
                badge_cls = "adzuna" if job.source == "Adzuna" else ""

                match_html = ""
                if job.match_score is not None:
                    tier = "hi" if job.match_score >= 60 else "mid" if job.match_score >= 30 else "lo"
                    match_html = f'<span class="rb-match {tier}">{job.match_score}% match</span>'

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

                skills_html = ""
                if job.matched_skills:
                    shown = job.matched_skills[:5]
                    chips = "".join(f'<span class="rb-chip">{escape(s)}</span>' for s in shown)
                    extra = len(job.matched_skills) - len(shown)
                    more = f'<span class="rb-chip-more">+{extra} more</span>' if extra > 0 else ""
                    skills_html = ('<div class="rb-skills"><span class="rb-skills-lbl">Your skills</span>'
                                   f'{chips}{more}</div>')

                st.markdown(
                    '<div class="rb-job-top"><div>'
                    f'<div class="rb-job-title">{escape(job.title)}</div>'
                    f'<div class="rb-job-co">{escape(job.company) or "—"}</div></div>'
                    f'<div class="rb-tags">{match_html}'
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
                if resume_text.strip() and job.description:
                    if b.button("Match in ATS", key=f"match_{i}", width="stretch"):
                        set_job_description(job.description)
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
