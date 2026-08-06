"""ATS Match: semantic résumé/JD scoring + evidence-based AI tailoring.

Two halves, both grounded and honest:

  * The breakdown -- an embedding-based match report: overall %, which skills
    matched vs are missing, how well experience covers the JD's responsibilities
    (with the supporting bullet), and whether education meets the requirement.
    "Missing" is information, never a prompt to fabricate.

  * The suggestions -- an agent pipeline (retrieve -> evidence -> write) turns the
    report into apply-able cards: each one names the JD requirement that caused
    it, the exact résumé bullet it edits, and a confidence. The AI only rephrases
    bullets you already wrote; it never invents skills, tools, or metrics.
"""
from html import escape

import streamlit as st

from utils import agents
from utils import ai_assistant as ai
from utils import ats_analyzer, embeddings
from utils.ats_analyzer import analyze
from utils.resume_parser import extract_text
from utils.session_manager import (
    get_job_description,
    get_resume_data,
    init_session_state,
    refresh_field,
    set_job_description,
)
from utils.theme import inject_theme, render_download_footer, render_header, render_hero

inject_theme()
render_header("ats")
init_session_state()
resume = get_resume_data()

# --- Page-local styling (breakdown tiles, chips, evidence, suggestion cards) ---
st.markdown(
    """
<style>
.rb-score { display:flex; align-items:baseline; gap:.7rem; margin:.2rem 0 .1rem; }
.rb-score-num { font-family:var(--mono); font-weight:600; font-size:3.1rem; line-height:1;
  color:var(--text); letter-spacing:-.03em; font-feature-settings:"tnum"; }
.rb-score-pct { font-family:var(--mono); font-size:1.1rem; color:var(--muted); }
.rb-score-verdict { font-size:.92rem; color:var(--muted); margin:.1rem 0 .2rem; }
.rb-tiles { display:grid; grid-template-columns:repeat(3,1fr); gap:.7rem; margin:.4rem 0 .2rem; }
.rb-tile { border:1px solid var(--line); border-radius:12px; background:var(--surface); padding:.7rem .8rem; }
.rb-tile-label { font-family:var(--mono); font-size:.6rem; font-weight:500; letter-spacing:.14em;
  text-transform:uppercase; color:var(--muted); margin:0 0 .35rem; }
.rb-tile-num { font-family:var(--mono); font-weight:600; font-size:1.5rem; color:var(--text);
  font-feature-settings:"tnum"; line-height:1; }
.rb-tile-num.na { color:var(--subtle); font-size:1.05rem; }
.rb-bar { height:5px; border-radius:99px; background:var(--line); margin-top:.5rem; overflow:hidden; }
.rb-bar > i { display:block; height:100%; border-radius:99px; background:var(--accent);
  animation:rb-fill .5s var(--ease) both; }
@keyframes rb-fill { from{ transform:scaleX(0); transform-origin:left; } to{ transform:scaleX(1); } }
.rb-tile-note { font-size:.74rem; color:var(--muted); margin:.4rem 0 0; line-height:1.4; }
.rb-chips { display:flex; flex-wrap:wrap; gap:.4rem; margin:.2rem 0 .1rem; }
.rb-chip-ok, .rb-chip-miss { font-family:var(--mono); font-size:.74rem; font-weight:500;
  padding:.2rem .55rem; border-radius:7px; letter-spacing:-.01em; }
.rb-chip-ok { background:var(--wash); border:1px solid var(--chip-border); color:var(--accent-ink); }
.rb-chip-miss { background:#FFF1F2; border:1px solid #FECDD3; color:var(--gap); }
.rb-ev { border-top:1px solid var(--line); padding:.55rem 0 .1rem; }
.rb-ev:first-child { border-top:0; }
.rb-ev-req { font-size:.85rem; color:var(--text); font-weight:500; }
.rb-ev-sim { font-family:var(--mono); font-size:.68rem; color:var(--muted); float:right; }
.rb-ev-bullet { font-size:.8rem; color:var(--muted); margin:.2rem 0 0; padding-left:.8rem;
  border-left:2px solid var(--chip-border); line-height:1.45; }
.rb-ev-bullet.none { border-left-color:#FECDD3; color:var(--subtle); font-style:italic; }
/* Suggestion cards */
.rb-sg-badge { display:inline-block; font-family:var(--mono); font-size:.6rem; font-weight:600;
  letter-spacing:.1em; text-transform:uppercase; padding:.16rem .5rem; border-radius:6px; }
.rb-sg-badge.hi { background:var(--wash); border:1px solid var(--chip-border); color:var(--accent-ink); }
.rb-sg-badge.mid { background:#F4F4F5; border:1px solid var(--line); color:var(--text); }
.rb-sg-badge.lo { background:transparent; border:1px solid var(--line); color:var(--muted); }
.rb-sg-because { font-size:.78rem; color:var(--muted); margin:.5rem 0 .1rem; }
.rb-sg-because b { color:var(--text); font-weight:600; }
.rb-sg-target { font-family:var(--mono); font-size:.66rem; letter-spacing:.06em; text-transform:uppercase;
  color:var(--muted); margin:.45rem 0 .3rem; }
.rb-sg-old { font-size:.82rem; color:var(--subtle); text-decoration:line-through;
  text-decoration-color:#FECDD3; line-height:1.5; margin:0 0 .25rem; }
.rb-sg-new { font-size:.9rem; color:var(--text); line-height:1.55; padding-left:.7rem;
  border-left:2px solid var(--accent); }
</style>
""",
    unsafe_allow_html=True,
)

