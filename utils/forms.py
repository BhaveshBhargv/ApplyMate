"""Section editors for the Dashboard's left panel.

Each render_* function draws one resume section's editing UI (forms, per-entry
expanders, validation) into the current Streamlit container. They're grouped
here so the Dashboard page stays a thin layout shell. Multi-entry sections live
inside tabs (not a top-level expander), so their per-entry expanders don't
violate Streamlit's no-nested-expanders rule. AI tailoring lives on the ATS
Match page, not here.
"""
from __future__ import annotations

import streamlit as st

from utils import ai_assistant, resume_ai_parser
from utils.date_picker import is_start_after_end, month_year_input
from utils.resume_parser import extract_text, parse_resume
from utils.session_manager import (
    add_education,
    add_experience,
    add_project,
    add_skill_category,
    form_key,
    get_resume_data,
    remove_education,
    remove_experience,
    remove_project,
    remove_skill_category,
    set_resume_data,
)
from utils.validators import is_valid_email, is_valid_phone, is_valid_url, normalize_url


# --- Personal -----------------------------------------------------------------

def render_personal() -> None:
    resume = get_resume_data()
    info = resume.personal_info

    with st.form("f_personal"):
        c1, c2 = st.columns(2)
        with c1:
            full_name = st.text_input("Full name *", value=info.full_name, placeholder="First & Last Name")
            email = st.text_input("Email *", value=info.email, placeholder="jordan@example.com")
            phone = st.text_input("Phone * (with country code)", value=info.phone, placeholder="+1 555-123-4567")
        with c2:
            location = st.text_input("Location", value=info.location, placeholder="City, Country")
            linkedin = st.text_input("LinkedIn URL", value=info.linkedin_url, placeholder="linkedin.com/in/you")
            portfolio = st.text_input("Portfolio / GitHub URL", value=info.portfolio_url, placeholder="github.com/you")
        summary = st.text_area(
            "Professional summary", value=info.professional_summary,
            key=form_key("personal_summary"), height=110,
            placeholder="2-3 sentences. Tip: tailor it to a job on the ATS Match page.",
        )
        saved = st.form_submit_button("Save details", type="primary")

    if saved:
        errors = []
        if not full_name.strip():
            errors.append("Full name is required.")
        if not email.strip():
            errors.append("Email is required.")
        elif not is_valid_email(email):
            errors.append("Email is not valid.")
        if not phone.strip():
            errors.append("Phone is required.")
        elif not is_valid_phone(phone):
            errors.append("Phone must include a country code (e.g. +1 555-123-4567).")
        linkedin_n, portfolio_n = normalize_url(linkedin), normalize_url(portfolio)
        if linkedin.strip() and not is_valid_url(linkedin_n):
            errors.append("LinkedIn URL is not valid.")
        if portfolio.strip() and not is_valid_url(portfolio_n):
            errors.append("Portfolio/GitHub URL is not valid.")
        if errors:
            for e in errors:
                st.error(e)
        else:
            info.full_name, info.email, info.phone = full_name.strip(), email.strip(), phone.strip()
            info.location, info.linkedin_url, info.portfolio_url = location.strip(), linkedin_n, portfolio_n
            info.professional_summary = summary.strip()
            st.success("Saved.")


# --- Education ----------------------------------------------------------------

