# APPLYMATE - Resume Building + Job Searching

A web app for building an ATS-friendly resume tailored to a specific job
description -- without inventing experience, skills, or qualifications the
user doesn't have -- and then searching live job openings that match it,
straight from the same résumé.

**🔗 Live demo:** https://applymate-bb.streamlit.app/

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
- [x] **Phase 4** -- AI (Tencent Hunyuan 3 via OpenRouter) lives on the **ATS
      Match page**: given your résumé and a job description, it generates
      **apply-able changes** (a tailored professional summary and rewritten
      experience bullets) that you review and apply one by one -- only ever
      rephrasing content you already entered, tuned to the job, never inventing
      anything.
- [x] **Export** -- Download the finished resume as an ATS-friendly Word
      (.docx) or PDF file (single column, standard headings, real bullets,
      no tables or graphics).
- [x] **Job search** -- Search live openings by job title, location, work
      type, industry, and country across **six free sources** (Adzuna, Remotive,
      RemoteOK, We Work Remotely, and curated Greenhouse/Lever company boards),
      fetched in parallel, deduped, and ranked by **match score** + **freshness**.
      Each listing can be sent to ATS Match in one click. Read-only: it only
      links to official postings.

## Tech Stack

Python, Streamlit, pypdf, python-docx, reportlab, scikit-learn,
requests (Adzuna + Remotive job APIs), and openai (used as an
OpenAI-compatible client for OpenRouter). Two deviations
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
│   ├── job_search.py       # Job search: Adzuna + Remotive + RemoteOK + WWR + Greenhouse/Lever boards (parallel, cached, deduped)
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

**Design (single committed look -- technical, Vercel-core).** A neutral **Zinc**
base (`#FAFAFA` surface, `#18181B` text -- never pure black) with one desaturated
**Emerald** accent (`#10B981`), deliberately avoiding the "AI indigo/purple"
cliché. Type is **Geist** (UI/display) + **Geist Mono** (numeric and label data --
job meta, ATS score, pagination, eyebrows), no serif on this software UI. Buttons
are near-black with white text; the emerald is used sparingly for kickers, links,
chips, and the score bar. Hairline dividers and generous space are preferred over
boxed cards; job/proposal cards use a white surface, a 1px border, a tinted
diffusion shadow, and a subtle hover lift. Motion is fluid CSS only (cards rise
in, tactile button press) and respects `prefers-reduced-motion`. **No emojis in
the UI** -- symbols are replaced with clean text/mono labels. The whole system is
in `utils/theme.py`. The résumé sheet itself stays a calm, conventional white document
(`utils/resume_html.py`) framed by this chrome -- its own format follows the
reference layout exactly -- centered name, pipe-separated contact line, blue
uppercase section headers with hairline rules, single column, right-aligned
dates -- and the .docx / .pdf exports mirror it so the download matches the
preview (the résumé stays white in both themes).

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
- **Navigation is a custom top bar, not a sidebar.** `app.py` registers the
  three pages with `st.navigation(..., position="hidden")` -- purely so
  `st.switch_page()` and URL routing work -- and each page then calls
  `utils.theme.render_header(active)` to draw the wordmark plus a row of
  **Dashboard / ATS Match / Jobs** buttons, highlighting the current one.
- **The Dashboard is two panels.** The left panel holds the section editors as
  tabs (**Personal, Education, Experience, Projects, Skills, Import**); the
  right panel is a live "paper" preview of the résumé that updates on save.
  The section editors live in `utils/forms.py` (not in `pages/`), so all three
  pages share the same widgets. Multi-entry sections (Education, Experience,
  Projects, Skills) let you add or remove entries freely. Each section's fields
  live inside an `st.form` with its own **Save** button -- nothing is written
  back to the data model until you click it, so typing never triggers a rerun
  mid-edit. The only feedback is the success/error alert right after that click.
- `utils/validators.py` checks that Email, Phone (must include a country
  code, e.g. `+1 555-123-4567`), and URL fields (LinkedIn, Portfolio, Project
  URL) are well-formed before they're saved; scheme-less URLs like
  `github.com/you` are auto-normalized to `https://github.com/you`.