render_hero("Match & tailor", "Match your résumé to a job",
            "Paste a job description for a semantic match breakdown — skills, experience, and "
            "education — then apply evidence-based AI edits to your own bullets.")

if not resume.searchable_text().strip():
    st.info("Your résumé is empty. Fill it in on the Dashboard first, then come back.")
    render_download_footer(resume)
    st.stop()


# --- Helpers ------------------------------------------------------------------

def _bar(pct: int) -> str:
    return f'<div class="rb-bar"><i style="width:{max(0, min(100, pct))}%"></i></div>'


def _tile(label: str, score: "ats_analyzer.SectionScore | int", *, specified: bool = True) -> str:
    """One sub-score tile. Shows n/a when the JD doesn't specify the section."""
    if isinstance(score, ats_analyzer.SectionScore):
        specified, value = score.specified, score.score
    else:
        value = int(score)
    if not specified:
        return (f'<div class="rb-tile"><p class="rb-tile-label">{escape(label)}</p>'
                f'<div class="rb-tile-num na">n/a</div>'
                f'<p class="rb-tile-note">Not specified in this job.</p></div>')
    return (f'<div class="rb-tile"><p class="rb-tile-label">{escape(label)}</p>'
            f'<div class="rb-tile-num">{value}<span style="font-size:.9rem;color:var(--muted)">%</span></div>'
            f'{_bar(value)}</div>')


def _render_report(report: "ats_analyzer.MatchReport") -> None:
    # Overall score + verdict
    if report.overall >= 70:
        verdict = "Strong match — your résumé already speaks to most of this job."
    elif report.overall >= 45:
        verdict = "Moderate match — the breakdown below shows where to strengthen it."
    else:
        verdict = "Low match — see which skills and responsibilities are light."
    st.markdown(
        f'<div class="rb-score"><span class="rb-score-num">{report.overall}'
        f'<span class="rb-score-pct">%</span></span></div>'
        f'<p class="rb-score-verdict">{escape(verdict)}</p>',
        unsafe_allow_html=True,
    )
    if not report.semantic:
        st.caption("Keyword-based estimate. Add a free `EMBEDDINGS_API_KEY` (Gemini) for synonym-aware semantic matching.")

    # Sub-score tiles: a skills-coverage %, experience match, education match
    skills_pct = (round(len(report.skills_matched) / report.skills_total * 100)
                  if report.skills_total else 0)
    st.markdown(
        '<div class="rb-tiles">'
        + _tile("Skills coverage", skills_pct, specified=report.skills_total > 0)
        + _tile("Experience match", report.experience)
        + _tile("Education match", report.education)
        + "</div>",
        unsafe_allow_html=True,
    )

    # Skills matched / missing
    st.markdown(f"###### Skills matched ({len(report.skills_matched)})")
    if report.skills_matched:
        chips = "".join(
            f'<span class="rb-chip-ok" title="matches your: {escape(m.resume_skill)}">{escape(m.jd_skill)}</span>'
            for m in report.skills_matched
        )
        st.markdown(f'<div class="rb-chips">{chips}</div>', unsafe_allow_html=True)
    else:
        st.caption("None of the job's named skills were found in your résumé.")

    st.markdown(f"###### Skills missing ({len(report.skills_missing)})")
    if report.skills_missing:
        chips = "".join(f'<span class="rb-chip-miss">{escape(s)}</span>' for s in report.skills_missing)
        st.markdown(f'<div class="rb-chips">{chips}</div>', unsafe_allow_html=True)
        st.caption("In the job, not in your résumé. Add them **only if you genuinely have them** — "
                   "the app never fabricates skills.")
    else:
        st.caption("Every named skill in the job is already covered.")

    # Experience evidence: JD responsibility -> best supporting bullet
    evidence = [m for m in report.requirement_matches if m.best_bullet][:5]
    if evidence:
        st.markdown("###### How your experience lines up")
        rows = ""
        for m in evidence:
            sim = ats_analyzer._calibrate(m.similarity) if report.semantic else int(round(m.similarity * 100))
            rows += (
                f'<div class="rb-ev"><span class="rb-ev-sim">{sim}% match</span>'
                f'<div class="rb-ev-req">{escape(m.requirement)}</div>'
                f'<p class="rb-ev-bullet">{escape(m.best_bullet)}</p></div>'
            )
        st.markdown(rows, unsafe_allow_html=True)

    if report.education.specified:
        st.caption(f"**Education:** {report.education.detail}")


