"""App-wide visual design system: CSS injection + shared chrome (header, hero, footer).

Single, committed look -- no theme toggle. A neutral **Zinc** base with one
desaturated **Emerald** accent, near-black (not pure-black) buttons, Geist +
Geist Mono type (mono carries numeric/label data), hairline dividers over boxed
cards, and tactile motion. Technical, Vercel-core, non-templated -- and it
deliberately avoids the "AI indigo/purple" cliché. The résumé sheet itself
(resume_html.py) stays a clean white ATS document, framed by this chrome.
"""
from __future__ import annotations

import streamlit as st

# --- Design tokens (Zinc neutrals + one Emerald accent) -----------------------
BG = "#FAFAFA"            # zinc-50 -- app background
SURFACE = "#FFFFFF"       # cards + résumé sheet
TEXT = "#18181B"          # zinc-900 -- primary text (never pure black)
MUTED = "#71717A"         # zinc-500 -- secondary text
SUBTLE = "#A1A1AA"        # zinc-400
LINE = "#E4E4E7"          # zinc-200 -- hairlines
ACCENT = "#10B981"        # emerald-500 -- the single accent
ACCENT_INK = "#059669"    # emerald-600 -- accent hover
WASH = "#ECFDF5"          # emerald-50 -- chip fills
CHIP_BORDER = "#A7F3D0"   # emerald-200
BTN = "#18181B"           # near-black button fill
BTN_HOVER = "#27272A"     # zinc-800 -- button hover
GAP = "#E11D48"           # rose-600 -- ATS "missing" (semantic only)
IMPORT = "#000000"        # black -- import button fill

# Back-compat aliases.
INK = TEXT

_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700;800&family=Geist+Mono:wght@400;500;600&display=swap');
:root {{
  --bg: {BG}; --surface: {SURFACE}; --text: {TEXT}; --muted: {MUTED}; --subtle: {SUBTLE};
  --line: {LINE}; --accent: {ACCENT}; --accent-ink: {ACCENT_INK}; --wash: {WASH};
  --chip-border: {CHIP_BORDER}; --btn: {BTN}; --btn-hover: {BTN_HOVER}; --gap: {GAP}; --import: {IMPORT};
  --ui: "Geist", -apple-system, "Segoe UI", system-ui, sans-serif;
  --mono: "Geist Mono", ui-monospace, "SF Mono", Menlo, monospace;
  --display: "Geist", system-ui, sans-serif;
  --ease: cubic-bezier(0.16, 1, 0.3, 1);
}}

.stApp {{ background: var(--bg); font-family: var(--ui); color: var(--text); }}
[data-testid="stHeader"] {{ background: transparent; height: 0; }}
[data-testid="stToolbar"] {{ right: 0.5rem; }}
#MainMenu, footer, [data-testid="stDecoration"] {{ display: none; }}
[data-testid="stMainBlockContainer"] {{ max-width: 1240px; padding: 1.3rem 2.4rem 4rem; }}

/* Top bar */
.rb-header {{ display: flex; align-items: baseline; gap: 0.5rem; }}
.rb-word {{ font-family: var(--display) !important; font-weight: 800; font-size: 1.55rem !important;
  color: var(--text); letter-spacing: -0.04em; line-height: 1; }}
.rb-word em {{ font-style: normal; color: var(--accent); }}
.rb-tag {{ font-family: var(--mono); font-size: 0.64rem; font-weight: 500; letter-spacing: 0.14em;
  text-transform: uppercase; color: var(--muted); padding-left: 0.15rem; }}

/* Buttons */
.stButton > button, .stDownloadButton > button {{
  font-family: var(--ui); font-weight: 600; font-size: 0.88rem; border-radius: 9px; letter-spacing: -.005em;
  transition: background .2s var(--ease), color .2s var(--ease), border-color .2s var(--ease), transform .1s ease; }}
