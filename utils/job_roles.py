"""Turns a résumé into job-role suggestions: the AI facts and a keyword fallback.

This module is pure -- no network, no Streamlit. utils.resume_precompute runs
it (and the AI call) when the résumé is saved; the Jobs page only reads the
stored result.

Roles are ranked by FIT, not by emphasis. Demonstrated experience (work,
projects, education, skills) is weighed alongside stated goals (the profile /
objective text), and a role scores by how many *kinds* of evidence back it --
not by how many bullets or entries the résumé happens to spend on it. Recency
only orders the evidence and breaks ties: items sit on one timeline, latest
first. Only experience and education carry dates; projects, skills and extra
sections are undated and rank after every dated item, in the order listed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from models.resume_data import ResumeData
from utils.date_picker import month_year_to_ordinal

_CURRENT = 10**9      # a current/ongoing entry counts as the latest possible
_LAST = 10**6         # position for undated, non-list items (skills, extras)


def has_enough_to_suggest(resume: ResumeData) -> bool:
    """True if there's anything -- experience, education, projects, skills,
    or a summary -- to base a role suggestion on."""
    return bool(
        resume.experience
        or resume.education
        or resume.projects
        or any(category.skills for category in resume.skills)
        or resume.personal_info.professional_summary.strip()
    )


# --- Timeline ----------------------------------------------------------------

@dataclass
class _Signal:
    kind: str                 # experience | education | project | skills | extra
    entry: Any
    recency: Optional[int]    # month ordinal of the end date; None if undated
    start: Optional[int]      # month ordinal of the start date, tie-breaker
    order: int                # position within its own section (0 = listed first)
    rank: int                 # last-resort tie-break between sections


def _ordinal(value: str) -> Optional[int]:
    value = (value or "").strip()
    if not value:
        return None
    if value.lower() in ("present", "current", "now"):
        return _CURRENT
    return month_year_to_ordinal(value)


def _recency(entry: Any) -> Tuple[Optional[int], Optional[int]]:
    """(recency, start) for a dated entry. A current entry is the latest
    possible; one with no end date falls back to its start date."""
    start = _ordinal(entry.start_date)
    if entry.is_current:
        return _CURRENT, start
    end = _ordinal(entry.end_date)
    return (end if end is not None else start), start


def _sort_key(signal: _Signal) -> tuple:
    dated = signal.recency is not None
    return (
        0 if dated else 1,
        -(signal.recency or 0),
        -(signal.start or 0),
        signal.order,
        signal.rank,
    )


def _timeline(resume: ResumeData) -> List[_Signal]:
    """Every résumé item on one list, most recent first. Undated items follow
    the dated ones, interleaved across sections by their position in the
    section (everyone's first-listed item before anyone's second). The final
    tie-break between sections follows the résumé's own printed order
    (education, experience, projects), not any preference for one of them."""
    signals: List[_Signal] = []
    for i, exp in enumerate(resume.experience):
        recency, start = _recency(exp)
        signals.append(_Signal("experience", exp, recency, start, i, 1))
    for i, edu in enumerate(resume.education):
        recency, start = _recency(edu)
        signals.append(_Signal("education", edu, recency, start, i, 0))
    for i, proj in enumerate(resume.projects):
        signals.append(_Signal("project", proj, None, None, i, 2))
    if any(category.skills for category in resume.skills):
        signals.append(_Signal("skills", resume, None, None, _LAST, 3))
    for i, extra in enumerate(resume.extra_sections):
        signals.append(_Signal("extra", extra, None, None, _LAST + 1 + i, 4))
    signals.sort(key=_sort_key)
    return signals


# --- Keyword fallback --------------------------------------------------------

# Degree/field keyword -> a common entry-level role for that field. Checked
# as a substring of "<degree> <field_of_study>" lowercased. Deliberately a
# short list of common, unambiguous fields -- an unmatched degree is left
# alone rather than forcing a guess.
_FIELD_ROLE_HINTS: List[Tuple[str, str]] = [
    ("computer science", "Software Engineer"),
    ("software engineering", "Software Engineer"),
    ("information technology", "IT Support Specialist"),
    ("data science", "Data Analyst"),
    ("civil engineering", "Civil Engineer"),
    ("mechanical engineering", "Mechanical Engineer"),
    ("electrical engineering", "Electrical Engineer"),
    ("electronics", "Electronics Engineer"),
    ("business administration", "Business Analyst"),
    ("marketing", "Marketing Associate"),
    ("finance", "Financial Analyst"),
    ("accounting", "Accountant"),
    ("economics", "Business Analyst"),
    ("biotechnology", "Research Associate"),
    ("nursing", "Registered Nurse"),
    ("psychology", "HR Associate"),
    ("graphic design", "Graphic Designer"),
    ("architecture", "Architect"),
]

# Skill/technology keyword(s) -> a role commonly associated with them.
_SKILL_ROLE_HINTS: List[Tuple[Tuple[str, ...], str]] = [
    (("python", "django", "flask", "java", "javascript", "typescript", "c++", "c#"), "Software Engineer"),
    (("sql", "excel", "tableau", "power bi", "pandas"), "Data Analyst"),
    (("autocad", "revit", "site supervision", "staad"), "Civil Engineer"),
    (("figma", "sketch", "adobe xd", "ui design", "ux design"), "UI/UX Designer"),
    (("aws", "docker", "kubernetes", "terraform", "ci/cd"), "DevOps Engineer"),
    (("solidworks", "catia", "ansys"), "Mechanical Engineer"),
]


def _field_roles(text: str) -> List[str]:
    lowered = text.lower()
    return [role for keyword, role in _FIELD_ROLE_HINTS if keyword in lowered]


def _skill_roles(tokens: List[str]) -> List[str]:
    lowered = " ".join(tokens).lower()
    return [role for keywords, role in _SKILL_ROLE_HINTS if any(k in lowered for k in keywords)]


def _roles_for(signal: _Signal, resume: ResumeData) -> List[str]:
    """Every role one résumé item points to (a job title is used as written)."""
    if signal.kind == "experience":
        title = signal.entry.job_title.strip()
        return [title] if title else []
    if signal.kind == "education":
        return _field_roles(f"{signal.entry.degree} {signal.entry.field_of_study}")
    if signal.kind == "project":
        return _skill_roles(list(signal.entry.technologies))
    if signal.kind == "skills":
        return _skill_roles(resume.all_skills_flat())
    return []  # extra sections are free text; no reliable role to read off


def fallback_roles(resume: ResumeData, max_roles: int = 3) -> List[str]:
    """Heuristic role suggestions, used instantly and whenever the AI is
    unavailable or hasn't finished. Ranked by fit:

    - each résumé item (and the stated goal in the summary) votes for the
      roles it points to, tagged with what kind of item it is;
    - a role's score is the number of DIFFERENT kinds backing it -- work,
      education, project, skills, stated goal -- so a role shown in five
      bullets scores no higher than one shown once;
    - ties go to whichever role is backed by the most recent item.

    A job title is used as written; a degree or tech list only votes on a real
    keyword match, so an unmatched one adds nothing rather than a guess."""
    timeline = _timeline(resume)
    evidence: Dict[str, dict] = {}

    def note(role: str, kind: str, pos: int) -> None:
        key = role.strip().lower()
        if not key:
            return
        item = evidence.setdefault(key, {"role": role.strip(), "kinds": set(), "pos": pos})
        item["kinds"].add(kind)
        item["pos"] = min(item["pos"], pos)

    for pos, signal in enumerate(timeline):
        for role in _roles_for(signal, resume):
            note(role, signal.kind, pos)

    goals = resume.personal_info.professional_summary.strip()
    if goals:
        lowered = goals.lower()
        for role in _field_roles(goals) + _skill_roles([goals]):
            note(role, "goal", len(timeline))
        for item in evidence.values():
            if item["role"].lower() in lowered:
                item["kinds"].add("goal")

    ranked = sorted(evidence.values(), key=lambda item: (-len(item["kinds"]), item["pos"]))
    return [item["role"] for item in ranked[:max_roles]]


# --- AI facts ----------------------------------------------------------------

def _dates(entry: Any) -> str:
    end = "Present" if entry.is_current else entry.end_date
    if entry.start_date and end:
        return f" ({entry.start_date} - {end})"
    when = entry.start_date or end
    return f" ({when})" if when else ""


def _signal_lines(signal: _Signal, resume: ResumeData) -> List[str]:
    e = signal.entry
    if signal.kind == "experience":
        head = " at ".join(p for p in [e.job_title, e.company] if p)
        lines = [f"Work: {head}{_dates(e)}"]
        lines += [f"  - {b}" for b in e.bullet_points[:5]]
        return lines
    if signal.kind == "education":
        head = e.degree
        if e.field_of_study:
            head = f"{head} in {e.field_of_study}" if head else e.field_of_study
        head = ", ".join(p for p in [head, e.institution] if p)
        return [f"Education: {head}{_dates(e)}"]
    if signal.kind == "project":
        tech = f" ({', '.join(e.technologies)})" if e.technologies else ""
        lines = [f"Project: {e.name}{tech}"]
        if e.description:
            lines.append(f"  {e.description}")
        lines += [f"  - {b}" for b in e.bullet_points[:3]]
        return lines
    if signal.kind == "skills":
        return [f"Skills: {', '.join(resume.all_skills_flat())}"]
    return [f"{e.heading}: {e.content[:300]}"]


def timeline_facts(resume: ResumeData) -> str:
    """The résumé as text for the AI prompt: the stated goals (summary /
    objective) first and labelled, then the demonstrated items numbered most
    recent first."""
    blocks: List[str] = []
    goals = resume.personal_info.professional_summary.strip()
    if goals:
        blocks.append(f"STATED GOALS (the candidate's own summary/objective): {goals}")
    n = 0
    for signal in _timeline(resume):
        lines = _signal_lines(signal, resume)
        if lines and lines[0].split(":", 1)[-1].strip():
            n += 1
            lines[0] = f"{n}. {lines[0]}"
            blocks.append("\n".join(lines))
    return "\n".join(blocks)