def _apply_suggestion(sg: "agents.Suggestion") -> bool:
    """Write a rewrite back to the exact bullet it came from, and refresh its form field.

    Returns False (a no-op) if the target bullet can't be located -- e.g. the
    résumé was edited after the suggestion was generated -- so a stale card never
    corrupts unrelated content.
    """
    entries = resume.experience if sg.source == "experience" else resume.projects
    entry = next((e for e in entries if e.id == sg.entry_id), None)
    if entry is None or not (0 <= sg.bullet_index < len(entry.bullet_points)):
        return False
    entry.bullet_points[sg.bullet_index] = sg.proposed
    refresh_field(f"{'exp' if sg.source == 'experience' else 'proj'}_b_{sg.entry_id}")
    return True


# --- Job description input -----------------------------------------------------
uploaded = st.file_uploader("Upload a job description (optional)", type=["pdf", "docx", "txt"])
if uploaded is not None:
    try:
        set_job_description(extract_text(uploaded.getvalue(), uploaded.name))
    except Exception as exc:  # noqa: BLE001
        st.error(f"Couldn't read that file: {exc}")

with st.form("jd_form"):
    jd_text = st.text_area("Job description", value=get_job_description(), height=220,
                           placeholder="Paste the full job description here...")
    analyze_clicked = st.form_submit_button("Analyze match", type="primary")

if analyze_clicked:
    set_job_description(jd_text)
    if not jd_text.strip():
        st.warning("Paste or upload a job description first.")
        st.stop()
    with st.spinner("Reading the job and matching your résumé..."):
        st.session_state["ats_report"] = analyze(resume, jd_text)
    # A fresh analysis invalidates any previously generated suggestions.
    st.session_state.pop("ats_suggestions", None)
    st.session_state.pop("ats_suggest_error", None)

report = st.session_state.get("ats_report")
if report is not None:
    st.divider()
    _render_report(report)

# --- Evidence-based AI tailoring ----------------------------------------------
st.divider()
st.markdown('<p class="rb-eyebrow">Assistant</p>', unsafe_allow_html=True)
st.markdown('<p class="rb-panel-title">Evidence-based tailoring</p>', unsafe_allow_html=True)
st.markdown('<p class="rb-sub">The AI retrieves the résumé bullets most relevant to this job and rewrites '
            '<em>only those</em> — each suggestion shows the job requirement that prompted it, the exact bullet '
            'it edits, and a confidence. It never invents skills or facts.</p>',
            unsafe_allow_html=True)

if not ai.is_configured():
    ai.render_unavailable_notice()
else:
    jd = get_job_description()
    if not jd.strip():
        st.info("Paste a job description above and analyze it first — the suggestions are tailored to it.")
    else:
        if st.button("Generate suggestions", key="gen_suggestions", type="primary"):
            with st.spinner("Retrieving relevant bullets and tailoring them..."):
                try:
                    st.session_state["ats_suggestions"] = agents.generate_suggestions(resume, jd)
                    st.session_state.pop("ats_suggest_error", None)
                except ai.AIError as exc:
                    st.session_state["ats_suggestions"] = []
                    st.session_state["ats_suggest_error"] = str(exc)

        err = st.session_state.get("ats_suggest_error")
        if err:
            st.error(f"Couldn't generate suggestions: {err}")

        if "ats_suggestions" in st.session_state:
            suggestions = st.session_state["ats_suggestions"]
            if not suggestions and not err:
                st.info("No confident, evidence-backed edits to suggest — your bullets already align, or "
                        "none clearly match this job's requirements (the AI won't invent a match).")

            _badge_class = {"High": "hi", "Medium": "mid", "Low": "lo"}
            applied_any = False
            for i, sg in enumerate(list(suggestions)):
                with st.container(border=True):
                    st.markdown(
                        f'<span class="rb-sg-badge {_badge_class.get(sg.confidence, "lo")}">'
                        f'{escape(sg.confidence)} confidence</span>'
                        f'<p class="rb-sg-because">Because the job asks: '
                        f'<b>{escape(sg.jd_requirement)}</b></p>'
                        f'<p class="rb-sg-target">Editing your bullet · {escape(sg.role_label)}</p>'
                        f'<p class="rb-sg-old">{escape(sg.original)}</p>'
                        f'<p class="rb-sg-new">{escape(sg.proposed)}</p>',
                        unsafe_allow_html=True,
                    )
                    key = f"{sg.source}_{sg.entry_id}_{sg.bullet_index}"
                    a, d, _ = st.columns([1, 1, 3])
                    if a.button("Apply", key=f"apply_{key}", type="primary", width="stretch"):
                        if _apply_suggestion(sg):
                            suggestions.remove(sg)
                            applied_any = True
                    if d.button("Dismiss", key=f"dismiss_{key}", width="stretch"):
                        suggestions.remove(sg)
                        applied_any = True

            if applied_any:
                st.session_state["ats_suggestions"] = suggestions
                st.rerun()

render_download_footer(resume)