[data-testid="stBaseButton-secondary"] {{ background: transparent; border: 1px solid var(--line); color: var(--text); }}
[data-testid="stBaseButton-secondary"]:hover {{ border-color: var(--accent); color: var(--accent-ink); background: var(--surface); }}
[data-testid="stBaseButton-primary"] {{ background: var(--btn); border: 1px solid var(--btn); color: #fff; }}
[data-testid="stBaseButton-primary"]:hover {{ background: var(--btn-hover); border-color: var(--btn-hover); }}
[data-testid="stBaseButton-primary"]:disabled {{ background: var(--btn); border-color: var(--btn); color: #fff; opacity: 1; }}
/* Button labels live in a nested markdown container -- colour them explicitly so
   the label is visible (a dark label on a dark primary button would vanish). */
[data-testid="stBaseButton-primary"] p, [data-testid="stBaseButton-primary"] div {{ color: #fff !important; }}
[data-testid="stBaseButton-secondary"] p {{ color: var(--text); }}
[data-testid="stBaseButton-secondary"]:hover p {{ color: var(--accent-ink); }}
.stButton > button:active, .stDownloadButton > button:active {{ transform: translateY(1px); }}

/* The "Search jobs for this résumé" action -- highlighted in emerald so it pops. */
.st-key-dash_find_jobs [data-testid="stBaseButton-secondary"] {{
  background: var(--accent); border-color: var(--accent); color: #fff;
  box-shadow: 0 8px 20px -8px rgba(16,185,129,.55); }}
.st-key-dash_find_jobs [data-testid="stBaseButton-secondary"]:hover {{
  background: var(--accent-ink); border-color: var(--accent-ink); }}
.st-key-dash_find_jobs [data-testid="stBaseButton-secondary"] p {{ color: #fff !important; }}

/* Signature: an emerald tick anchoring a hairline */
.rb-rule {{ height: 1px; margin: 0.85rem 0 0.2rem;
  background: linear-gradient(90deg, var(--accent) 0%, var(--accent) 2.5%, var(--line) 2.5%, var(--line) 100%); }}

/* Hero: mono kicker over a controlled title, with an emerald accent bar */
.rb-hero {{ margin: 1.5rem 0 1.9rem; padding-left: 1rem; border-left: 3px solid var(--accent); }}
.rb-kicker {{ font-family: var(--mono); font-size: 0.68rem; font-weight: 500; letter-spacing: 0.16em;
  text-transform: uppercase; color: var(--accent-ink); margin: 0 0 0.45rem; }}
.rb-hero-title {{ font-family: var(--display) !important; font-weight: 700; font-size: 2.1rem !important;
  color: var(--text); letter-spacing: -0.04em; line-height: 1.04; margin: 0 0 0.45rem; }}
.rb-eyebrow {{ font-family: var(--mono); font-size: 0.66rem; font-weight: 500; letter-spacing: 0.16em;
  text-transform: uppercase; color: var(--muted); margin: 0 0 0.4rem; }}
.rb-panel-title {{ font-family: var(--display) !important; font-size: 1.35rem !important; font-weight: 700;
  color: var(--text); margin: 0 0 0.15rem; letter-spacing: -0.03em; line-height: 1.12; }}
.rb-sub {{ color: var(--muted); font-size: 0.9rem; line-height: 1.55; margin: 0 0 0.5rem; max-width: 62ch; }}
.rb-footer-label {{ font-family: var(--mono); font-size: 0.66rem; font-weight: 500; letter-spacing: 0.16em;
  text-transform: uppercase; color: var(--muted); margin-bottom: 0.4rem; }}
.rb-num {{ font-family: var(--mono); font-feature-settings: "tnum"; }}

/* Inputs */
.stTextInput input, .stTextArea textarea, .stNumberInput input {{
  background: var(--surface) !important; color: var(--text) !important; border-radius: 8px; }}
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="textarea"] {{
  background: var(--surface) !important; border-color: var(--line) !important; }}
input::placeholder, textarea::placeholder {{ color: var(--subtle); }}
.stTextInput input:focus, .stTextArea textarea:focus {{ border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }}
[data-baseweb="select"] > div {{ background: var(--surface) !important; border-color: var(--line) !important; color: var(--text) !important; }}
[data-baseweb="popover"] [role="option"]:hover {{ background: var(--wash) !important; }}

/* Tabs */
[data-baseweb="tab-list"] {{ border-bottom-color: var(--line); }}
[data-baseweb="tab"] {{ color: var(--muted); font-weight: 500; }}
[data-baseweb="tab"][aria-selected="true"] {{ color: var(--text); }}
[data-baseweb="tab-highlight"] {{ background-color: var(--accent) !important; }}


.st-key-dash_tabs [data-baseweb="tab-list"] button:nth-last-child(2) {{
  background: var(--import); border: 1px solid var(--chip-border); border-radius: 8px 8px 0 0;
  padding: 0 16px; margin-bottom: -1px; }}
.st-key-dash_tabs [data-baseweb="tab-list"] button:nth-last-child(2) p {{
  font-size: 0.98rem; font-weight: 700; color: var(--accent-ink); }}
.st-key-dash_tabs [data-baseweb="tab-list"] button:nth-last-child(2)[aria-selected="true"] {{
  background: var(--accent); border-color: var(--accent); }}
.st-key-dash_tabs [data-baseweb="tab-list"] button:nth-last-child(2)[aria-selected="true"] p {{ color: #fff; }}

/* Metrics + captions */
[data-testid="stMetricValue"] {{ color: var(--text); font-family: var(--mono); font-weight: 600; }}
[data-testid="stMetricLabel"], [data-testid="stMetricLabel"] * {{ color: var(--muted); }}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] * {{ color: var(--muted); }}

/* Cards: white surface, hairline border, tinted diffusion shadow, hover lift */
[data-testid="stVerticalBlockBorderWrapper"] {{ background: var(--surface); border-color: var(--line); border-radius: 16px;
  box-shadow: 0 18px 38px -24px rgba(24,24,27,.12); }}
[data-testid="stExpander"] {{ border: 1px solid var(--line); border-radius: 12px; background: var(--surface); }}
[data-testid="stExpander"] summary {{ font-weight: 600; color: var(--text); }}
[data-testid="stExpander"] summary:hover {{ color: var(--accent-ink); }}
hr {{ border-color: var(--line); background: var(--line); }}

/* File uploader */
[data-testid="stFileUploaderDropzone"] {{ background: var(--surface); border-color: var(--line); }}
[data-testid="stFileUploaderDropzone"], [data-testid="stFileUploaderDropzone"] * {{ color: var(--text); }}

/* Progress bar (ATS score) */
[data-testid="stProgress"] div[role="progressbar"] {{ background: var(--line); }}
[data-testid="stProgress"] div[role="progressbar"] > div {{ background: var(--accent); }}

/* Motion (fluid CSS): cards rise in; hover lifts */
@keyframes rb-rise {{ from {{ opacity: 0; transform: translateY(10px); }} to {{ opacity: 1; transform: none; }} }}
[data-testid="stVerticalBlockBorderWrapper"] {{ animation: rb-rise .32s var(--ease) both; }}
@media (hover: hover) and (pointer: fine) {{
  [data-testid="stVerticalBlockBorderWrapper"] {{ transition: transform .25s var(--ease), box-shadow .25s var(--ease), border-color .2s ease; }}
  [data-testid="stVerticalBlockBorderWrapper"]:hover {{ transform: translateY(-2px);
    box-shadow: 0 26px 46px -22px rgba(24,24,27,.16); border-color: var(--chip-border); }}
}}
@media (prefers-reduced-motion: reduce) {{
  [data-testid="stVerticalBlockBorderWrapper"] {{ animation: none; transition: none; }}
  .stButton > button:active, .stDownloadButton > button:active {{ transform: none; }}
}}
</style>
"""


def inject_theme() -> None:
    """Inject the app stylesheet. Call once at the top of every page."""
    st.markdown(_CSS, unsafe_allow_html=True)


def render_header(active: str) -> None:
    """Render the wordmark lockup + top nav (Dashboard / ATS Match / Jobs / Cover Letter)."""
    st.markdown(
        '<div class="rb-header"><span class="rb-word">Apply<em>Mate</em></span>'
        '<span class="rb-tag">Résumé + Jobs</span></div>',
        unsafe_allow_html=True,
    )
    spacer, b1, b2, b3, b4 = st.columns([3.1, 1.4, 1.4, 1.4, 1.75])
    with b1:
        if st.button("Dashboard", key="nav_dashboard", width="stretch",
                     type="primary" if active == "dashboard" else "secondary",
                     disabled=active == "dashboard"):
            st.switch_page("pages/1_🧭_Dashboard.py")
    with b2:
        if st.button("ATS Match", key="nav_ats", width="stretch",
                     type="primary" if active == "ats" else "secondary",
                     disabled=active == "ats"):
            st.switch_page("pages/2_🎯_ATS_Match.py")
    with b3:
        if st.button("Jobs", key="nav_jobs", width="stretch",
                     type="primary" if active == "jobs" else "secondary",
                     disabled=active == "jobs"):
            st.switch_page("pages/3_💼_Jobs.py")
    with b4:
        if st.button("Cover Letter", key="nav_cover", width="stretch",
                     type="primary" if active == "cover" else "secondary",
                     disabled=active == "cover"):
            st.switch_page("pages/4_✉️_Cover_Letter.py")
    st.markdown('<div class="rb-rule"></div>', unsafe_allow_html=True)


def render_hero(kicker: str, title: str, sub: str) -> None:
    """The page hero: a mono accent kicker over a controlled heavy title."""
    from html import escape
    st.markdown(
        f'<div class="rb-hero"><p class="rb-kicker">{escape(kicker)}</p>'
        f'<h1 class="rb-hero-title">{escape(title)}</h1>'
        f'<p class="rb-sub">{escape(sub)}</p></div>',
        unsafe_allow_html=True,
    )


def render_download_footer(resume) -> None:
    """Footer with Word/PDF download buttons. Shown on every page."""
    import re

    from utils import docx_export, pdf_export

    _docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    st.markdown('<div class="rb-rule"></div>', unsafe_allow_html=True)
    st.markdown('<p class="rb-footer-label">Download résumé</p>', unsafe_allow_html=True)
    if not (resume.personal_info.full_name or resume.searchable_text().strip()):
        st.caption("Add your details on the Dashboard, then download here.")
        return
    safe = re.sub(r"[^A-Za-z0-9]+", "_", resume.personal_info.full_name).strip("_") or "resume"
    d1, d2, _sp = st.columns([1, 1, 3])
    d1.download_button("Word (.docx)", data=docx_export.build_docx(resume),
                       file_name=f"{safe}_resume.docx", mime=_docx_mime, type="primary", width="stretch")
    d2.download_button("PDF", data=pdf_export.build_pdf(resume),
                       file_name=f"{safe}_resume.pdf", mime="application/pdf", width="stretch")
