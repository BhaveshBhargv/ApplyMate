"""Dashboard: edit on the left, live résumé preview on the right, download below.

This single page replaces the old multi-page flow (Home, Upload, Personal,
Education, Experience, Projects, Skills, Review). Section editors come from
utils.forms; the live sheet from utils.resume_html; export from
docx_export / pdf_export.
"""
import streamlit as st

from utils import forms, job_roles
from utils.resume_html import render_resume_html
from utils.session_manager import get_resume_data, init_session_state
from utils.theme import (
    inject_theme,
    render_download_footer,
    render_header,
    render_hero,
    section_picker,
)

inject_theme()
render_header("dashboard")
init_session_state()
forms.process_pending_import()   # a queued import must land before anything reads the résumé
resume = get_resume_data()

render_hero("Build résumé", "Build your résumé",
            "Fill a section, hit Save, and watch the sheet update live on the right.")

edit_col, preview_col = st.columns([1, 1.05], gap="small")

# --- Left: editor -------------------------------------------------------------
with edit_col:
    role = job_roles.latest_title(resume)
    if st.button("Search jobs for this résumé", key="dash_find_jobs",
                 help="Opens the Jobs page and searches openings for your most recent role."):
        st.session_state["auto_job_search"] = True
        st.switch_page("pages/3_💼_Jobs.py")
    if not role:
        st.caption("Add a role under **Experience** to search by job title.")

    with st.container(key="dash_tabs"):
        section = section_picker(
            ["Personal", "Education", "Experience", "Projects", "Skills", "Import"], "dash_section")
        {
            "Personal": forms.render_personal,
            "Education": forms.render_education,
            "Experience": forms.render_experience,
            "Projects": forms.render_projects,
            "Skills": forms.render_skills,
            "Import": forms.render_import,
        }[section]()

# --- Right: live preview ------------------------------------------------------
with preview_col:
    st.markdown('<p class="rb-eyebrow">Preview</p>', unsafe_allow_html=True)
    st.markdown(render_resume_html(resume), unsafe_allow_html=True)

# --- Footer: download ---------------------------------------------------------
render_download_footer(resume)
