"""App-wide visual design system: CSS injection + shared chrome (header, footer).

Design concept -- "Workbench & Paper": the app is a matte editing workbench and
the resume is a crisp sheet of paper that updates live. The chrome stays quiet
so the document is the loud thing. Palette and type are defined once here as CSS
custom properties and reused everywhere.
"""
from __future__ import annotations

import streamlit as st

# --- Design tokens (kept in sync with the resume sheet in resume_html.py) ------
INK = "#16233E"          # deep navy -- primary text, wordmark
ACCENT = "#2B5A9E"       # professional blue -- section rules + primary actions
ACCENT_INK = "#1E3F70"   # darker blue -- hover
WORKBENCH = "#E7EBF1"    # app background
PAPER = "#FFFFFF"        # cards + resume sheet
LINE = "#DDE2EA"         # hairlines
MUTED = "#5B6472"        # secondary text
MATCH = "#1F9D6B"        # ATS matched
GAP = "#E5533D"          # ATS missing


_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,600;1,9..144,500&display=swap');
:root {{
  --ink: {INK};
  --accent: {ACCENT};
  --accent-ink: {ACCENT_INK};
  --workbench: {WORKBENCH};
  --paper: {PAPER};
  --line: {LINE};
  --muted: {MUTED};
  --match: {MATCH};
  --gap: {GAP};
  --ui: "Inter", -apple-system, "Segoe UI", system-ui, sans-serif;
  --display: "Fraunces", Georgia, "Times New Roman", serif;
}}

/* Workbench surface (base font inherits; we do NOT blanket-override every
   element, which would out-specify the signature .rb-* classes below) */
.stApp {{ background: var(--workbench); font-family: var(--ui); }}

/* Quiet the default Streamlit chrome so our header leads */
[data-testid="stHeader"] {{ background: transparent; height: 0; }}
[data-testid="stToolbar"] {{ right: 0.5rem; }}
#MainMenu, footer, [data-testid="stDecoration"] {{ display: none; }}

/* Roomier main column */
[data-testid="stMainBlockContainer"] {{
  max-width: 1300px;
  padding: 1.1rem 2rem 3rem;
}}

/* --- Custom top bar --------------------------------------------------------- */
.rb-header {{
  display: flex; align-items: baseline; gap: 0.6rem;
  padding-bottom: 0.2rem; margin-bottom: 0.1rem;
}}
.rb-word {{
  font-family: var(--display) !important; font-weight: 600; font-size: 1.7rem !important;
  color: var(--ink); letter-spacing: -0.01em; line-height: 1;
}}
.rb-word em {{ font-style: italic; color: var(--accent); font-weight: 500; }}
.rb-tag {{
  font-family: var(--ui); font-size: 0.72rem; font-weight: 600;
  letter-spacing: 0.16em; text-transform: uppercase; color: var(--muted);
  padding-left: 0.2rem;
}}

/* Nav buttons (secondary = inactive, primary = active) */
.stButton > button, .stDownloadButton > button {{
  font-family: var(--ui); font-weight: 600; border-radius: 9px;
  transition: all .15s ease; letter-spacing: .01em;
}}
[data-testid="stBaseButton-secondary"] {{
  background: transparent; border: 1px solid var(--line); color: var(--ink);
}}
[data-testid="stBaseButton-secondary"]:hover {{
  border-color: var(--accent); color: var(--accent); background: #fff;
}}
[data-testid="stBaseButton-primary"] {{
  background: var(--accent); border: 1px solid var(--accent); color: #fff;
}}
[data-testid="stBaseButton-primary"]:hover {{ background: var(--accent-ink); border-color: var(--accent-ink); }}
/* Active nav item is a disabled primary button -- keep it fully blue, not greyed */
[data-testid="stBaseButton-primary"]:disabled {{
  background: var(--accent); border-color: var(--accent); color: #fff; opacity: 1;
}}

