"""ATS Match: score the résumé against a job description, and get AI suggestions.

Analysis only -- it reports matched vs missing keywords and read-only advice.
It never edits the résumé or invents skills; "missing" keywords are shown as
information ("add these only if you genuinely have them").
"""
import streamlit as st

from utils import ai_assistant as ai
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
resume_text = resume.searchable_text()

render_hero("Match & tailor", "Match your résumé to a job",
            "Paste a job description to see which keywords your résumé covers — then let AI tailor it.")

if not resume_text.strip():
    st.info("Your résumé is empty. Fill it in on the Dashboard first, then come back.")
    render_download_footer(resume)
    st.stop()

# --- Job description input ----------------------------------------------------
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
    result = analyze(resume_text, jd_text)

    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        st.metric("ATS match score", f"{result.score}%")
        st.progress(result.score / 100)
        st.caption(f"{len(result.matched)} of {result.total_keywords} job keywords found in your résumé.")
    with c2:
        st.metric("Overall text similarity", f"{result.similarity}%")
        st.progress(result.similarity / 100)
        st.caption("TF-IDF similarity between the full texts of your résumé and the job.")

    if result.score >= 75:
        st.success("Strong match — your résumé already covers most of this job's keywords.")
    elif result.score >= 50:
        st.info("Moderate match — strengthen the missing keywords where you genuinely qualify.")
    else:
        st.warning("Low keyword match — see the gaps below.")

    st.markdown(f"#### Matched ({len(result.matched)})")
    if result.matched:
        st.markdown(" ".join(f":green-background[{kw}]" for kw in result.matched))
    else:
        st.caption("None of the job's keywords were found.")

    st.markdown(f"#### Missing ({len(result.missing)})")
    if result.missing:
        st.markdown(" ".join(f":red-background[{kw}]" for kw in result.missing))
        st.caption("In the job but not your résumé. Add them **only if you genuinely have the experience** — "
                   "the app never fabricates skills.")
    else:
        st.caption("Nothing missing — every keyword is already covered.")

# --- AI tailoring: apply-able changes -----------------------------------------
st.divider()
st.markdown('<p class="rb-eyebrow">Assistant</p>', unsafe_allow_html=True)
st.markdown('<p class="rb-panel-title">Tailor your résumé to this job</p>', unsafe_allow_html=True)
st.markdown('<p class="rb-sub">AI rewrites <em>your own</em> content to match this job description. '
            'Review each change and apply what you want — it never invents skills or facts.</p>',
            unsafe_allow_html=True)

if not ai.is_configured():
    ai.render_unavailable_notice()
else:
    jd = get_job_description()
    if not jd.strip():
        st.info("Paste a job description above and analyze it first — the changes are tailored to it.")
    else:
        if st.button("Generate tailored changes", key="gen_changes", type="primary"):
            with st.spinner("Tailoring your résumé to the job..."):
                changes = {"summary": None, "bullets": {}}
                errors = []
                try:
                    changes["summary"] = ai.generate_summary(resume, jd)
                except ai.AIError as exc:
                    errors.append(f"Summary: {exc}")
                for exp in resume.experience:
                    if exp.bullet_points:
                        try:
                            changes["bullets"][exp.id] = ai.rewrite_bullets(
                                exp.job_title, exp.company, exp.bullet_points, jd)
                        except ai.AIError as exc:
                            errors.append(f"{exp.job_title or 'A role'}: {exc}")
                st.session_state["ats_changes"] = changes
                st.session_state["ats_change_errors"] = errors

        for err in st.session_state.get("ats_change_errors", []):
            st.caption(f"Error — {err}")

        changes = st.session_state.get("ats_changes")
        if changes:
            applied_any = False

            # Tailored summary
            summary = changes.get("summary")
            if summary:
                with st.container(border=True):
                    st.markdown("**Tailored professional summary**")
                    st.write(summary)
                    a, d, _ = st.columns([1, 1, 3])
                    if a.button("Apply", key="apply_summary", type="primary", width="stretch"):
                        resume.personal_info.professional_summary = summary
                        refresh_field("personal_summary")
                        changes["summary"] = None
                        applied_any = True
                    if d.button("Dismiss", key="dismiss_summary", width="stretch"):
                        changes["summary"] = None
                        applied_any = True

            # Tailored bullets, per role
            exp_by_id = {e.id: e for e in resume.experience}
            for exp_id, new_bullets in list(changes.get("bullets", {}).items()):
                exp = exp_by_id.get(exp_id)
                if not exp or not new_bullets:
                    continue
                with st.container(border=True):
                    label = " · ".join(x for x in [exp.job_title, exp.company] if x) or "Experience"
                    st.markdown(f"**Tailored bullets — {label}**")
                    for b in new_bullets:
                        st.markdown(f"- {b}")
                    a, d, _ = st.columns([1, 1, 3])
                    if a.button("Apply", key=f"apply_b_{exp_id}", type="primary", width="stretch"):
                        exp.bullet_points = new_bullets
                        refresh_field(f"exp_b_{exp_id}")
                        changes["bullets"].pop(exp_id, None)
                        applied_any = True
                    if d.button("Dismiss", key=f"dismiss_b_{exp_id}", width="stretch"):
                        changes["bullets"].pop(exp_id, None)
                        applied_any = True

            if not summary and not changes.get("bullets"):
                st.success("All tailored changes handled. Your résumé and downloads are updated.")
                st.session_state.pop("ats_changes", None)

            if applied_any:
                st.rerun()

render_download_footer(resume)
