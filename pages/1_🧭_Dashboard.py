"""Dashboard: edit on the left, live résumé preview on the right, download below.

This single page replaces the old multi-page flow (Home, Upload, Personal,
Education, Experience, Projects, Skills, Review). Section editors come from
utils.forms; the live sheet from utils.resume_html; export from
docx_export / pdf_export.
"""
import streamlit as st

from utils import forms
from utils.resume_html import render_resume_html
from utils.session_manager import get_resume_data, init_session_state
from utils.theme import inject_theme, render_download_footer, render_header

inject_theme()
render_header("dashboard")
init_session_state()
resume = get_resume_data()

edit_col, preview_col = st.columns([1, 1.05], gap="large")

# --- Left: editor -------------------------------------------------------------
with edit_col:
    st.markdown('<p class="rb-eyebrow">Workbench</p>', unsafe_allow_html=True)
    st.markdown('<p class="rb-panel-title">Build your résumé</p>', unsafe_allow_html=True)
    st.markdown('<p class="rb-sub">Fill a section, hit Save, and watch the sheet update.</p>',
                unsafe_allow_html=True)

    role = resume.experience[0].job_title if resume.experience else ""
    if st.button("🔎 Search jobs for this résumé", key="dash_find_jobs",
                 help="Opens the Jobs page and searches openings for your most recent role."):
        st.session_state["auto_job_search"] = True
        st.switch_page("pages/3_💼_Jobs.py")
    if not role:
        st.caption("Add a role under **Experience** to search by job title.")

    tab_personal, tab_edu, tab_exp, tab_proj, tab_skills, tab_import = st.tabs(
        ["Personal", "Education", "Experience", "Projects", "Skills", "Import"]
    )
    with tab_personal:
        forms.render_personal()
    with tab_edu:
        forms.render_education()
    with tab_exp:
        forms.render_experience()
    with tab_proj:
        forms.render_projects()
    with tab_skills:
        forms.render_skills()
    with tab_import:
        forms.render_import()

# --- Right: live preview ------------------------------------------------------
with preview_col:
    st.markdown('<p class="rb-eyebrow">Live preview</p>', unsafe_allow_html=True)
    st.markdown('<p class="rb-panel-title">Your résumé, on paper</p>', unsafe_allow_html=True)
    st.markdown('<p class="rb-sub">This is exactly what downloads as Word or PDF.</p>', unsafe_allow_html=True)
    st.markdown(render_resume_html(resume), unsafe_allow_html=True)

# --- Footer: download ---------------------------------------------------------
render_download_footer(resume)
