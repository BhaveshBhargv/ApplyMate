"""Dashboard: edit on the left, live résumé preview on the right, download below.

This single page replaces the old multi-page flow (Home, Upload, Personal,
Education, Experience, Projects, Skills, Review). Section editors come from
utils.forms; the live sheet from utils.resume_html; export from
docx_export / pdf_export.
"""
import streamlit as st

from utils import account, auth, db, forms, job_roles
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


def _primary_controls() -> None:
    """Primary résumé: the one loaded first when you log in. "Set as primary" marks the
    résumé being edited; "Load primary" brings it back into the builder to keep editing."""
    if not auth.is_logged_in():
        return
    account.autosave()   # store this run's edits now, so the status below is current
    is_primary = account.active_is_primary()
    blank = account.is_blank_new()
    name = account.active_resume_label() or "New résumé (not saved yet)"
    st.markdown(f"Editing **{name}**" + (" · ★ primary" if is_primary else ""))
    if blank and account.has_primary():
        st.caption("The builder starts empty — load your primary résumé to keep editing it.")

    # Only offer what applies right now, so there are never dead buttons:
    #  * Set as primary -- a saved résumé that isn't the primary one
    #  * Load primary   -- a primary exists and isn't already the one in the builder
    #  * New CV         -- there's something in the builder to start over from
    primary_ok = db.primary_supported()
    actions = []
    if primary_ok and not blank and not is_primary:
        actions.append("make")
    if primary_ok and account.has_primary() and not is_primary:
        actions.append("load")
    if not blank:
        actions.append("new")
    if not primary_ok:
        st.caption("Primary résumés need the latest database schema (re-run supabase/schema.sql).")
    if not actions:
        return
    for slot, action in zip(st.columns(len(actions)), actions):
        if action == "make" and slot.button(
                "Set as primary", key="dash_make_primary", width="stretch",
                help="Make this résumé the one you load first."):
            problem = account.make_active_primary()
            if problem:
                st.error(problem)
            else:
                st.rerun()
        elif action == "load" and slot.button(
                "Load primary", key="dash_load_primary", width="stretch",
                help="Replace what's in the builder with your primary résumé, ready to edit."):
            problem = account.load_primary()
            if problem:
                st.error(problem)
            else:
                st.rerun()
        elif action == "new" and slot.button(
                "New CV", key="dash_new_cv", width="stretch",
                help="Start a blank résumé. The one you're editing stays saved."):
            account.autosave()
            account.new_resume()
            st.rerun()


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

    # Primary-résumé controls live in this slot but are filled in AFTER the editors below have
    # run: the status ("★ primary", "not saved yet") then reflects the edit you just made.
    primary_slot = st.container()

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

with primary_slot:
    _primary_controls()

# --- Right: live preview ------------------------------------------------------
with preview_col:
    st.markdown('<p class="rb-eyebrow">Preview</p>', unsafe_allow_html=True)
    st.markdown(render_resume_html(resume), unsafe_allow_html=True)

# --- Footer: download ---------------------------------------------------------
render_download_footer(resume)
