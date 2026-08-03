# APPLYMATE - Resume Building + Job Searching

A web app for building an ATS-friendly resume tailored to a specific job
description -- without inventing experience, skills, or qualifications the
user doesn't have.

**🔗 Live demo:** https://resume-builder-4kmyg83ru5rh6gygffosls.streamlit.app/

## Status: Complete (all phases + export)

- [x] **Phase 1** -- Personal details, Education, Experience, Projects, and
      Skills forms.
- [x] **Phase 2** -- Upload an existing resume (.pdf/.docx/.txt) to pre-fill
      every section, as an alternative to entering everything manually.
      Anything the parser can't confidently map to a known section is kept
      verbatim, tagged with its original heading from the resume.
- [x] **Phase 3** -- Paste/upload a job description, extract its keywords,
      compare them against the resume, and show an ATS match score plus the
      matched and missing keywords.
- [x] **Phase 4** -- AI (Tencent Hunyuan 3 via OpenRouter) drafts a
      professional summary, rewrites experience bullet points, enhances
      project descriptions, and gives whole-resume improvement suggestions --
      only ever rephrasing content you already entered, never inventing
      anything.
- [x] **Export** -- Download the finished resume as an ATS-friendly Word
      (.docx) or PDF file (single column, standard headings, real bullets,
      no tables or graphics).

## Tech Stack

Python, Streamlit, pypdf, python-docx, reportlab, pandas, scikit-learn,
openai (used as an OpenAI-compatible client for OpenRouter). Two deviations
from the originally-listed stack, each explained in its phase notes below:
**scikit-learn** replaces spaCy for Phase 3 (no runtime model download), and
Phase 4 uses **Tencent Hunyuan 3 (`tencent/hy3:free`) via OpenRouter** instead
of the OpenAI API directly, since Hunyuan 3 has a free tier so the deployed
app can run at no cost.

## Project Structure

The UI is a single two-panel **Dashboard** (edit on the left, live résumé
preview on the right, download in the footer), an **ATS Match** page, and a
**Jobs** page for searching live openings.

```
resume-builder/
├── app.py                  # Entry point: registers the 3 pages via st.navigation
├── requirements.txt
├── models/
│   └── resume_data.py      # Dataclasses: the single source of truth for resume content
├── utils/
│   ├── session_manager.py  # Bridges Streamlit session_state and the data models
│   ├── theme.py            # Design system: CSS/tokens + shared header & download footer
│   ├── forms.py            # Section editors (Personal/Education/.../Skills/Import) for the left panel
│   ├── resume_html.py      # Live "paper" preview -- renders the résumé in the reference format
│   ├── validators.py       # Email / phone / URL format validation
│   ├── date_picker.py      # Month + Year dropdown pair for resume dates
│   ├── resume_parser.py    # Best-effort .pdf/.docx/.txt resume text extraction + parsing
│   ├── ats_analyzer.py     # JD keyword extraction + resume match scoring
│   ├── ai_assistant.py     # OpenRouter/Hunyuan client + never-invent prompts (summary/bullets/suggestions)
│   ├── job_search.py       # Job search across Adzuna (keyed) + Remotive (no key), merged & deduped
│   ├── docx_export.py      # ATS-friendly Word (.docx) export (matches the preview format)
│   └── pdf_export.py       # ATS-friendly PDF export (matches the preview format)
├── pages/
│   ├── 1_🧭_Dashboard.py   # Edit (left, tabbed) + live preview (right) + download footer
│   ├── 2_🎯_ATS_Match.py   # JD match score + matched/missing keywords + AI suggestions
│   └── 3_💼_Jobs.py        # Search live openings by title/location/work type -> official apply links
├── assets/                 # Static assets (icons, sample data, etc.)
├── templates/              # Resume document templates (reserved for future custom exporters)
└── output/                 # Generated resume files (git-ignored)
```

**Design ("Workbench & Paper").** The chrome is a matte editing workbench; the
résumé is a crisp sheet of paper that updates live as you save. Palette and
type live in `utils/theme.py` (navy ink, professional blue `#2B5A9E`, Fraunces
display face, Calibri-style résumé body). The résumé format follows the
reference layout exactly -- centered name, pipe-separated contact line, blue
uppercase section headers with hairline rules, single column, right-aligned
dates -- and the .docx / .pdf exports mirror it so the download matches the
preview.

