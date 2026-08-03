"""ATS Match: score the résumé against a job description, and get AI suggestions.

Analysis only -- it reports matched vs missing keywords and read-only advice.
It never edits the résumé or invents skills; "missing" keywords are shown as
information ("add these only if you genuinely have them").
"""
import streamlit as st

from utils import ai_assistant as ai
from utils.ats_analyzer import analyze
from utils.resume_parser import extract_text
from utils.session_manager import get_job_description, get_resume_data, init_session_state, set_job_description
from utils.theme import inject_theme, render_download_footer, render_header

inject_theme()
render_header("ats")
init_session_state()
resume = get_resume_data()
resume_text = resume.searchable_text()

st.markdown('<p class="rb-eyebrow">Tailoring</p>', unsafe_allow_html=True)
st.markdown('<p class="rb-panel-title">Match your résumé to a job</p>', unsafe_allow_html=True)
st.markdown('<p class="rb-sub">Paste a job description to see which of its keywords your résumé already covers.</p>',
            unsafe_allow_html=True)

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
    analyze_clicked = st.form_submit_button("🔍 Analyze match", type="primary")

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

    st.markdown(f"#### ✅ Matched ({len(result.matched)})")
    if result.matched:
        st.markdown(" ".join(f":green-background[{kw}]" for kw in result.matched))
    else:
        st.caption("None of the job's keywords were found.")

    st.markdown(f"#### ❌ Missing ({len(result.missing)})")
    if result.missing:
        st.markdown(" ".join(f":red-background[{kw}]" for kw in result.missing))
        st.caption("In the job but not your résumé. Add them **only if you genuinely have the experience** — "
                   "the app never fabricates skills.")
    else:
        st.caption("Nothing missing — every keyword is already covered.")

# --- AI suggestions -----------------------------------------------------------
st.divider()
st.markdown('<p class="rb-eyebrow">Assistant</p>', unsafe_allow_html=True)
st.markdown('<p class="rb-panel-title">AI résumé suggestions</p>', unsafe_allow_html=True)
st.markdown('<p class="rb-sub">Read-only advice on your whole résumé. It never edits or invents content.</p>',
            unsafe_allow_html=True)

if not ai.is_configured():
    ai.render_unavailable_notice()
else:
    jd = get_job_description()
    if jd.strip():
        st.caption("Suggestions consider the job description above.")
    if st.button("Get AI suggestions", key="gen_suggestions", type="primary"):
        with st.spinner("Reviewing your résumé..."):
            try:
                st.session_state["ai_suggestions"] = ai.suggest_improvements(resume, jd)
            except ai.AIError as exc:
                st.session_state.pop("ai_suggestions", None)
                st.error(str(exc))
    suggestions = st.session_state.get("ai_suggestions")
    if suggestions:
        st.markdown(suggestions)
        if st.button("✕ Clear", key="clear_suggestions"):
            st.session_state.pop("ai_suggestions", None)
            st.rerun()

render_download_footer(resume)
