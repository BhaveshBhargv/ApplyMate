"""Daily picks: each day, ~10 UK jobs at visa-sponsoring employers that fit the user's job titles.

What this does NOT do: it never submits an application. For each pick the user opens a
"ready-to-apply pack" (utils.apply_pack) and presses Submit on the employer's own page -- employers'
application systems don't accept submissions from third parties, and bots breach LinkedIn/Indeed terms.

Selection rules (chosen by the user):
  * only employers on the UK Home Office register of licensed Skilled Worker sponsors;
  * only UK locations;
  * ranked by fit to the saved job titles, then how many of the job's named skills the résumé
    already covers, then freshness;
  * at most 2 jobs per employer, so one big company can't fill the list;
  * never a job already shown in the last 14 days (so the list is fresh each day), and never
    one the user marked applied or skipped.

State is one small dict per user (encrypted in the database by utils.db):
    {"titles": [...], "location": "United Kingdom", "day": "2026-10-16",
     "jobs": [job dicts], "done": {url: "applied" | "skipped"}, "seen": {url: "2026-10-16"}}
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

from models.resume_data import ResumeData
from utils import ats_analyzer, job_search, sponsors
from utils.job_search import JobPosting

MAX_TITLES = 5
DAILY_COUNT = 10
MAX_PER_COMPANY = 2     # a big employer with 40 similar openings mustn't fill the whole list
SEEN_DAYS = 14
DEFAULT_LOCATION = "United Kingdom"
_JOB_FIELDS = {f.name for f in fields(JobPosting)}


def today() -> str:
    """Today's date (UTC) as YYYY-MM-DD -- the key for 'have I built today's list yet?'."""
    return datetime.now(timezone.utc).date().isoformat()


def parse_titles(text: str) -> List[str]:
    """Job titles typed as lines or comma/semicolon-separated; de-duplicated, capped."""
    seen, out = set(), []
    for raw in re.split(r"[\n,;]+", text or ""):
        title = re.sub(r"\s+", " ", raw).strip(" -•*\t")[:60]
        if title and title.lower() not in seen:
            seen.add(title.lower())
            out.append(title)
    return out[:MAX_TITLES]


def new_state() -> dict:
    return {"titles": [], "location": DEFAULT_LOCATION, "day": "", "jobs": [], "done": {}, "seen": {}}


def normalise_state(state: Optional[dict]) -> dict:
    base = new_state()
    base.update(state or {})
    return base


def job_to_dict(job: JobPosting) -> dict:
    data = asdict(job)
    data["description"] = data["description"][:1500]   # keep the stored list small; fetched in full on demand
    return data


def job_from_dict(data: dict) -> JobPosting:
    return JobPosting(**{k: v for k, v in data.items() if k in _JOB_FIELDS})


def needs_refresh(state: dict, day: Optional[str] = None) -> bool:
    return bool(state["titles"]) and state.get("day") != (day or today())


def _title_fit(job_title: str, titles: List[str]) -> float:
    """How closely the listing's title matches the closest searched title (0-1, by word stems)."""
    job_stems = ats_analyzer.content_stems(job_title)
    best = 0.0
    for title in titles:
        want = ats_analyzer.content_stems(title)
        if want:
            best = max(best, len(want & job_stems) / len(want))
    return best


@dataclass
class BuildResult:
    jobs: List[JobPosting] = field(default_factory=list)
    notices: List[str] = field(default_factory=list)
    considered: int = 0           # distinct listings found across all titles
    uk: int = 0                   # ...of which in the UK
    register_ok: bool = True


def build_picks(titles: List[str], location: str, resume: Optional[ResumeData],
                seen: Dict[str, str], *, count: int = DAILY_COUNT) -> BuildResult:
    """Search every title and pick the best `count` UK listings at licensed sponsors."""
    result = BuildResult()
    country = job_search.infer_country(location or DEFAULT_LOCATION)
    pool: Dict[str, JobPosting] = {}
    for title in titles:
        found = job_search.search_jobs(title, location or DEFAULT_LOCATION, country, "Any")
        for note in found.notices:
            if note not in result.notices:
                result.notices.append(note)
        for job in found.jobs:
            key = job.url or f"{job.title}|{job.company}"
            pool.setdefault(key, job)
    result.considered = len(pool)

    sponsors.annotate(pool.values())
    result.register_ok = sponsors.register_available()
    if not result.register_ok:
        result.notices.append("The UK sponsor register couldn't be loaded, so no picks can be made "
                              "right now. Try again in a few minutes.")
        return result

    resume_text = resume.searchable_text() if resume else ""
    candidates = []
    for key, job in pool.items():
        if job.url in seen or key in seen:
            continue
        if not sponsors.is_uk_location(job.location, trusted_uk_source=(job.source == "Adzuna" and country == "gb")):
            continue
        result.uk += 1
        if job.sponsor_licensed is not True or job.sponsorship_text == "no":
            continue
        jd_skills = ats_analyzer.extract_keywords(f"{job.title} {job.description}", max_keywords=12)
        job.matched_skills = job_search.skills_in_text(jd_skills, resume_text) if resume_text else []
        covered = {s.lower() for s in job.matched_skills}
        job.missing_skills = [s for s in jd_skills if s.lower() not in covered]
        job.freshness_days = job_search.posted_days(job.posted)
        fresh = 0.0 if job.freshness_days is None else max(0.0, 1.0 - min(job.freshness_days, 30) / 30)
        score = 3 * _title_fit(job.title, titles) + 0.5 * len(job.matched_skills) + fresh
        job.match_score = int(round(score * 10))
        candidates.append((score, job))
    candidates.sort(key=lambda t: t[0], reverse=True)
    per_company: Dict[str, int] = {}
    for _, job in candidates:
        company = sponsors.normalize_company(job.company)
        if per_company.get(company, 0) >= MAX_PER_COMPANY:
            continue
        per_company[company] = per_company.get(company, 0) + 1
        result.jobs.append(job)
        if len(result.jobs) >= count:
            break
    return result


def apply_build(state: dict, built: BuildResult, day: Optional[str] = None) -> dict:
    """Store a finished build as today's list and remember what was shown (pruning old entries)."""
    day = day or today()
    state["day"] = day
    state["jobs"] = [job_to_dict(j) for j in built.jobs]
    state["done"] = {}
    cutoff = (date.fromisoformat(day) - timedelta(days=SEEN_DAYS)).isoformat()
    seen = {u: d for u, d in state.get("seen", {}).items() if d >= cutoff}
    for job in built.jobs:
        seen[job.url or f"{job.title}|{job.company}"] = day
    state["seen"] = dict(list(seen.items())[-400:])
    return state


def pending_jobs(state: dict) -> List[JobPosting]:
    """Today's picks that haven't been marked applied or skipped."""
    done = state.get("done", {})
    return [job_from_dict(d) for d in state.get("jobs", []) if d.get("url") not in done]


def mark(state: dict, url: str, status: str) -> None:
    state.setdefault("done", {})[url] = status
