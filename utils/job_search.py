"""Job search across several free, official, legal-to-use sources.

Sources:
- **Adzuna** -- global aggregator (needs a free key). Searchable by keyword.
- **Remotive** -- remote-only board, public JSON API.
- **RemoteOK** -- remote-only board, public JSON API (attribution requested).
- **We Work Remotely** -- remote-only board, public RSS feed.
- **Company ATS boards** -- a curated list of well-known companies whose jobs
  are hosted on **Greenhouse** or **Lever** (each board is public). There is no
  free API to search *all* ATS boards, so we pull a fixed set and filter by the
  search title.

Remote-only boards are queried for "Any"/"Remote" work types. ATS boards carry
on-site/hybrid/remote roles, so they're queried for every work type. All network
calls run in parallel, share a small thread-safe TTL cache, have timeouts, and
fail soft: if one source errors the others still return. Nothing here applies to
a job or sends anything on the user's behalf -- it only reads public listings.
"""
from __future__ import annotations

import html
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, List, Optional, Tuple
from xml.etree import ElementTree as ET

import requests
import streamlit as st

_ADZUNA_BASE = "https://api.adzuna.com/v1/api/jobs"
_REMOTIVE_BASE = "https://remotive.com/api/remote-jobs"
_REMOTEOK_URL = "https://remoteok.com/api"
_WWR_URL = "https://weworkremotely.com/remote-jobs.rss"
_GREENHOUSE_BASE = "https://boards-api.greenhouse.io/v1/boards"
_LEVER_BASE = "https://api.lever.co/v0/postings"
_UA = {"User-Agent": "ApplyMate/1.0 (résumé builder + job search)"}
_TIMEOUT = 12          # seconds per request
_MAX_RESULTS = 50      # Adzuna's per-page maximum
_CACHE_TTL = 1800      # 30 min -- remote/ATS boards are fetched at most this often

# Curated company boards (verified live). Editable -- add/remove tokens freely.
_GREENHOUSE = [
    "stripe", "databricks", "gitlab", "coinbase", "figma", "dropbox", "instacart",
    "robinhood", "reddit", "discord", "cloudflare", "brex", "anthropic", "samsara",
    "asana", "affirm", "gusto", "mongodb", "elastic", "datadog", "airtable",
]
_LEVER = ["spotify", "binance"]
_LEVER_NAMES = {"spotify": "Spotify", "binance": "Binance"}

# Country code -> display name, for the picker. Adzuna scopes search by country.
COUNTRIES = {
    "gb": "United Kingdom", "us": "United States", "ca": "Canada", "au": "Australia",
    "de": "Germany", "fr": "France", "nl": "Netherlands", "in": "India",
    "sg": "Singapore", "nz": "New Zealand", "it": "Italy", "pl": "Poland",
    "at": "Austria", "za": "South Africa",
}

_CURRENCY = {
    "gb": "£", "us": "$", "ca": "C$", "au": "A$", "de": "€", "fr": "€",
    "nl": "€", "it": "€", "at": "€", "in": "₹", "sg": "S$", "nz": "NZ$",
    "pl": "zł", "za": "R",
}

WORK_TYPES = ["Any", "Remote", "Hybrid", "On-site"]

# Industry -> (Adzuna category tag, Remotive category slug). Other sources have
# no matching taxonomy and are filtered by title only.
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

    A positive-only signal: a short/absent description can only *hide* a match,
    never invent a false one.
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
    """Turn an HTML (or plain) description into clean, capped plain text."""
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()[:limit]


