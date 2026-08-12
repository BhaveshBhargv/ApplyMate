"""Cover Letter: a grounded, job-specific letter built from the résumé only.

The pipeline lives in utils/cover_letter.py -- retrieve the strongest
résumé/job matches, one LLM call over just those, then a deterministic verifier
that re-checks every skill and number in the finished letter against the
résumé. This page shows the result in an editable box (so the letter is always
reviewed before it leaves), names the matches it was built from, surfaces
anything the verifier couldn't back up, and exports to PDF.

Reached either from the nav or from a job card's "Cover letter" button on the
Jobs page, which arrives with the company, role, and full job description.
"""
from html import escape

import streamlit as st

from utils import ai_assistant as ai
from utils import cover_letter as cl
from utils import cover_letter_pdf
from utils.session_manager import (
    form_key,
    get_cover_letter_target,
    get_job_description,
    get_resume_data,
    init_session_state,
    refresh_field,
    set_cover_letter_target,
    set_job_description,
)
from utils.theme import inject_theme, render_download_footer, render_header, render_hero

inject_theme()
render_header("cover")
init_session_state()
resume = get_resume_data()

st.markdown(
    """
<style>
.rb-cl-meta { display:flex; gap:1.4rem; align-items:baseline; margin:.1rem 0 .6rem; }
.rb-cl-count { font-family:var(--mono); font-size:.78rem; color:var(--muted); font-feature-settings:"tnum"; }
.rb-cl-count b { color:var(--text); font-weight:600; }
.rb-cl-ok { font-family:var(--mono); font-size:.66rem; font-weight:500; letter-spacing:.1em;
  text-transform:uppercase; color:var(--accent-ink); background:var(--wash);
  border:1px solid var(--chip-border); border-radius:6px; padding:.18rem .5rem; }
.rb-cl-warn { font-family:var(--mono); font-size:.66rem; font-weight:500; letter-spacing:.1em;
  text-transform:uppercase; color:var(--gap); background:#FFF1F2; border:1px solid #FECDD3;
  border-radius:6px; padding:.18rem .5rem; }
.rb-cl-ev { border-top:1px solid var(--line); padding:.5rem 0 .1rem; }
.rb-cl-ev:first-child { border-top:0; }
.rb-cl-req { font-size:.85rem; color:var(--text); font-weight:500; }
.rb-cl-bullet { font-size:.79rem; color:var(--muted); margin:.2rem 0 0; padding-left:.8rem;
  border-left:2px solid var(--chip-border); line-height:1.45; }
</style>
""",
    unsafe_allow_html=True,
)

render_hero("Cover letter", "Write a letter for one job",
            "Built only from what's on your résumé, focused on the points that actually match "
            "this job — then checked line by line against your own words.")

if not resume.searchable_text().strip():
    st.info("Your résumé is empty. Fill it in on the Dashboard first, then come back.")
    render_download_footer(resume)
    st.stop()

target_company, target_role = get_cover_letter_target()
# Handoff from a job card: generate straight away instead of making the user
# re-submit a form that's already filled in.
auto = st.session_state.pop("cover_letter_auto", False)
if auto:
    # A letter for the previous job must never be shown under this job's heading.
    st.session_state.pop("cover_letter", None)
    st.session_state.pop("cover_letter_error", None)


def _generate(company: str, role: str, jd: str) -> None:
    """Run the pipeline and store the result (or the error) for rendering."""
    try:
        letter = cl.generate(resume, jd, company=company, role=role)
        st.session_state["cover_letter"] = letter
        st.session_state.pop("cover_letter_error", None)
        # The letter goes into a widget the user may have already edited, so the
        # revision nonce has to be bumped for `value=` to be re-read.
        refresh_field("cl_body")
    except ai.AIError as exc:
        st.session_state["cover_letter_error"] = str(exc)


# --- Target job ----------------------------------------------------------------
with st.form("cover_letter_form"):
    c1, c2 = st.columns(2)
    with c1:
        company = st.text_input("Company", value=target_company,
                                placeholder="e.g. Stripe",
                                help="Left blank, the letter stays generic — it never guesses a company.")
    with c2:
        role = st.text_input("Role", value=target_role, placeholder="e.g. Data Engineer")
    jd_text = st.text_area("Job description", value=get_job_description(), height=200,
                           placeholder="Paste the full job description here...")
    generate_clicked = st.form_submit_button("Generate cover letter", type="primary")

