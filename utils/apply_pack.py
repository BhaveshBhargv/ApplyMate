"""The 'ready-to-apply pack' for one job: a résumé tailored to it, a cover letter, and the
details the application form will ask for -- everything the user needs to press Submit on the
employer's own page.

Honesty rules (same as the rest of the app): nothing is invented. The tailored résumé is the
user's own résumé with the parts that matter to THIS job moved to the front (skills, bullets,
projects) -- wording is never changed. The cover letter comes from utils.cover_letter, which
only draws on the résumé and verifies every claim against it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

from models.resume_data import ResumeData
from utils import ats_analyzer, cover_letter as cl, cover_letter_pdf, pdf_export
from utils.job_search import JobPosting


@dataclass
class Pack:
    resume_pdf: bytes
    resume_file: str
    cover_pdf: bytes
    cover_file: str
    cover_text: str
    cover_flags: list = field(default_factory=list)
    fields: List[Tuple[str, str]] = field(default_factory=list)
    tailoring_note: str = ""


def _hits(text: str, terms) -> int:
    stems = ats_analyzer._stems(text)
    return sum(1 for t in terms if ats_analyzer._contains(stems, t.stems, slack=0))


def tailor_resume(resume: ResumeData, jd_text: str) -> Tuple[ResumeData, str]:
    """A copy of the résumé with job-relevant items first. Returns (copy, short description).
    Only ORDER changes: skills within a category, categories, bullets within a role, projects."""
    copy = ResumeData.from_dict(resume.to_dict())
    terms = [t for t in ats_analyzer.extract_jd_terms(jd_text) if t.kind != "soft"]
    if not terms:
        return copy, "No job-specific keywords found, so your résumé is unchanged."

    moved = 0
    for category in copy.skills:
        ordered = sorted(category.skills, key=lambda s: -_hits(s, terms))
        moved += ordered != category.skills
        category.skills = ordered
    ranked = sorted(copy.skills, key=lambda c: -sum(_hits(s, terms) for s in c.skills))
    moved += ranked != copy.skills
    copy.skills = ranked
    for exp in copy.experience:
        ordered = sorted(exp.bullet_points, key=lambda b: -_hits(b, terms))
        moved += ordered != exp.bullet_points
        exp.bullet_points = ordered
    projects = sorted(copy.projects, key=lambda p: -_hits(f"{p.name} {p.description} "
                                                          f"{' '.join(p.technologies)} {' '.join(p.bullet_points)}", terms))
    moved += projects != copy.projects
    copy.projects = projects
    note = ("Skills, bullets and projects most relevant to this job are listed first; wording unchanged."
            if moved else "Your résumé already leads with what this job asks for, so it is unchanged.")
    return copy, note


def autofill_fields(resume: ResumeData) -> List[Tuple[str, str]]:
    """The details application forms usually ask for, ready to copy."""
    info = resume.personal_info
    name = info.full_name.strip()
    first, last = (name.rsplit(" ", 1) if " " in name else (name, ""))   # "Mary Ann Smith" -> "Mary Ann", "Smith"
    rows = [
        ("Full name", info.full_name), ("First name", first), ("Last name", last),
        ("Email", info.email), ("Phone", info.phone), ("Location", info.location),
        ("LinkedIn", info.linkedin_url), ("Portfolio / GitHub", info.portfolio_url),
    ]
    if resume.experience:
        job = resume.experience[0]
        rows += [("Current / latest job title", job.job_title), ("Current / latest employer", job.company)]
    if resume.education:
        edu = resume.education[0]
        degree = " in ".join(p for p in [edu.degree, edu.field_of_study] if p)
        rows += [("Highest education", ", ".join(p for p in [degree, edu.institution] if p))]
    return [(k, v) for k, v in rows if v and v.strip()]


def build_pack(resume: ResumeData, job: JobPosting, jd_text: str, *, write_letter: bool = True) -> Pack:
    """Make the pack for one job. With write_letter=True this makes the cover-letter AI call
    (raises ai_assistant.AIError if it can't be written -- the caller can retry with
    write_letter=False to still get the tailored résumé and the form details)."""
    jd = jd_text or job.description or job.title
    tailored, note = tailor_resume(resume, jd)
    letter = cl.generate(tailored, jd, company=job.company, role=job.title) if write_letter else None
    stem = cover_letter_pdf.file_stem(resume, job.company)
    return Pack(
        resume_pdf=pdf_export.build_pdf(tailored),
        resume_file=f"{stem.replace('Cover_Letter', 'Resume')}.pdf",
        cover_pdf=(cover_letter_pdf.build_cover_letter_pdf(tailored, letter.text, company=job.company, role=job.title)
                   if letter else b""),
        cover_file=f"{stem}.pdf",
        cover_text=letter.text if letter else "",
        cover_flags=list(letter.flags) if letter else [],
        fields=autofill_fields(resume),
        tailoring_note=note,
    )
