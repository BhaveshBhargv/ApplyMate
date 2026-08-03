"""Job search across two free, official, legal-to-use APIs.

Sources:
- **Adzuna** -- a global job-search aggregator. Needs a free developer key
  (`app_id` + `app_key`). Covers on-site / hybrid / remote roles across many
  countries and returns a real apply link (`redirect_url`) per posting.
- **Remotive** -- a remote-only job board with a public, no-auth JSON API.
  Used to strengthen "Remote" searches (it is queried only when the user is
  after remote work).

Nothing here submits an application or sends anything on the user's behalf --
it only reads public listings and hands back their official links. All calls
have timeouts and fail soft: if one source errors, the other still returns.

Keys are read from Streamlit secrets or the environment, never hard-coded.
"""
from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import requests
import streamlit as st

_ADZUNA_BASE = "https://api.adzuna.com/v1/api/jobs"
_REMOTIVE_BASE = "https://remotive.com/api/remote-jobs"
_TIMEOUT = 12  # seconds per request

# Country code -> display name, for the picker. Adzuna scopes search by country.
COUNTRIES = {
    "gb": "United Kingdom",
    "us": "United States",
    "ca": "Canada",
    "au": "Australia",
    "de": "Germany",
    "fr": "France",
    "nl": "Netherlands",
    "in": "India",
    "sg": "Singapore",
    "nz": "New Zealand",
    "it": "Italy",
    "pl": "Poland",
    "at": "Austria",
    "za": "South Africa",
}

_CURRENCY = {
    "gb": "£", "us": "$", "ca": "C$", "au": "A$", "de": "€", "fr": "€",
    "nl": "€", "it": "€", "at": "€", "in": "₹", "sg": "S$", "nz": "NZ$",
    "pl": "zł", "za": "R",
}

WORK_TYPES = ["Any", "Remote", "Hybrid", "On-site"]

# Industry -> (Adzuna category tag, Remotive category slug). Each source has its
# own taxonomy, so an industry maps to whichever category each one recognises;
# None means that source has no matching category and is left unfiltered.
INDUSTRIES = {
    "Any": (None, None),
    "Software Development": ("it-jobs", "software-dev"),
    "Data & Analytics": ("it-jobs", "data"),
    "Engineering": ("engineering-jobs", None),
    "Design": ("creative-design-jobs", "design"),
    "Product": (None, "product"),
    "DevOps / Sysadmin": ("it-jobs", "devops"),
    "Marketing": ("pr-advertising-marketing-jobs", "marketing"),
    "Sales": ("sales-jobs", "sales"),
    "Finance & Legal": ("accounting-finance-jobs", "finance-legal"),
    "Customer Support": ("customer-services-jobs", "customer-support"),
    "Human Resources": ("hr-jobs", "hr"),
    "QA": ("scientific-qa-jobs", "qa"),
    "Healthcare": ("healthcare-nursing-jobs", None),
    "Teaching": ("teaching-jobs", None),
}


def _adzuna_category(industry: str) -> Optional[str]:
    return INDUSTRIES.get(industry, (None, None))[0]


def _remotive_category(industry: str) -> Optional[str]:
    return INDUSTRIES.get(industry, (None, None))[1]


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def skills_in_text(skills: List[str], text: str) -> List[str]:
    """Return the résumé skills that appear (whole-word) in a listing's text.

    This is a deliberately positive-only signal: a truncated job description can
    only *hide* a match, never invent a false one, so surfacing overlapping
    skills is honest where a coverage percentage over a short snippet is not.
    """
    if not text:
        return []
    haystack = text.lower()
    hits: List[str] = []
    seen = set()
    for skill in skills:
        term = skill.strip()
        key = term.lower()
        if not term or key in seen:
            continue
        pattern = r"(?<![\w+#])" + re.escape(key) + r"(?![\w+#])"
        if re.search(pattern, haystack):
            hits.append(term)
            seen.add(key)
    return hits


def strip_html(text: str, limit: int = 6000) -> str:
    """Turn an HTML (or plain) description into clean, capped plain text.

    Remotive returns full HTML descriptions and Adzuna returns short snippets;
    both are normalized here so the ATS analyzer sees plain text.
    """
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()[:limit]