if generate_clicked:
    set_job_description(jd_text)
    set_cover_letter_target(company, role)
    if not jd_text.strip():
        st.warning("Paste the job description first — the letter is written against that specific job.")
    elif not ai.is_configured():
        pass  # the notice below explains what's missing
    else:
        with st.spinner("Matching your résumé to the job, writing, and verifying..."):
            _generate(company, role, jd_text)
elif auto and ai.is_configured() and get_job_description().strip():
    with st.spinner("Matching your résumé to the job, writing, and verifying..."):
        _generate(target_company, target_role, get_job_description())

if not ai.is_configured():
    ai.render_unavailable_notice()

err = st.session_state.get("cover_letter_error")
if err:
    st.error(f"Couldn't write the letter: {err}")

# --- Result --------------------------------------------------------------------
letter: "cl.CoverLetter | None" = st.session_state.get("cover_letter")
if letter is not None:
    st.divider()

    band = f"{cl.MIN_WORDS}–{cl.MAX_WORDS}"
    status = ('<span class="rb-cl-ok">Verified against your résumé</span>' if letter.verified
              else f'<span class="rb-cl-warn">{len(letter.flags)} unverified claim'
                   f'{"s" if len(letter.flags) != 1 else ""}</span>')
    st.markdown(
        f'<div class="rb-cl-meta"><span class="rb-cl-count"><b>{letter.word_count}</b> words '
        f'· target {band}</span>{status}</div>',
        unsafe_allow_html=True,
    )
    if not letter.in_range:
        st.caption(f"The model landed outside the {band}-word target. Regenerate, or trim it "
                   "yourself in the box below.")
    if not letter.semantic:
        st.caption("Matches were found by keyword. Add a free `EMBEDDINGS_API_KEY` (Gemini) for "
                   "synonym-aware matching, which usually finds stronger evidence.")

    if letter.flags:
        rows = "\n".join(
            f"- **{escape(f.text)}** — {escape(f.note)}" for f in letter.flags
        )
        st.warning("These appear in the letter but the verifier couldn't find them in your "
                   f"résumé. Check each one and remove anything you can't stand behind:\n\n{rows}")

    edited = st.text_area(
        "Cover letter", value=letter.text, height=460, key=form_key("cl_body"),
        help="Edit freely — the PDF exports exactly what's in this box.",
    )

    d1, d2, _sp = st.columns([1.2, 1.2, 2.6])
    d1.download_button(
        "Download PDF",
        data=cover_letter_pdf.build_cover_letter_pdf(
            resume, edited, company=letter.company, role=letter.role,
        ),
        file_name=f"{cover_letter_pdf.file_stem(resume, letter.company)}.pdf",
        mime="application/pdf", type="primary", width="stretch",
        disabled=not edited.strip(),
    )
    if d2.button("Start over", key="cl_reset", width="stretch"):
        st.session_state.pop("cover_letter", None)
        st.session_state.pop("cover_letter_error", None)
        refresh_field("cl_body")
        st.rerun()

    live_words = len(edited.split())
    if live_words != letter.word_count:
        st.caption(f"Edited length: **{live_words}** words.")

    # What the letter was allowed to talk about -- the same evidence the model saw.
    with st.expander(f"What this letter was built from ({len(letter.highlights)} matches)"):
        if letter.highlights:
            rows = ""
            for h in letter.highlights:
                rows += (f'<div class="rb-cl-ev"><div class="rb-cl-req">{escape(h.requirement)}</div>'
                         f'<p class="rb-cl-bullet">{escape(h.bullet)}</p></div>')
            st.markdown(rows, unsafe_allow_html=True)
        else:
            st.caption("No requirement-level match cleared the evidence threshold, so the letter "
                       "was written from your résumé facts alone.")
        if letter.safe_skills:
            st.caption("**Skills it could mention** (evidenced by your résumé): "
                       + ", ".join(letter.safe_skills))
        if letter.withheld_skills:
            st.caption("**Skills it was told to withhold** (the job asks, your résumé doesn't show): "
                       + ", ".join(letter.withheld_skills))
        if letter.revised:
            st.caption("The first draft failed verification, so it was rewritten once and rechecked.")
elif ai.is_configured() and not err:
    st.caption("Add the company and role, paste the job description, and hit "
               "**Generate cover letter** — or start from a job card on the **Jobs** page.")

render_download_footer(resume)