## How It Works

### Phase 1 -- Manual entry

- `models/resume_data.py` defines dataclasses (`PersonalInfo`,
  `EducationEntry`, `ExperienceEntry`, `ProjectEntry`, `SkillCategory`,
  `ExtraSection`, `ResumeData`) that represent everything the user enters.
  This is the only place resume data is *defined* -- every other module
  reads/writes these objects rather than raw dicts.
- `utils/session_manager.py` stores one `ResumeData` instance in Streamlit's
  `st.session_state` for the duration of the browser session, and exposes
  small functions (`add_education`, `remove_experience`, `set_resume_data`,
  etc.) so pages never touch `session_state` directly.
- **Navigation is a top bar, not a sidebar.** `app.py` registers every page
  with `st.navigation(..., position="hidden")` -- purely so
  `st.switch_page()` and URL routing work -- and each page then calls
  `utils.navigation.render_top_nav()` to draw its own horizontal row of page
  buttons, plus `render_prev_next()` at the bottom for Previous/Left and
  Next/Right buttons. `utils/navigation.py`'s `PAGES` list is the single
  source of truth for page order, titles, and icons. (Streamlit's built-in
  `position="top"` was tried first, but with 8 pages it collapses most of
  them into an "N more" dropdown instead of showing them all -- rendering
  the bar manually avoids that.)
- Each file in `pages/` is a self-contained form for one resume section.
  Multi-entry sections (Education, Experience, Projects, Skills) let you add
  or remove entries freely. Each entry's fields live inside an `st.form`
  with its own **Save Entry** button -- nothing is written back to the data
  model until you click it, so typing never triggers a page rerun mid-edit.
  The only feedback shown is the success/error alert immediately after that
  click; there's no separate summary text elsewhere on the page.
- `utils/validators.py` checks that Email, Phone (must include a country
  code, e.g. `+1 555-123-4567`), and URL fields (LinkedIn, Portfolio, Project
  URL) are well-formed before they're saved; scheme-less URLs like
  `github.com/you` are auto-normalized to `https://github.com/you`.