def render_education() -> None:
    resume = get_resume_data()
    if st.button("Add education", key="add_edu", width="stretch"):
        add_education()
        st.rerun()
    if not resume.education:
        st.caption("No education yet.")
    for entry in resume.education:
        with st.expander(f"{entry.institution or 'New entry'} — {entry.degree or 'Untitled'}",
                         expanded=not entry.institution):
            with st.form(f"edu_f_{entry.id}"):
                institution = st.text_input("Institution *", value=entry.institution, key=f"edu_i_{entry.id}")
                degree = st.text_input("Degree *", value=entry.degree, key=f"edu_d_{entry.id}")
                field = st.text_input("Field of study", value=entry.field_of_study, key=f"edu_fs_{entry.id}")
                gpa = st.text_input("GPA (optional)", value=entry.gpa, key=f"edu_g_{entry.id}",
                                    placeholder="e.g. 3.8, 8.5/10, 58.88%")
                current = st.checkbox("Currently studying", value=entry.is_current, key=f"edu_c_{entry.id}")
                st.caption("Start")
                start = month_year_input(entry.start_date, key_prefix=f"edu_s_{entry.id}")
                st.caption("End (ignored if currently studying)")
                end = month_year_input(entry.end_date, key_prefix=f"edu_e_{entry.id}")
                ach = st.text_area("Achievements (one per line)", value="\n".join(entry.achievements),
                                   key=f"edu_a_{entry.id}", height=70)
                saved = st.form_submit_button("Save", type="primary")
            if saved:
                errs = []
                if not institution or not degree:
                    errs.append("Institution and Degree are required.")
                if not current and is_start_after_end(start, end):
                    errs.append("Start date cannot be after end date.")
                if errs:
                    for e in errs:
                        st.error(e)
                else:
                    entry.institution, entry.degree, entry.field_of_study = institution.strip(), degree.strip(), field.strip()
                    entry.gpa = gpa.strip()
                    entry.is_current, entry.start_date = current, start
                    entry.end_date = "Present" if current else end
                    entry.achievements = [ln.strip() for ln in ach.split("\n") if ln.strip()]
                    st.success("Saved.")
            if st.button("Remove", key=f"edu_r_{entry.id}"):
                remove_education(entry.id)
                st.rerun()


# --- Experience ---------------------------------------------------------------

def render_experience() -> None:
    resume = get_resume_data()
    if st.button("Add experience", key="add_exp", width="stretch"):
        add_experience()
        st.rerun()
    if not resume.experience:
        st.caption("No experience yet.")
    for entry in resume.experience:
        with st.expander(f"{entry.job_title or 'New role'} at {entry.company or 'Company'}",
                         expanded=not entry.company):
            with st.form(f"exp_f_{entry.id}"):
                job_title = st.text_input("Job title *", value=entry.job_title, key=f"exp_t_{entry.id}")
                company = st.text_input("Company *", value=entry.company, key=f"exp_co_{entry.id}")
                location = st.text_input("Location", value=entry.location, key=f"exp_l_{entry.id}")
                current = st.checkbox("I currently work here", value=entry.is_current, key=f"exp_c_{entry.id}")
                st.caption("Start")
                start = month_year_input(entry.start_date, key_prefix=f"exp_s_{entry.id}")
                st.caption("End (ignored if current)")
                end = month_year_input(entry.end_date, key_prefix=f"exp_e_{entry.id}")
                bullets = st.text_area("Responsibilities & achievements (one per line) *",
                                       value="\n".join(entry.bullet_points),
                                       key=form_key(f"exp_b_{entry.id}"), height=120)
                saved = st.form_submit_button("Save", type="primary")
            if saved:
                errs = []
                if not job_title or not company:
                    errs.append("Job title and company are required.")
                if not current and is_start_after_end(start, end):
                    errs.append("Start date cannot be after end date.")
                if errs:
                    for e in errs:
                        st.error(e)
                else:
                    entry.job_title, entry.company, entry.location = job_title.strip(), company.strip(), location.strip()
                    entry.is_current, entry.start_date = current, start
                    entry.end_date = "Present" if current else end
                    entry.bullet_points = [ln.strip() for ln in bullets.split("\n") if ln.strip()]
                    st.success("Saved.")
            if st.button("Remove", key=f"exp_r_{entry.id}"):
                remove_experience(entry.id)
                st.rerun()


# --- Projects -----------------------------------------------------------------

def render_projects() -> None:
    resume = get_resume_data()
    if st.button("Add project", key="add_proj", width="stretch"):
        add_project()
        st.rerun()
    if not resume.projects:
        st.caption("No projects yet.")
    for entry in resume.projects:
        with st.expander(f"{entry.name or 'New project'}", expanded=not entry.name):
            with st.form(f"proj_f_{entry.id}"):
                name = st.text_input("Project name *", value=entry.name, key=f"proj_n_{entry.id}")
                url = st.text_input("URL (optional)", value=entry.url,
                                    placeholder="github.com/you/repo")
                desc = st.text_area("Short description", value=entry.description,
                                    key=form_key(f"proj_d_{entry.id}"), height=70)
                tech = st.text_input("Technologies (comma-separated)", value=", ".join(entry.technologies),
                                     key=f"proj_t_{entry.id}")
                bullets = st.text_area("Key contributions (one per line)", value="\n".join(entry.bullet_points),
                                       key=form_key(f"proj_b_{entry.id}"), height=90)
                saved = st.form_submit_button("Save", type="primary")
            if saved:
                url_n = normalize_url(url)
                if not name.strip():
                    st.error("Project name is required.")
                elif url.strip() and not is_valid_url(url_n):
                    st.error("Project URL is not valid.")
                else:
                    entry.name, entry.url, entry.description = name.strip(), url_n, desc.strip()
                    entry.technologies = [t.strip() for t in tech.split(",") if t.strip()]
                    entry.bullet_points = [ln.strip() for ln in bullets.split("\n") if ln.strip()]
                    st.success("Saved.")
            if st.button("Remove", key=f"proj_r_{entry.id}"):
                remove_project(entry.id)
                st.rerun()