@dataclass
class JobPosting:
    """One normalized job listing, source-agnostic."""

    title: str
    company: str
    location: str
    url: str
    source: str          # "Adzuna" | "Remotive"
    salary: str = ""
    job_type: str = ""   # e.g. full_time, contract
    posted: str = ""     # YYYY-MM-DD
    description: str = ""                       # cleaned plain text (for ATS matching)
    matched_skills: List[str] = field(default_factory=list)  # résumé skills found in this listing


@dataclass
class SearchResult:
    jobs: List[JobPosting] = field(default_factory=list)
    notices: List[str] = field(default_factory=list)   # non-fatal messages to show the user
    adzuna_configured: bool = False


# --- Configuration -------------------------------------------------------------

def _get_secret(name: str) -> Optional[str]:
    """Read a value from st.secrets (Streamlit Cloud) then the environment."""
    try:
        value = st.secrets.get(name)  # type: ignore[attr-defined]
        if value:
            return str(value)
    except Exception:
        pass
    return os.environ.get(name)


def _adzuna_creds() -> Tuple[Optional[str], Optional[str]]:
    return _get_secret("ADZUNA_APP_ID"), _get_secret("ADZUNA_APP_KEY")


def adzuna_configured() -> bool:
    app_id, app_key = _adzuna_creds()
    return bool(app_id and app_key)


def render_setup_notice() -> None:
    """Shown when Adzuna keys are missing. Remotive still works without them."""
    st.info(
        "**Add free Adzuna keys to widen the search** (on-site, hybrid and "
        "location-based roles). Remote results below already work without them.\n\n"
        "1. Register at https://developer.adzuna.com/ and create an app to get an "
        "**App ID** and **App Key** (free).\n"
        "2. **Locally:** add them to `.streamlit/secrets.toml`:\n"
        "   ```toml\n"
        "   ADZUNA_APP_ID = \"your-app-id\"\n"
        "   ADZUNA_APP_KEY = \"your-app-key\"\n"
        "   ```\n"
        "3. **On Streamlit Cloud:** add both under the app's **Settings → Secrets**.\n\n"
        "Then reload this page."
    )


# --- Source: Adzuna ------------------------------------------------------------

def _format_salary(country: str, lo, hi) -> str:
    cur = _CURRENCY.get(country, "")
    try:
        lo = float(lo) if lo else 0.0
        hi = float(hi) if hi else 0.0
    except (TypeError, ValueError):
        return ""
    if lo and hi and abs(lo - hi) > 1:
        return f"{cur}{lo:,.0f}–{cur}{hi:,.0f}"
    val = lo or hi
    return f"{cur}{val:,.0f}" if val else ""