/* Cards / accordion */
[data-testid="stExpander"] {{
  border: 1px solid var(--line); border-radius: 12px; background: var(--paper);
  box-shadow: 0 1px 2px rgba(22,35,62,.04);
}}
[data-testid="stExpander"] summary {{ font-weight: 600; color: var(--ink); }}
[data-testid="stExpander"] summary:hover {{ color: var(--accent); }}

/* Inputs */
[data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea,
[data-testid="stNumberInputContainer"] input {{
  border-radius: 8px;
}}

/* Section eyebrow used above panels */
.rb-eyebrow {{
  font-family: var(--ui); font-size: 0.72rem; font-weight: 700;
  letter-spacing: 0.16em; text-transform: uppercase; color: var(--accent);
  margin: 0 0 0.15rem;
}}
.rb-panel-title {{
  font-family: var(--display) !important; font-size: 1.5rem !important; color: var(--ink);
  margin: 0 0 0.1rem; letter-spacing: -0.01em; line-height: 1.15;
}}
.rb-sub {{ color: var(--muted); font-size: 0.9rem; margin: 0 0 0.4rem; }}

/* The paper sheet wraps the resume preview */
.rb-paper-wrap {{
  position: sticky; top: 0.5rem;
}}

/* Footer bar */
.rb-footer-label {{
  font-family: var(--ui); font-size: 0.72rem; font-weight: 700;
  letter-spacing: 0.16em; text-transform: uppercase; color: var(--muted);
  margin-bottom: 0.3rem;
}}
</style>
"""


def inject_theme() -> None:
    """Inject the app stylesheet. Call once at the top of every page."""
    st.markdown(_CSS, unsafe_allow_html=True)


def render_header(active: str) -> None:
    """Render the wordmark + top navigation (Dashboard / ATS Match).

    `active` is the current page key so the matching nav button is highlighted.
    Uses st.switch_page for routing; app.py registers the pages with
    st.navigation(position="hidden").
    """
    st.markdown(
        '<div class="rb-header"><span class="rb-word">R<em>é</em>sumé</span>'
        '<span class="rb-tag">Studio</span></div>',
        unsafe_allow_html=True,
    )
    spacer, b1, b2, b3 = st.columns([5, 1.4, 1.4, 1.4])
    with b1:
        if st.button(
            "Dashboard", key="nav_dashboard", width="stretch",
            type="primary" if active == "dashboard" else "secondary",
            disabled=active == "dashboard",
        ):
            st.switch_page("pages/1_🧭_Dashboard.py")
    with b2:
        if st.button(
            "ATS Match", key="nav_ats", width="stretch",
            type="primary" if active == "ats" else "secondary",
            disabled=active == "ats",
        ):
            st.switch_page("pages/2_🎯_ATS_Match.py")
    with b3:
        if st.button(
            "Jobs", key="nav_jobs", width="stretch",
            type="primary" if active == "jobs" else "secondary",
            disabled=active == "jobs",
        ):
            st.switch_page("pages/3_💼_Jobs.py")
    st.divider()


def render_download_footer(resume) -> None:
    """Sticky-feeling footer with Word/PDF download buttons. Shown on every page."""
    import re

    from utils import docx_export, pdf_export

    _docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    st.divider()
    st.markdown('<p class="rb-footer-label">Download résumé</p>', unsafe_allow_html=True)
    if not (resume.personal_info.full_name or resume.searchable_text().strip()):
        st.caption("Add your details on the Dashboard, then download here.")
        return
    safe = re.sub(r"[^A-Za-z0-9]+", "_", resume.personal_info.full_name).strip("_") or "resume"
    d1, d2, _sp = st.columns([1, 1, 3])
    d1.download_button("⬇  Word (.docx)", data=docx_export.build_docx(resume),
                       file_name=f"{safe}_resume.docx", mime=_docx_mime, type="primary", width="stretch")
    d2.download_button("⬇  PDF", data=pdf_export.build_pdf(resume),
                       file_name=f"{safe}_resume.pdf", mime="application/pdf", width="stretch")