# --- Skills -------------------------------------------------------------------

def render_skills() -> None:
    resume = get_resume_data()
    if st.button("Add category", key="add_skill", width="stretch"):
        add_skill_category()
        st.rerun()
    if not resume.skills:
        st.caption("No skills yet. Add a category like 'Programming Languages'.")
    for entry in resume.skills:
        with st.expander(f"{entry.category_name or 'New category'} ({len(entry.skills)})",
                         expanded=not entry.category_name):
            with st.form(f"sk_f_{entry.id}"):
                name = st.text_input("Category name *", value=entry.category_name, key=f"sk_n_{entry.id}",
                                     placeholder="e.g. Programming Languages")
                skills = st.text_input("Skills (comma-separated) *", value=", ".join(entry.skills),
                                       key=f"sk_s_{entry.id}", placeholder="Python, SQL, Git")
                saved = st.form_submit_button("Save", type="primary")
            if saved:
                if not name or not skills:
                    st.error("Category name and at least one skill are required.")
                else:
                    entry.category_name = name.strip()
                    entry.skills = [s.strip() for s in skills.split(",") if s.strip()]
                    st.success("Saved.")
            if st.button("Remove", key=f"sk_r_{entry.id}"):
                remove_skill_category(entry.id)
                st.rerun()


# --- Import (upload an existing resume) ---------------------------------------

def render_import() -> None:
    resume = get_resume_data()
    has_data = any(resume.completion_status().values()) or bool(resume.extra_sections)
    st.caption("Upload a .pdf, .docx, or .txt resume to pre-fill every section. "
               "Best-effort parsing — review each section afterward.")
    if not ai_assistant.is_configured():
        st.caption("Add an OpenRouter API key (see ATS Match for setup) to parse with AI -- it "
                   "reads any résumé layout, not just the common ones the basic parser expects.")
    uploaded = st.file_uploader("Choose a file", type=["pdf", "docx", "txt"], key="import_file")
    if uploaded is not None:
        if has_data:
            st.warning("This replaces everything currently entered.")
        if st.button("Parse & fill", type="primary", key="import_parse", width="stretch"):
            _run_import(uploaded)
    if resume.extra_sections:
        st.markdown("**Kept from your upload** (didn't match a standard section):")
        for extra in resume.extra_sections:
            with st.expander(f"{extra.heading}"):
                st.text(extra.content)


def _run_import(uploaded) -> None:
    """Parse the uploaded file with AI when available (it generalizes across
    résumé layouts), falling back to the rule-based parser otherwise or if the
    AI call fails for any reason -- import always succeeds with *something*."""
    file_bytes = uploaded.getvalue()
    parsed = None
    ai_error = None

    if ai_assistant.is_configured():
        try:
            raw_text = extract_text(file_bytes, uploaded.name)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Couldn't read that file: {exc}")
            return
        try:
            with st.spinner("Reading your résumé with AI..."):
                parsed = resume_ai_parser.parse_resume_with_ai(raw_text)
        except ai_assistant.AIError as exc:
            ai_error = str(exc)

    if parsed is None:
        try:
            parsed = parse_resume(file_bytes, uploaded.name)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Couldn't read that file: {exc}")
            return
        if ai_error:
            st.info(f"AI parsing wasn't available ({ai_error}), so a simpler rule-based "
                     "parser was used instead -- review the result carefully.")

    set_resume_data(parsed)
    st.success("Parsed. Check each tab and the live preview.")
    st.rerun()
