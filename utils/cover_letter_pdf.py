"""Export a generated cover letter to PDF (reportlab).

A conventional business letter, and deliberately plain for the same reason the
résumé exports are: recruiters and ATS parsers read it as text. The header
(centered name, pipe-separated contact line, hairline rule) mirrors
pdf_export.py so the letter and the résumé read as one set of documents.

Only the header, date, and "Re:" line are supplied here -- the salutation, body,
and sign-off are the letter text exactly as the user reviewed it on screen.
"""
from __future__ import annotations

import re
from datetime import date
from io import BytesIO
from typing import List, Optional
from xml.sax.saxutils import escape

from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

from models.resume_data import ResumeData
from utils.cover_letter import paragraphs

_ACCENT = "#2B5A9E"
_INK = "#1a2740"
_MUTED = "#55607a"


def _styles():
    base = getSampleStyleSheet()
    return {
        "name": ParagraphStyle("Name", parent=base["Title"], fontSize=21, spaceAfter=2,
                               alignment=TA_CENTER, textColor=_INK),
        "contact": ParagraphStyle("Contact", parent=base["Normal"], fontSize=8.5,
                                  alignment=TA_CENTER, spaceAfter=6, textColor=_MUTED),
        "meta": ParagraphStyle("Meta", parent=base["Normal"], fontSize=9.5, leading=13,
                               spaceAfter=2, textColor=_MUTED),
        "subject": ParagraphStyle("Subject", parent=base["Normal"], fontSize=10, leading=14,
                                  spaceBefore=8, spaceAfter=10, textColor=_INK),
        "body": ParagraphStyle("Body", parent=base["Normal"], fontSize=10.5, leading=15.5,
                               spaceAfter=9, textColor=_INK),
    }


def file_stem(resume: ResumeData, company: str = "") -> str:
    """A filesystem-safe download name, e.g. 'Jane_Doe_Cover_Letter_Stripe'."""
    def slug(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9]+", "_", value or "").strip("_")

    parts = [slug(resume.personal_info.full_name) or "cover", "Cover_Letter"]
    if company.strip():
        parts.append(slug(company)[:40])
    return "_".join(p for p in parts if p)


def build_cover_letter_pdf(
    resume: ResumeData,
    letter_text: str,
    *,
    company: str = "",
    role: str = "",
    letter_date: Optional[date] = None,
) -> bytes:
    """Render `letter_text` as a one-page business letter and return PDF bytes."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=LETTER,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        leftMargin=0.9 * inch, rightMargin=0.9 * inch, title="Cover Letter",
    )
    s = _styles()
    story: List = []

    pi = resume.personal_info
    story.append(Paragraph(escape(pi.full_name or "Your Name"), s["name"]))
    contact = " | ".join(b for b in [pi.email, pi.phone, pi.location,
                                     pi.linkedin_url, pi.portfolio_url] if b)
    if contact:
        story.append(Paragraph(escape(contact), s["contact"]))
    story.append(HRFlowable(width="100%", thickness=1.0, color=_ACCENT,
                            spaceBefore=1, spaceAfter=10))

    story.append(Paragraph(escape((letter_date or date.today()).strftime("%d %B %Y")), s["meta"]))
    if company.strip():
        story.append(Paragraph(f"<b>{escape(company.strip())}</b>", s["meta"]))

    # Subject line, only from what we were actually given -- never invented.
    if role.strip():
        subject = f"Re: Application for {role.strip()}"
        if company.strip():
            subject += f" at {company.strip()}"
        story.append(Paragraph(escape(subject), s["subject"]))
    else:
        story.append(Spacer(1, 10))

    for block in paragraphs(letter_text):
        # Internal newlines are deliberate (a sign-off over the name), not wrapping.
        story.append(Paragraph(escape(block).replace("\n", "<br/>"), s["body"]))

    doc.build(story)
    return buffer.getvalue()