def search_adzuna(title: str, location: str, country: str, work_type: str,
                  limit: int, industry: str = "Any") -> Tuple[List[JobPosting], Optional[str]]:
    """Query Adzuna. Returns (jobs, error_message-or-None)."""
    app_id, app_key = _adzuna_creds()
    if not (app_id and app_key):
        return [], None  # not configured -- caller handles the setup notice

    what = title.strip()
    # Adzuna has no explicit remote flag, so fold the intent into keywords.
    if work_type == "Remote":
        what = f"{what} remote".strip()
    elif work_type == "Hybrid":
        what = f"{what} hybrid".strip()

    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": max(1, min(limit, 50)),
        "what": what or "developer",
        "content-type": "application/json",
    }
    if location.strip():
        params["where"] = location.strip()
    category = _adzuna_category(industry)
    if category:
        params["category"] = category

    url = f"{_ADZUNA_BASE}/{country}/search/1"
    try:
        resp = requests.get(url, params=params, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        return [], f"Adzuna request failed: {exc}"

    if resp.status_code == 401:
        return [], "Adzuna rejected the keys (401). Check ADZUNA_APP_ID / ADZUNA_APP_KEY."
    if resp.status_code != 200:
        return [], f"Adzuna returned HTTP {resp.status_code}."

    try:
        data = resp.json()
    except ValueError:
        return [], "Adzuna returned an unreadable response."

    jobs: List[JobPosting] = []
    for item in data.get("results", []):
        jobs.append(JobPosting(
            title=(item.get("title") or "").strip(),
            company=((item.get("company") or {}).get("display_name") or "").strip(),
            location=((item.get("location") or {}).get("display_name") or "").strip(),
            url=(item.get("redirect_url") or "").strip(),
            source="Adzuna",
            salary=_format_salary(country, item.get("salary_min"), item.get("salary_max")),
            job_type=(item.get("contract_time") or "").replace("_", " "),
            posted=(item.get("created") or "")[:10],
            description=strip_html(item.get("description")),
        ))
    return jobs, None


# --- Source: Remotive ----------------------------------------------------------

def _query_tokens(title: str) -> List[str]:
    """Significant words from the search title, for relevance filtering."""
    return [w for w in title.lower().split() if len(w) > 2]


def _is_relevant(job_title: str, category: str, tokens: List[str]) -> bool:
    """Keep a Remotive job only if the title/category actually matches the query.

    Remotive's `search` param is loose and returns many off-topic filler roles
    (a "data engineer" search can surface graphic designers), so we require at
    least one query word to appear in the job's title or category.
    """
    if not tokens:
        return True
    haystack = f"{job_title} {category}".lower()
    return any(tok in haystack for tok in tokens)


def search_remotive(title: str, limit: int, industry: str = "Any") -> Tuple[List[JobPosting], Optional[str]]:
    """Query Remotive (remote-only, no auth). Returns (jobs, error-or-None)."""
    params = {"search": title.strip(), "limit": max(1, min(limit, 50))}
    category = _remotive_category(industry)
    if category:
        params["category"] = category
    try:
        resp = requests.get(_REMOTIVE_BASE, params=params, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        return [], f"Remotive request failed: {exc}"
    if resp.status_code != 200:
        return [], f"Remotive returned HTTP {resp.status_code}."
    try:
        data = resp.json()
    except ValueError:
        return [], "Remotive returned an unreadable response."

    tokens = _query_tokens(title)
    jobs: List[JobPosting] = []
    for item in data.get("jobs", []):
        job_title = (item.get("title") or "").strip()
        if not _is_relevant(job_title, item.get("category") or "", tokens):
            continue
        jobs.append(JobPosting(
            title=job_title,
            company=(item.get("company_name") or "").strip(),
            location=(item.get("candidate_required_location") or "Remote").strip(),
            url=(item.get("url") or "").strip(),
            source="Remotive",
            salary=(item.get("salary") or "").strip(),
            job_type=(item.get("job_type") or "").replace("_", " "),
            posted=(item.get("publication_date") or "")[:10],
            description=strip_html(item.get("description")),
        ))
    return jobs, None


# --- Orchestration -------------------------------------------------------------

def _dedupe(jobs: List[JobPosting]) -> List[JobPosting]:
    """Drop repeats by (title, company), preserving first-seen order."""
    seen = set()
    out: List[JobPosting] = []
    for job in jobs:
        key = (job.title.lower().strip(), job.company.lower().strip())
        if key in seen or not job.title:
            continue
        seen.add(key)
        out.append(job)
    return out


def search_jobs(title: str, location: str, country: str, work_type: str,
                limit: int = 15, industry: str = "Any") -> SearchResult:
    """Search enabled sources, merge, dedupe. Never raises -- errors become notices.

    Remotive (remote-only) is queried when the user wants remote work
    ("Any" or "Remote"); it's skipped for Hybrid / On-site, where a
    remote-only board isn't relevant.
    """
    result = SearchResult(adzuna_configured=adzuna_configured())
    collected: List[JobPosting] = []

    adzuna_jobs, adzuna_err = search_adzuna(title, location, country, work_type, limit, industry)
    collected += adzuna_jobs
    if adzuna_err:
        result.notices.append(adzuna_err)

    if work_type in ("Any", "Remote"):
        remotive_jobs, remotive_err = search_remotive(title, limit, industry)
        collected += remotive_jobs
        if remotive_err:
            result.notices.append(remotive_err)

    result.jobs = _dedupe(collected)[:limit * 2]
    return result
