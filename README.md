# APPLYMATE - Resume Building + Job Searching

A web app for building an ATS-friendly resume tailored to a specific job
description -- without inventing experience, skills, or qualifications the
user doesn't have -- and then searching live job openings that match it,
straight from the same résumé.

**🔗 Live demo:** https://applymate-bb.streamlit.app/

## Tech Stack

Python, Streamlit, pypdf, python-docx, reportlab, requests (Adzuna + Remotive
job APIs), and openai (used as an OpenAI-compatible client for **both** the
OpenRouter chat model **and** a free embeddings endpoint -- Google Gemini's
free tier by default, or any OpenAI-compatible provider). The app is
deliberately light -- **no torch, no local ML model** -- so it deploys cleanly
on the Streamlit Community Cloud free tier. ATS matching is done with
**sentence embeddings** (`text-embedding-3-small`) rather than TF-IDF, and the
cosine/vector math is pure Python, so no numpy/scipy/scikit-learn is needed.
The chat model is **Tencent Hunyuan 3 (`tencent/hy3:free`) via OpenRouter**,
whose free tier lets the deployed app run at no cost.

## Project Structure

The UI is a single two-panel **Dashboard** (edit on the left, live résumé
preview on the right, download in the footer), an **ATS Match** page, a
**Jobs** page for searching live openings, and a **Cover Letter** page that
writes a grounded letter for one of those jobs.