def posted_days(posted: str) -> Optional[int]:
    """Whole days since a 'YYYY-MM-DD' posting date, or None if unparseable."""
    if not posted:
        return None
    try:
        d = datetime.strptime(posted[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return max(0, (datetime.now(timezone.utc).date() - d).days)


@dataclass
class JobPosting:
    """One normalized job listing, source-agnostic."""

    title: str
    company: str
    location: str
    url: str
    source: str          # e.g. "Adzuna" | "Remotive" | "RemoteOK" | "Greenhouse"
    salary: str = ""
    job_type: str = ""
    posted: str = ""     # YYYY-MM-DD
    description: str = ""                       # cleaned plain text (for matching)
    matched_skills: List[str] = field(default_factory=list)
    match_score: Optional[int] = None           # 0-100 vs the résumé (set by the page)
    freshness_days: Optional[int] = None         # days since posted (set by the page)


@dataclass
class SearchResult:
    jobs: List[JobPosting] = field(default_factory=list)
    notices: List[str] = field(default_factory=list)
    adzuna_configured: bool = False


# --- Thread-safe TTL cache (no Streamlit calls -- safe inside worker threads) --

_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()


def _cached(key: str, fetch: Callable[[], list]) -> list:
    """Return cached value for `key` if fresh, else fetch, store, and return.

    Failures propagate (are NOT cached), so a transient outage retries next time.
    """
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
    value = fetch()
    with _CACHE_LOCK:
        _CACHE[key] = (now, value)
    return value


# --- Configuration -------------------------------------------------------------

def _get_secret(name: str) -> Optional[str]:
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
    """Shown when Adzuna keys are missing. The other sources work without them."""
    st.info(
        "**Add free Adzuna keys to widen the search** (on-site, hybrid and "
        "location-based roles). Remote and company-board results already work "
        "without them.\n\n"
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


# --- Relevance filter (shared) -------------------------------------------------

def _query_tokens(title: str) -> List[str]:
    return [w for w in title.lower().split() if len(w) > 2]


def _is_relevant(job_title: str, extra: str, tokens: List[str]) -> bool:
    """Keep a job only if a query word appears in its title (or `extra` text)."""
    if not tokens:
        return True
    haystack = f"{job_title} {extra}".lower()
    return any(tok in haystack for tok in tokens)


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
                  industry: str = "Any",
                  creds: Optional[Tuple[Optional[str], Optional[str]]] = None
                  ) -> Tuple[List[JobPosting], Optional[str]]:
    """Query Adzuna. `creds` may be pre-resolved so this is safe in a thread."""
    app_id, app_key = creds if creds is not None else _adzuna_creds()
    if not (app_id and app_key):
        return [], None

    what = title.strip()
    if work_type == "Remote":
        what = f"{what} remote".strip()
    elif work_type == "Hybrid":
        what = f"{what} hybrid".strip()

    params = {
        "app_id": app_id, "app_key": app_key,
        "results_per_page": _MAX_RESULTS, "what": what or "developer",
        "content-type": "application/json",
    }
    if location.strip():
        params["where"] = location.strip()
    category = _adzuna_category(industry)
    if category:
        params["category"] = category

    try:
        resp = requests.get(f"{_ADZUNA_BASE}/{country}/search/1", params=params, timeout=_TIMEOUT)
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

    jobs = [JobPosting(
        title=(item.get("title") or "").strip(),
        company=((item.get("company") or {}).get("display_name") or "").strip(),
        location=((item.get("location") or {}).get("display_name") or "").strip(),
        url=(item.get("redirect_url") or "").strip(),
        source="Adzuna",
        salary=_format_salary(country, item.get("salary_min"), item.get("salary_max")),
        job_type=(item.get("contract_time") or "").replace("_", " "),
        posted=(item.get("created") or "")[:10],
        description=strip_html(item.get("description")),
    ) for item in data.get("results", [])]
    return jobs, None


# --- Source: Remotive ----------------------------------------------------------

def search_remotive(title: str, industry: str = "Any") -> Tuple[List[JobPosting], Optional[str]]:
    """Query Remotive (remote-only, no auth)."""
    params = {"search": title.strip()}
    category = _remotive_category(industry)
    if category:
        params["category"] = category
    try:
        resp = requests.get(_REMOTIVE_BASE, params=params, headers=_UA, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        return [], f"Remotive request failed: {exc}"
    if resp.status_code != 200:
        return [], f"Remotive returned HTTP {resp.status_code}."
    try:
        data = resp.json()
    except ValueError:
        return [], "Remotive returned an unreadable response."

    tokens = _query_tokens(title)
    jobs = []
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


# --- Source: RemoteOK ----------------------------------------------------------

def _fetch_remoteok() -> List[JobPosting]:
    resp = requests.get(_REMOTEOK_URL, headers=_UA, timeout=_TIMEOUT)
    resp.raise_for_status()
    jobs = []
    for item in resp.json():
        position = (item.get("position") or "").strip()
        if not position:
            continue  # first element is legal/metadata
        lo, hi = item.get("salary_min"), item.get("salary_max")
        salary = f"${int(lo):,}–${int(hi):,}" if lo and hi else ""
        jobs.append(JobPosting(
            title=position,
            company=(item.get("company") or "").strip(),
            location=(item.get("location") or "").strip() or "Remote",
            url=(item.get("url") or item.get("apply_url") or "").strip(),
            source="RemoteOK",
            salary=salary,
            posted=(item.get("date") or "")[:10],
            description=strip_html(item.get("description")),
        ))
    return jobs


# --- Source: We Work Remotely (RSS) --------------------------------------------

def _fetch_wwr() -> List[JobPosting]:
    resp = requests.get(_WWR_URL, headers=_UA, timeout=_TIMEOUT)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    jobs = []
    for item in root.findall(".//item"):
        text = lambda tag: (item.findtext(tag) or "").strip()  # noqa: E731
        raw = text("title")
        company, sep, role = raw.partition(":")
        company, title = (company.strip(), role.strip()) if sep and role.strip() else ("", raw)
        posted = ""
        try:
            posted = parsedate_to_datetime(text("pubDate")).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            pass
        jobs.append(JobPosting(
            title=title, company=company, location="Remote",
            url=text("link"), source="We Work Remotely",
            posted=posted, description=strip_html(text("description")),
        ))
    return jobs


# --- Source: company ATS boards (Greenhouse / Lever) ---------------------------

def _fetch_greenhouse(token: str) -> List[JobPosting]:
    resp = requests.get(f"{_GREENHOUSE_BASE}/{token}/jobs", headers=_UA, timeout=_TIMEOUT)
    resp.raise_for_status()
    jobs = []
    for j in resp.json().get("jobs", []):
        jobs.append(JobPosting(
            title=(j.get("title") or "").strip(),
            company=(j.get("company_name") or token.title()).strip(),
            location=((j.get("location") or {}).get("name") or "").strip(),
            url=(j.get("absolute_url") or "").strip(),
            source="Greenhouse",
            posted=(j.get("updated_at") or j.get("first_published") or "")[:10],
            description="",  # fetched lazily is heavy; matched on title
        ))
    return jobs


def _fetch_lever(token: str) -> List[JobPosting]:
    resp = requests.get(f"{_LEVER_BASE}/{token}?mode=json", headers=_UA, timeout=_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        return []
    jobs = []
    for j in data:
        cats = j.get("categories") or {}
        posted = ""
        created = j.get("createdAt")
        if isinstance(created, (int, float)):
            posted = datetime.fromtimestamp(created / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        jobs.append(JobPosting(
            title=(j.get("text") or "").strip(),
            company=_LEVER_NAMES.get(token, token.title()),
            location=(cats.get("location") or "").strip(),
            url=(j.get("hostedUrl") or "").strip(),
            source="Lever",
            job_type=(cats.get("commitment") or "").strip(),
            posted=posted,
            description=strip_html(j.get("descriptionPlain") or j.get("description")),
        ))
    return jobs


# --- Orchestration -------------------------------------------------------------

def _norm(text: str) -> str:
    """Normalize a title/company for dedup: lowercase, drop parentheticals/punct."""
    text = re.sub(r"\(.*?\)", " ", text.lower())          # drop "(remote)", "(m/f/d)"
    text = re.sub(r"[^a-z0-9\s]", " ", text)               # drop punctuation
    return _WS_RE.sub(" ", text).strip()


def _dedupe(jobs: List[JobPosting]) -> List[JobPosting]:
    """Merge duplicates by normalized (title, company), keeping the richest copy.

    Two aggregators often list the same role; when they collide we keep the entry
    with the longer description (better for matching), then the fresher one.
    """
    best: dict = {}
    order: List[tuple] = []
    for job in jobs:
        if not job.title:
            continue
        key = (_norm(job.title), _norm(job.company))
        cur = best.get(key)
        if cur is None:
            best[key] = job
            order.append(key)
        else:
            better = (len(job.description), -(posted_days(job.posted) or 9999)) > \
                     (len(cur.description), -(posted_days(cur.posted) or 9999))
            if better:
                best[key] = job
    return [best[k] for k in order]


def search_jobs(title: str, location: str, country: str, work_type: str,
                industry: str = "Any") -> SearchResult:
    """Search every enabled source in parallel, merge, and dedupe.

    Remote-only boards (Remotive, RemoteOK, We Work Remotely) are queried for
    "Any"/"Remote"; ATS company boards are queried for every work type. Never
    raises -- per-source errors become user-facing notices (ATS boards fail
    silently, since they're a best-effort supplement).
    """
    result = SearchResult(adzuna_configured=adzuna_configured())
    tokens = _query_tokens(title)
    creds = _adzuna_creds()  # resolved on the main thread, then passed into workers
    remote_ok = work_type in ("Any", "Remote")

    def filt(jobs: List[JobPosting]) -> List[JobPosting]:
        # These boards aren't searched server-side, so filter precisely: require
        # every query word to appear in the title (a "data engineer" search must
        # not return every "engineer" role).
        if not tokens:
            return jobs
        return [j for j in jobs if all(tok in j.title.lower() for tok in tokens)]

    def task_adzuna():
        return search_adzuna(title, location, country, work_type, industry, creds=creds)

    def task_remotive():
        return search_remotive(title, industry)

    def task_remoteok():
        try:
            return filt(_cached("remoteok", _fetch_remoteok)), None
        except Exception:  # noqa: BLE001
            return [], "RemoteOK is temporarily unavailable."

    def task_wwr():
        try:
            return filt(_cached("wwr", _fetch_wwr)), None
        except Exception:  # noqa: BLE001
            return [], "We Work Remotely is temporarily unavailable."

    def make_ats(fetch, tok, key):
        def task():
            try:
                return filt(_cached(key, lambda: fetch(tok))), None
            except Exception:  # noqa: BLE001 -- one company board down shouldn't matter
                return [], None
        return task

    tasks: List[Callable[[], Tuple[List[JobPosting], Optional[str]]]] = [task_adzuna]
    if remote_ok:
        tasks += [task_remotive, task_remoteok, task_wwr]
    tasks += [make_ats(_fetch_greenhouse, t, f"gh:{t}") for t in _GREENHOUSE]
    tasks += [make_ats(_fetch_lever, t, f"lv:{t}") for t in _LEVER]

    collected: List[JobPosting] = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        for jobs, notice in pool.map(lambda fn: fn(), tasks):
            collected += jobs
            if notice and notice not in result.notices:
                result.notices.append(notice)

    result.jobs = _dedupe(collected)
    return result