- `utils/date_picker.py` renders Start/End Date as Month + Year dropdowns
  (resumes don't need a specific day) instead of free-text fields, storing
  them as a plain `"Aug 2019"` string. `is_start_after_end()` blocks saving
  an entry where the start date comes after the end date.
- GPA uses `st.number_input` so only numeric values can be entered at all.
- `pages/8_📄_Review.py` gives a read-only summary of everything entered so
  far, and flags which sections are still incomplete.

### Phase 2 -- Upload an existing resume

- The Home page offers two starting points: **Upload a Resume** or **Enter
  Details Manually**. Both eventually land on the same forms -- uploading
  just pre-fills them.
- `pages/1_📤_Upload_Resume.py` accepts a `.pdf`, `.docx`, or `.txt` file and
  calls `utils/resume_parser.py`.
- `resume_parser.py` is deliberately a **heuristic, rule-based parser**
  (regex + section-heading detection), not AI -- resume formatting varies
  too much for anything to be trustworthy without review:
  - `extract_text()` pulls raw text out of the file (`pypdf` for PDF,
    `python-docx` for DOCX).
  - `split_into_sections()` scans for short, standalone, Title Case or ALL
    CAPS lines as section headings, and buckets the text under each one.
    Headings that match common aliases ("Work Experience", "Employment
    History", etc.) map to a canonical section (education / experience /
    projects / skills / summary); anything else -- "Certifications",
    "Awards", "Languages", whatever the resume actually calls it -- is kept
    as-is under its **original heading** rather than being dropped.
  - Each canonical section has its own best-effort field parser
    (`parse_education`, `parse_experience`, `parse_projects`,
    `parse_skills`) that splits the block into per-entry chunks and pulls
    out dates, degree/job-title lines, GPA, bullet points, etc.
  - Dates are only ever converted to the app's `"Mon YYYY"` format when the
    month is explicit in the source text (e.g. `"Aug 2019"`, `"08/2019"`) --
    a bare year like `"2019"` is left blank rather than guessing a month
    that was never stated, in keeping with the app's "never invent" rule.
- Parsed data is loaded straight into the session's resume data via
  `set_resume_data()`, so it appears immediately on every page including
  Review -- the user explicitly asked uploading to fill everything in, and
  the parser only ever transcribes what the file actually says, never
  invents content. What isn't guaranteed is *accuracy*: parsing can misread
  a line, so every page's own **Save Entry** button is still there to let
  the user correct anything before treating it as final.
- Anything routed to an `ExtraSection` (unmatched heading) is shown on the
  Upload page immediately after parsing, and again on the Review page under
  **"Additional Information (from uploaded resume)"**, tagged with its
  original heading -- so nothing from the uploaded file is silently lost,
  even if the app couldn't figure out where it belongs.

### Phase 3 -- Job description matching (ATS)

- The **ATS Match** page lets the user paste or upload a job description and
  reports how well the resume matches it, using `utils/ats_analyzer.py`.
- **Why scikit-learn instead of spaCy.** The original stack listed spaCy for
  this phase, but spaCy needs a ~12MB language model downloaded at runtime,
  which is fragile in a sandboxed/offline environment. Phase 3 uses
  scikit-learn's TF-IDF for the similarity score plus a rule-based keyword
  extractor -- close to how many real ATS tools actually work, and with no
  runtime model download. (spaCy can be swapped in later for smarter
  noun-phrase extraction if desired.)
- **Keyword extraction** (`extract_keywords`) draws from two sources:
  (1) a curated **gazetteer** of real skills, tools, cloud platforms, certs,
  and soft skills found in the JD, and (2) **acronyms / tech-punctuation
  tokens** (AI, AWS, C++, Data+). Named products in ordinary title case
  (Azure, CompTIA, Python) are covered by the gazetteer, so we deliberately
  don't try to guess title-case proper nouns -- that keeps ordinary words
  that merely open a bullet ("Developing", "Strong") out of the results.
- **Matching** (`analyze`) checks each JD keyword against the resume's full
  searchable text (`ResumeData.searchable_text()` -- skills *and* experience
  bullets, projects, education, and uploaded extra sections), then reports:
  a **keyword-coverage score**, a **TF-IDF cosine-similarity score**, and
  the **matched** (green) and **missing** (red) keyword lists.
- Matching is literal, mirroring how real ATS software screens resumes: if a
  job asks for "Azure" and the resume only says "cloud", Azure shows as
  missing. Missing keywords are presented as information framed **"add these
  only if you genuinely have the experience"** -- the app never rewrites the
  resume or invents a skill.

### Phase 4 -- AI writing assistant (Tencent Hunyuan 3 via OpenRouter)

- `utils/ai_assistant.py` calls an OpenAI-compatible chat API (the `openai`
  SDK pointed at OpenRouter, serving Tencent's `tencent/hy3:free`) and exposes
  four features, surfaced in-context on the pages they relate to:
  - **Professional summary** (Personal Details) -- drafts a 2-3 sentence
    summary from the experience, projects, and skills you entered.
  - **Bullet-point rewrite** (Experience, per role) -- tightens your saved
    bullets into stronger, action-verb-led phrasing.
  - **Project enhancement** (Projects, per project) -- polishes the
    description and bullet points.
  - **Resume suggestions** (Review) -- read-only, actionable advice on the
    whole resume.
- **Never fabricates.** A strict system prompt forbids inventing employers,
  dates, metrics, technologies, or skills; every function only rephrases
  content you already provided. All output is shown as a **proposal you
  explicitly accept or discard** -- nothing is written into your resume
  silently. If a job description was entered on the ATS Match page, the
  summary, bullets, and suggestions gently tailor emphasis toward it (still
  without adding anything you didn't state).
- **Why Hunyuan 3 via OpenRouter.** OpenRouter exposes an OpenAI-compatible
  endpoint, and Hunyuan 3 has a free tier (`tencent/hy3:free`), so the
  deployed app can run at no cost. Because it's OpenAI-compatible, `base URL`,
  `model`, and `key` are all configurable (`OPENROUTER_BASE_URL`,
  `OPENROUTER_MODEL`, `OPENROUTER_API_KEY`) -- point it at a different
  OpenAI-compatible provider or model with zero code changes. The whole
  integration is isolated in `ai_assistant.py`.
- Under the hood, accepting a proposal writes back into the form field via a
  small revision-nonce on the widget key (`session_manager.form_key` /
  `refresh_field`), because Streamlit otherwise ignores a keyed widget's
  `value=` once the user has touched it.

### AI setup (required for Phase 4 features)

The AI features are hidden until an OpenRouter API key is present; everything
else works without one.

1. Get a key at https://openrouter.ai/keys (the `tencent/hy3:free` model is free).
2. **Locally:** create `.streamlit/secrets.toml` (git-ignored) with:
   ```toml
   OPENROUTER_API_KEY = "your-key-here"
   # optional overrides:
   # OPENROUTER_MODEL = "tencent/hy3:free"
   # OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
   ```
   or set the `OPENROUTER_API_KEY` environment variable.
3. **On Streamlit Community Cloud:** add `OPENROUTER_API_KEY` under the app's
   **Settings → Secrets**.

Note: free-tier models are rate-limited (a capped number of requests per day);
switch `OPENROUTER_MODEL` to `tencent/hy3` (paid) if you hit the limit.

### Job search

- The **Jobs** page takes a job title, location, work type (Any / Remote /
  Hybrid / On-site) and country, and returns live openings as cards, each
  linking to the official posting. Title and location are prefilled from the
  résumé you're building, and the Dashboard's **"Search jobs for this résumé"**
  button jumps straight here and auto-searches for your most recent role.
- Each card shows which of **your skills** the listing mentions (green chips) --
  a positive-only signal, since a truncated description can only hide a match,
  never invent one. For the full matched-vs-missing breakdown, **🎯 Match in ATS**
  loads that job's description into the ATS Match page in one click.
- Two free, official, legal-to-use APIs back it (`utils/job_search.py`), merged
  and deduplicated:
  - **Adzuna** -- a global aggregator (needs free keys). Covers on-site, hybrid
    and remote roles across many countries, with an apply link per posting.
  - **Remotive** -- a remote-only board with a public, no-auth API. Queried
    only for remote-inclusive searches so the page still returns results with
    no keys configured.
- The page **only reads listings and shows their links** -- it never submits an
  application or sends anything on the user's behalf. All calls have timeouts
  and fail soft: if one source errors, the other still returns.

**Job search setup (optional -- widens results).** Remote results work with no
keys. To also get on-site / hybrid / location-based roles, add free Adzuna keys:

1. Register at https://developer.adzuna.com/ and create an app to get an
   **App ID** and **App Key**.
2. **Locally:** add to `.streamlit/secrets.toml`:
   ```toml
   ADZUNA_APP_ID = "your-app-id"
   ADZUNA_APP_KEY = "your-app-key"
   ```
3. **On Streamlit Cloud:** add both under **Settings → Secrets**.

### Export -- download the finished resume

- The **Download** page turns the current `ResumeData` into a **Word (.docx)**
  file (`utils/docx_export.py`, python-docx) or a **PDF** (`utils/pdf_export.py`,
  reportlab), delivered via `st.download_button`.
- Both use the same deliberately plain, **ATS-friendly** layout: one column,
  standard fonts, uppercase section headings with a thin rule, and real bullet
  lists -- no tables, text boxes, columns, or images, since those are what
  applicant-tracking systems fail to parse. Everything the app entered
  (including uploaded "extra sections") is included, and `is_current` roles
  render as `… – Present`.

## Setup

```bash
cd resume-builder
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
streamlit run app.py
```

The app opens at `http://localhost:8501`. All data lives in the browser
session only (nothing is persisted to disk yet) -- refreshing the page or
closing the tab clears it. See **AI setup** above to enable the Phase 4
features.

## Rules Followed by Design

- The app never invents work experience, skills, or qualifications -- both
  manual entry and resume parsing only ever surface what the user actually
  wrote, and parsed data must still be reviewed and explicitly saved before
  it's kept.
- UI (`pages/`), data models (`models/`), and business logic (`utils/`) are
  kept in separate modules.
- Type hints are used throughout; important functions have docstrings.
