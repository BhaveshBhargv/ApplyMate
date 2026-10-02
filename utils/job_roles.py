"""Suggests job roles to search for, based on the résumé's own content.

Recency decides priority, not section: whatever the résumé shows as most
recent -- a current job, a degree finishing this year, anything with dates --
weighs most. Items are put on one timeline (latest first) and both paths read
it in that order: the AI path (utils.ai_assistant, via the OpenRouter client)
gets the facts in that order, and the keyword fallback (no API key, or the AI
call failed) walks it top to bottom.

Only experience and education carry dates. Projects, skills and extra sections
are undated, so they rank after every dated item, in the order the résumé
lists them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from models.resume_data import ResumeData
from utils import ai_assistant
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


def _hint_from_field(text: str) -> Optional[str]:
    lowered = text.lower()
    for keyword, role in _FIELD_ROLE_HINTS:
        if keyword in lowered:
            return role
    return None


def _hint_from_tokens(tokens: List[str]) -> Optional[str]:
    lowered = " ".join(tokens).lower()
    for keywords, role in _SKILL_ROLE_HINTS:
        if any(keyword in lowered for keyword in keywords):
            return role
    return None


def _role_for(signal: _Signal, resume: ResumeData) -> Optional[str]:
    if signal.kind == "experience":
        return signal.entry.job_title.strip() or None
    if signal.kind == "education":
        return _hint_from_field(f"{signal.entry.degree} {signal.entry.field_of_study}")
    if signal.kind == "project":
        return _hint_from_tokens(list(signal.entry.technologies))
    if signal.kind == "skills":
        return _hint_from_tokens(resume.all_skills_flat())
    return None  # extra sections are free text; no reliable role to read off


def _fallback_roles(resume: ResumeData, max_roles: int) -> List[str]:
    """Heuristic role suggestions when there's no AI key (or the AI call
    failed): walk the timeline most-recent-first, take whatever role each item
    points to, dedupe case-insensitively, stop at `max_roles`. A job title is
    used as written; a degree or a tech list only yields a role on a real
    keyword match -- an unmatched one is skipped rather than forced."""
    roles: List[str] = []
    seen = set()
    for signal in _timeline(resume):
        role = _role_for(signal, resume)
        key = role.strip().lower() if role else ""
        if key and key not in seen:
            roles.append(role.strip())
            seen.add(key)
            if len(roles) == max_roles:
                break
    return roles


# --- AI path -----------------------------------------------------------------

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


def _timeline_facts(resume: ResumeData) -> str:
    """The résumé as numbered facts, most recent first, for the AI prompt."""
    blocks: List[str] = []
    for n, signal in enumerate(_timeline(resume), 1):
        lines = _signal_lines(signal, resume)
        if lines and lines[0].split(":", 1)[-1].strip():
            lines[0] = f"{n}. {lines[0]}"
            blocks.append("\n".join(lines))
    summary = resume.personal_info.professional_summary.strip()
    if summary:
        blocks.append(f"Summary: {summary}")
    return "\n".join(blocks)


def suggest_roles(resume: ResumeData, max_roles: int = 3) -> List[str]:
    """Return up to `max_roles` job titles to search for.

    Empty if there's nothing in the résumé to base a suggestion on --
    callers should check has_enough_to_suggest() first to decide whether to
    show the suggestion row at all vs. a "add your résumé" prompt.
    """
    if not has_enough_to_suggest(resume):
        return []
    if ai_assistant.is_configured():
        try:
            roles = ai_assistant.suggest_job_roles(_timeline_facts(resume), max_roles=max_roles)
            if roles:
                return roles
        except ai_assistant.AIError:
            pass
    return _fallback_roles(resume, max_roles)