- `utils/date_picker.py` renders Start/End Date as Month + Year dropdowns
  (resumes don't need a specific day) instead of free-text fields, storing
  them as a plain `"Aug 2019"` string. `is_start_after_end()` blocks saving
  an entry where the start date comes after the end date.
- GPA uses `st.number_input` so only numeric values can be entered at all.
- The Dashboard's right panel (`utils/resume_html.py`) is the live review --
  it renders exactly what the `.docx` / `.pdf` will export, so there's no
  separate Review page; you see the finished résumé update as you save.

### Phase 2 -- Upload an existing resume

- The **Import** tab in the Dashboard's left panel accepts a `.pdf`, `.docx`,
  or `.txt` file and calls `utils/resume_parser.py` to pre-fill every section,
  as an alternative to entering everything by hand.
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
  `set_resume_data()`, so it appears immediately across every section tab and
  in the live preview -- the user explicitly asked uploading to fill everything
  in, and the parser only ever transcribes what the file actually says, never
  invents content. What isn't guaranteed is *accuracy*: parsing can misread
  a line, so each section tab's own **Save** button is still there to let the
  user correct anything before treating it as final.
- Anything routed to an `ExtraSection` (unmatched heading) is carried into the
  résumé under its **original heading** (shown in the live preview and included
  in the export) -- so nothing from the uploaded file is silently lost, even if
  the app couldn't figure out where it belongs.

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

- **AI lives only on the ATS Match page**, not the Dashboard. Given your résumé
  and the job description, it generates **apply-able changes** you review and
  apply individually (`utils/ai_assistant.py`, OpenAI-compatible `openai` SDK
  pointed at OpenRouter serving Tencent's `tencent/hy3:free`):
  - **Tailored professional summary** -- a 2-3 sentence summary rebuilt from the
    experience, projects, and skills you entered, tuned to the job. **Apply**
    writes it into your résumé.
  - **Tailored experience bullets** (per role) -- your saved bullets rewritten
    into stronger, JD-relevant phrasing, preserving every fact. **Apply** per
    role.
- **Never fabricates.** A strict system prompt forbids inventing employers,
  dates, metrics, technologies, or skills; every function only rephrases
  content you already provided, tuned to the job description. Each change is a
  **proposal you explicitly Apply or Dismiss** -- nothing is written into your
  résumé silently.
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
  Hybrid / On-site), **industry** and country, and returns live openings as
  cards, each linking to the official posting. Title and location are prefilled
  from the résumé you're building, and the Dashboard's **"Search jobs for this
  résumé"** button jumps straight here and auto-searches for your most recent
  role. Industry maps to each source's own category taxonomy (`INDUSTRIES` in
  `utils/job_search.py`), so "Data & Analytics", "Design", "Finance & Legal",
  etc. narrow both Adzuna and Remotive where each has a matching category.
- Each card shows a **match score** (how well the job fits your résumé), a
  **freshness** label (how recently it was posted), the **source**, and chips for
  which of **your skills** the listing mentions. Results are **sorted by best
  match, then freshest**. For the full matched-vs-missing breakdown, **Match in
  ATS** loads that job's description into the ATS Match page in one click.
  - *Match score* is skill-overlap over the job's title + description, normalised
    so matching ~5 of your skills counts as a full match (users with long skill
    lists aren't penalised). It's positive-only -- an absent description can hide
    a match but never invent one -- so company-board jobs (no description) match
    on title and tend to score lower.
- **Six free, official, legal-to-use sources** back it (`utils/job_search.py`),
  fetched **in parallel** (thread-safe 30-min cache), merged and **deduplicated**
  (normalised title + company; the richer/fresher copy wins):
  - **Adzuna** -- global aggregator (needs free keys), searchable by keyword.
  - **Remotive**, **RemoteOK**, **We Work Remotely** -- remote-only boards with
    public no-auth APIs / RSS; queried for Any/Remote searches.
  - **Company ATS boards** -- a curated list of well-known companies whose jobs
    are hosted on **Greenhouse** or **Lever** (edit `_GREENHOUSE` / `_LEVER` in
    `job_search.py`). No free API searches *all* ATS boards, so a fixed set is
    pulled and filtered by title. Queried for every work type.
- There's no results-count control: each search fetches full batches from every
  source, scores + sorts them, keeps the strongest ~120, and paginates **10 per
  page** with Previous / Next and a "Page X / Y" indicator.
- The page **only reads listings and shows their links** -- it never submits an
  application or sends anything on the user's behalf. All calls have timeouts and
  fail soft: if a source errors the others still return (ATS boards fail silently
  as a best-effort supplement).

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

- The **download footer** (on every page, via `utils/theme.render_download_footer`)
  turns the current `ResumeData` into a **Word (.docx)** file
  (`utils/docx_export.py`, python-docx) or a **PDF** (`utils/pdf_export.py`,
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
- UI (`pages/`, plus shared widgets in `utils/forms.py` and `utils/theme.py`),
  data models (`models/`), and business logic (`utils/`) are kept in separate
  modules.
- Type hints are used throughout; important functions have docstrings.
