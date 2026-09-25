"""AI-based résumé parsing: turns arbitrary résumé text into structured data.

utils.resume_parser's parser is heuristic and layout-specific -- every new
résumé format (a different section name, a multi-column sidebar, bullets that
don't survive text extraction as literal characters) can need new
special-casing, and there's no end to that list. This instead asks the model
already wired up for résumé rewriting (utils.ai_assistant) to read the same
extracted text and return the app's own data shape directly as JSON, which
generalizes across formats with no résumé-specific code.

This is the primary import path when an API key is configured; utils.forms
falls back to utils.resume_parser's rule-based parser when it isn't, or if
this raises for any reason (bad/empty response, model unavailable), so
import keeps working either way.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from models.resume_data import (
    EducationEntry,
    ExperienceEntry,
    ExtraSection,
    PersonalInfo,
    ProjectEntry,
    ResumeData,
    SkillCategory,
)
from utils import ai_assistant

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def is_available() -> bool:
    """True if an API key is configured, so the AI import path can run."""
    return ai_assistant.is_configured()


def _extract_json_object(text: str) -> Dict[str, Any]:
    """Pull the JSON object out of a model response, tolerant of a wrapping
    markdown code fence or stray text the model added despite instructions."""
    stripped = text.strip()
    fence = _JSON_FENCE_RE.search(stripped)
    if fence:
        stripped = fence.group(1).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ai_assistant.AIError("The model's response wasn't valid JSON.")
    try:
        data = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ai_assistant.AIError(f"The model's response wasn't valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ai_assistant.AIError("The model's response wasn't a JSON object.")
    return data


# --- Field coercion (never trust the model's typing -- validate everything) ---


def _str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _bool(value: Any) -> bool:
    return value is True


def _str_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _personal_info(data: Any) -> PersonalInfo:
    if not isinstance(data, dict):
        return PersonalInfo()
    return PersonalInfo(
        full_name=_str(data.get("full_name")),
        email=_str(data.get("email")),
        phone=_str(data.get("phone")),
        location=_str(data.get("location")),
        linkedin_url=_str(data.get("linkedin_url")),
        portfolio_url=_str(data.get("portfolio_url")),
        professional_summary=_str(data.get("professional_summary")),
    )


def _education(items: Any) -> List[EducationEntry]:
    out: List[EducationEntry] = []
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        entry = EducationEntry(
            institution=_str(item.get("institution")),
            degree=_str(item.get("degree")),
            field_of_study=_str(item.get("field_of_study")),
            start_date=_str(item.get("start_date")),
            end_date=_str(item.get("end_date")),
            is_current=_bool(item.get("is_current")),
            gpa=_str(item.get("gpa")),
            achievements=_str_list(item.get("achievements")),
        )
        if entry.institution or entry.degree or entry.achievements:
            out.append(entry)
    return out


def _experience(items: Any) -> List[ExperienceEntry]:
    out: List[ExperienceEntry] = []
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        entry = ExperienceEntry(
            company=_str(item.get("company")),
            job_title=_str(item.get("job_title")),
            location=_str(item.get("location")),
            start_date=_str(item.get("start_date")),
            end_date=_str(item.get("end_date")),
            is_current=_bool(item.get("is_current")),
            bullet_points=_str_list(item.get("bullet_points")),
        )
        if entry.company or entry.job_title or entry.bullet_points:
            out.append(entry)
    return out


def _projects(items: Any) -> List[ProjectEntry]:
    out: List[ProjectEntry] = []
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        entry = ProjectEntry(
            name=_str(item.get("name")),
            description=_str(item.get("description")),
            technologies=_str_list(item.get("technologies")),
            url=_str(item.get("url")),
            bullet_points=_str_list(item.get("bullet_points")),
        )
        if entry.name or entry.description or entry.bullet_points:
            out.append(entry)
    return out


def _skills(items: Any) -> List[SkillCategory]:
    out: List[SkillCategory] = []
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        skills = _str_list(item.get("skills"))
        if not skills:
            continue
        out.append(SkillCategory(category_name=_str(item.get("category_name")) or "Skills", skills=skills))
    return out


def _extra_sections(items: Any) -> List[ExtraSection]:
    out: List[ExtraSection] = []
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        heading = _str(item.get("heading"))
        content = _str(item.get("content"))
        if heading and content:
            out.append(ExtraSection(heading=heading, content=content))
    return out


def parse_resume_with_ai(raw_text: str) -> ResumeData:
    """Parse résumé text via the LLM into a ResumeData object.

    Raises ai_assistant.AIError on any failure (no key, model unavailable,
    unparsable response) -- callers should catch this and fall back to
    utils.resume_parser.parse_resume.
    """
    response = ai_assistant.extract_resume_json(raw_text)
    data = _extract_json_object(response)

    resume = ResumeData()
    resume.personal_info = _personal_info(data.get("personal_info"))
    resume.education = _education(data.get("education"))
    resume.experience = _experience(data.get("experience"))
    resume.projects = _projects(data.get("projects"))
    resume.skills = _skills(data.get("skills"))
    resume.extra_sections = _extra_sections(data.get("extra_sections"))
    return resume