```
resume-builder/
├── app.py                  # Entry point: registers the 4 pages via st.navigation
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
│   ├── embeddings.py       # Cached embeddings client (Gemini free tier by default) + cosine (ATS + RAG)
│   ├── ats_analyzer.py     # Semantic JD/résumé match -> MatchReport (skills/experience/education)
│   ├── agents.py           # Agent workflow: Retriever (RAG) -> Evidence -> Writer for tailoring
│   ├── ai_assistant.py     # OpenRouter/Hunyuan client + never-invent prompts (the Writer's LLM call)
│   ├── job_search.py       # Job search: Adzuna + Remotive + RemoteOK + WWR + Greenhouse/Lever boards (parallel, cached, deduped)
│   ├── cover_letter.py     # Cover letter: evidence retrieval -> one LLM call -> claim verifier
│   ├── cover_letter_pdf.py # One-page business-letter PDF (header matches the résumé export)
│   ├── docx_export.py      # ATS-friendly Word (.docx) export (matches the preview format)
│   └── pdf_export.py       # ATS-friendly PDF export (matches the preview format)
├── pages/
│   ├── 1_🧭_Dashboard.py   # Edit (left, tabbed) + live preview (right) + download footer
│   ├── 2_🎯_ATS_Match.py   # Semantic match breakdown + evidence-based AI tailoring cards
│   ├── 3_💼_Jobs.py        # Search live openings by title/location/work type -> official apply links
│   └── 4_✉️_Cover_Letter.py # Grounded cover letter for one job + verifier flags + PDF download
├── assets/                 # Static assets (icons, sample data, etc.)
└── templates/              # Resume document templates (reserved for future custom exporters)
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

### Manual entry

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

### Upload an existing resume

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

### Job description matching (ATS, semantic)

- The **ATS Match** page lets the user paste or upload a job description and
  reports how well the résumé matches it, using `utils/ats_analyzer.py` +
  `utils/embeddings.py`.
- **Sentence embeddings, not keywords.** Matching compares *meaning* using a
  sentence-embedding model (Google Gemini's free `text-embedding-004` by
  default), so "React" matches "ReactJS", "ML" matches "machine learning", and
  "built pipelines" matches "data engineering" -- things literal keyword matching
  misses. `embeddings.py` is a thin, cached, batched, **provider-neutral**
  client; cosine similarity is pure Python (no numpy/scikit-learn).
- **A real breakdown, not one number** (`analyze` -> `MatchReport`):
  - **Overall match %** -- a calibrated blend of the three sections below.
  - **Skills matched / missing** -- JD skill candidates are pulled with the
    curated **gazetteer** + acronym detection (`extract_keywords`), but the
    match *decision* is semantic: a JD skill counts as covered when its closest
    résumé skill clears a similarity threshold. Matched skills show which résumé
    skill covered them; missing skills are shown as honest gaps.
  - **Experience match** -- each JD responsibility is matched to the résumé
    experience bullet that best supports it, with that bullet surfaced as
    evidence.
  - **Education match** -- the JD's stated education requirement (if any) vs. the
    résumé's education; "n/a" when the JD asks for none, so nothing is penalised
    unfairly.
- Missing skills are framed **"add these only if you genuinely have them"** --
  the app never rewrites the résumé or invents a skill.
- **Graceful fallback.** With no `EMBEDDINGS_API_KEY`, `analyze` falls back to
  the previous lexical (exact/token-overlap) matching, flagged in the UI, so the
  page keeps working -- it just isn't synonym-aware until a (free) key is added.

### Evidence-based AI tailoring (agent workflow + RAG)

- **AI lives only on the ATS Match page**, not the Dashboard. Given your résumé
  and the job description, it generates **evidence-based, apply-able
  suggestions** you review individually. Instead of one big prompt dumping the
  whole résumé and JD into the model, the work is split into a small **agent
  workflow** (`utils/agents.py`), three parts of which are deterministic (no
  LLM) and only the last of which calls the language model:
  1. **Scorer** (`ats_analyzer.analyze`) -- the semantic breakdown from Phase 3.
  2. **Retriever** (RAG) -- embeds every résumé bullet once and, for each JD
     requirement, retrieves only the single most relevant bullet (with
     provenance). **Only those retrieved bullets reach the LLM** -- never the
     whole résumé.
  3. **Evidence** -- binds each retrieved bullet to the JD requirement that
     surfaced it and to a **confidence** derived from the retrieval similarity.
     The "why" behind every suggestion is grounded, not model-invented.
  4. **Writer** (`ai_assistant.tailor_bullets`, the one LLM call) -- rewrites
     each retrieved bullet to emphasise its requirement.
- **Every suggestion is a card:** *which JD requirement caused it* -> *which
  résumé bullet is being modified* (original vs. rewrite) -> *confidence*.
  **Apply** writes the rewrite back to that exact bullet; **Dismiss** drops it.
- **Never fabricates.** A strict system prompt forbids inventing employers,
  dates, metrics, technologies, or skills; the Writer only rephrases bullets you
  already wrote. Bullets whose evidence is too weak are skipped entirely (that's
  exactly where fabrication would otherwise creep in), and nothing is written
  into your résumé silently.
- **Why Hunyuan 3 via OpenRouter.** OpenRouter exposes an OpenAI-compatible
  endpoint, and Hunyuan 3 has a free tier (`tencent/hy3:free`), so the
  deployed app can run at no cost. Because it's OpenAI-compatible, `base URL`,
  `model`, and `key` are all configurable (`OPENROUTER_BASE_URL`,
  `OPENROUTER_MODEL`, `OPENROUTER_API_KEY`) -- point it at a different
  OpenAI-compatible provider or model with zero code changes.
- Under the hood, accepting a suggestion writes back into the form field via a
  small revision-nonce on the widget key (`session_manager.form_key` /
  `refresh_field`), because Streamlit otherwise ignores a keyed widget's
  `value=` once the user has touched it. Stale suggestions (résumé edited after
  generation) are guarded -- a rewrite that can't locate its target bullet is a
  no-op, never a corruption.

### AI setup (two keys, both free, both optional)

Everything else works without keys. Two independent features light up when their
key is present -- **both have a genuinely free tier, no billing card needed:**

- **`OPENROUTER_API_KEY`** -- the chat model behind the AI tailoring suggestions
  (Phase 4). Free key at https://openrouter.ai/keys (`tencent/hy3:free` is free).
- **`EMBEDDINGS_API_KEY`** -- embeddings behind the *semantic* ATS breakdown and
  the RAG retriever (Phase 3). Default is **Google Gemini's free tier** -- free
  key at https://aistudio.google.com/apikey. Without it, ATS matching falls back
  to keyword/lexical matching (flagged in the UI). Any OpenAI-compatible
  embeddings provider works via the `EMBEDDINGS_BASE_URL` / `EMBEDDINGS_MODEL`
  overrides.

1. **Locally:** create `.streamlit/secrets.toml` (git-ignored) with whichever you
   have -- see `.streamlit/secrets.toml.example` for the full template:
   ```toml
   OPENROUTER_API_KEY = "your-openrouter-key"
   EMBEDDINGS_API_KEY = "your-gemini-key"
   # optional overrides:
   # OPENROUTER_MODEL = "tencent/hy3:free"
   # OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
   # EMBEDDINGS_MODEL = "text-embedding-004"
   # EMBEDDINGS_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
   ```
   or set the same names as environment variables.
2. **On Streamlit Community Cloud:** add the keys under the app's
   **Settings → Secrets**.

Note: free-tier chat models are rate-limited (a capped number of requests per
day); switch `OPENROUTER_MODEL` to `tencent/hy3` (paid) if you hit the limit.

### Job search

- The **Jobs** page takes a job title, location, work type (Any / Remote /
  Hybrid / On-site), **industry** and country, and returns live openings as
  cards, each linking to the official posting. Title and location are prefilled
  from the résumé you're building, and the Dashboard's **"Search jobs for this
  résumé"** button jumps straight here and auto-searches for your most recent
  role. Industry maps to each source's own category taxonomy (`INDUSTRIES` in
  `utils/job_search.py`), so "Data & Analytics", "Design", "Finance & Legal",
  etc. narrow both Adzuna and Remotive where each has a matching category.
- Each card shows, for that job's own named skills, which ones **you already
  have** (green chips) and which are **missing** (red chips), plus a **freshness**
  label and the **source**. Results are **sorted by most skills covered, then
  freshest**. Skill detection is positive-only -- a short or absent description
  can hide a match but never invent one -- so the chips only ever under-claim.
- **Match in ATS** loads that job's **full description** into the ATS Match page
  in one click for the semantic breakdown. Because Adzuna returns only a
  truncated snippet and company boards return none, the handoff fetches the full
  text from the public posting page when what it holds looks partial
  (`job_search.fetch_full_description`); the ATS page shows it in an editable box,
  so you can always paste or correct the JD.
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

### Cover letter (grounded, verified, per job)

- The **Cover Letter** page writes a letter for **one specific job**, built only
  from the résumé. Every job card on the **Jobs** page has a **Cover letter**
  button: it fetches that posting's full description, carries the company and
  role across, and generates immediately -- or open the page from the nav and
  paste a job description yourself.
- Same shape as the tailoring agent -- deterministic work around a single LLM
  call (`utils/cover_letter.py`):
  1. **Retriever** -- `ats_analyzer.analyze()` supplies the strongest
     (job requirement → supporting résumé bullet) pairs, the job's skills the
     résumé genuinely evidences, and the ones it doesn't. The top few pairs are
     the letter's *only* subject matter.
  2. **Writer** -- `ai_assistant.write_cover_letter()`, one call, seeing only
     that evidence, the résumé's own facts, and an explicit **do-not-claim list**
     built from the skills the résumé lacks. It is told never to claim a skill
     just because the job names it.
  3. **Verifier** -- the finished letter is re-read deterministically: every
     tool, platform, certification, and number in it is checked against the
     résumé text. Anything unsupported, or a word count outside the 250–350
     target, sends the draft back **once** with those complaints; the better of
     the two drafts wins.
- **Nothing is hidden.** The page shows the word count, the matches the letter
  was built from, the skills it was told to withhold, and any claim the verifier
  still couldn't find in your résumé -- flagged for you to remove, never quietly
  kept. The letter sits in an editable box, so it's always reviewed before it
  leaves.
- Output is the letter only (salutation → body → sign-off); preamble,
  letterheads, and trailing model notes are stripped rather than trusted.
- **Download PDF** (`utils/cover_letter_pdf.py`, reportlab) renders exactly
  what's in the box as a one-page business letter -- the same header as the
  résumé export, then the date, a `Re:` line, and your text.
- Uses the same `OPENROUTER_API_KEY` as the tailoring suggestions; the
  `EMBEDDINGS_API_KEY` is optional and just makes the retrieved matches stronger.

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
