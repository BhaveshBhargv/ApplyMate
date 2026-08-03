"""Data models for resume content.

These dataclasses are the single source of truth for everything the user
enters across the multi-page form. UI pages (pages/*.py) never touch
st.session_state directly for resume content -- they go through
utils.session_manager, which reads and writes these objects. Keeping the
model separate from Streamlit means the same classes can later be reused
by the ATS analyzer, the AI rewrite features, and the document exporters.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import List


def _new_id() -> str:
    """Short unique id used as a Streamlit widget key for a list entry."""
    return uuid.uuid4().hex[:8]


@dataclass
class PersonalInfo:
    full_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin_url: str = ""
    portfolio_url: str = ""
    professional_summary: str = ""

    def is_complete(self) -> bool:
        """Minimum fields required for a usable resume header."""
        return bool(self.full_name and self.email and self.phone)


@dataclass
class EducationEntry:
    id: str = field(default_factory=_new_id)
    institution: str = ""
    degree: str = ""
    field_of_study: str = ""
    start_date: str = ""
    end_date: str = ""
    is_current: bool = False
    gpa: str = ""
    achievements: List[str] = field(default_factory=list)


@dataclass
class ExperienceEntry:
    id: str = field(default_factory=_new_id)
    company: str = ""
    job_title: str = ""
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    is_current: bool = False
    bullet_points: List[str] = field(default_factory=list)


@dataclass
class ProjectEntry:
    id: str = field(default_factory=_new_id)
    name: str = ""
    description: str = ""
    technologies: List[str] = field(default_factory=list)
    url: str = ""
    bullet_points: List[str] = field(default_factory=list)


@dataclass
class SkillCategory:
    id: str = field(default_factory=_new_id)
    category_name: str = ""
    skills: List[str] = field(default_factory=list)


@dataclass
class ExtraSection:
    """A block of content from an uploaded resume that didn't match any
    known section (Education, Experience, Projects, Skills). Kept verbatim,
    tagged with its original heading, so nothing from the uploaded file is
    silently dropped -- the user can decide what to do with it manually."""

    id: str = field(default_factory=_new_id)
    heading: str = ""
    content: str = ""


@dataclass
class ResumeData:
    """Aggregate root holding every section of the resume."""

    personal_info: PersonalInfo = field(default_factory=PersonalInfo)
    education: List[EducationEntry] = field(default_factory=list)
    experience: List[ExperienceEntry] = field(default_factory=list)
    projects: List[ProjectEntry] = field(default_factory=list)
    skills: List[SkillCategory] = field(default_factory=list)
    extra_sections: List[ExtraSection] = field(default_factory=list)

    def all_skills_flat(self) -> List[str]:
        """Flatten all skill categories into one deduplicated list.

        Used by the ATS keyword matcher in Phase 3 to compare what the
        user actually has against what the job description asks for.
        """
        flat: List[str] = []
        seen_lower = set()
        for category in self.skills:
            for skill in category.skills:
                normalized = skill.strip()
                if normalized and normalized.lower() not in seen_lower:
                    flat.append(normalized)
                    seen_lower.add(normalized.lower())
        return flat

    def completion_status(self) -> dict:
        """Report which sections have data, used to drive the progress UI."""
        return {
            "Personal Details": self.personal_info.is_complete(),
            "Education": len(self.education) > 0,
            "Experience": len(self.experience) > 0,
            "Projects": len(self.projects) > 0,
            "Skills": len(self.skills) > 0 and any(c.skills for c in self.skills),
        }

    def searchable_text(self) -> str:
        """Flatten every piece of user-entered content into one string.

        Used by the ATS analyzer (Phase 3) to check which job-description
        keywords actually appear anywhere in the resume -- not just in the
        Skills section, but in experience bullets, project descriptions,
        education, and any extra sections carried over from an upload.
        """
        parts: List[str] = [self.personal_info.professional_summary]
        for edu in self.education:
            parts += [edu.institution, edu.degree, edu.field_of_study, *edu.achievements]
        for exp in self.experience:
            parts += [exp.job_title, exp.company, *exp.bullet_points]
        for proj in self.projects:
            parts += [proj.name, proj.description, *proj.technologies, *proj.bullet_points]
        for cat in self.skills:
            parts += [cat.category_name, *cat.skills]
        for extra in self.extra_sections:
            parts += [extra.heading, extra.content]
        return " ".join(part for part in parts if part)

    def to_dict(self) -> dict:
        return asdict(self)
