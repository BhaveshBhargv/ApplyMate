"""Export a ResumeData object to an ATS-friendly PDF (reportlab).

Mirrors the on-screen preview and the .docx export: centered navy name,
pipe-separated contact line, blue uppercase section headings with a hairline
rule, right-aligned dates, a single column, and real bullet lists. Section
order: Summary, Education, Experience, Skills, Projects, then extras.
"""
from __future__ import annotations

from io import BytesIO
from typing import List
from xml.sax.saxutils import escape

from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Table,
    TableStyle,
)

from models.resume_data import ResumeData

_ACCENT = "#2B5A9E"
_INK = "#1a2740"
_MUTED = "#55607a"


def _date_range(start: str, end: str, is_current: bool) -> str:
    end_text = "Present" if is_current else end
    if start and end_text:
        return f"{start} – {end_text}"
    return start or end_text or ""


def _styles():
    base = getSampleStyleSheet()
    return {
        "name": ParagraphStyle("Name", parent=base["Title"], fontSize=21, spaceAfter=2,
                               alignment=TA_CENTER, textColor=_INK),
        "contact": ParagraphStyle("Contact", parent=base["Normal"], fontSize=8.5, alignment=TA_CENTER,
                                  spaceAfter=6, textColor=_MUTED),
        "heading": ParagraphStyle("Heading", parent=base["Normal"], fontName="Helvetica-Bold",
                                  fontSize=11.5, spaceBefore=10, spaceAfter=2, textColor=_ACCENT),
        "body": ParagraphStyle("Body", parent=base["Normal"], fontSize=10, leading=13, spaceAfter=2, textColor=_INK),
        "title": ParagraphStyle("EntryTitle", parent=base["Normal"], fontSize=10, leading=13, textColor=_INK),
        "date": ParagraphStyle("EntryDate", parent=base["Normal"], fontSize=9.5, leading=13,
                               alignment=TA_RIGHT, textColor=_MUTED),
        "sub": ParagraphStyle("Sub", parent=base["Normal"], fontSize=9.5, leading=12, textColor=_MUTED),
        "bullet": ParagraphStyle("Bullet", parent=base["Normal"], fontSize=10, leading=13, textColor=_INK),
    }


def build_pdf(resume: ResumeData) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=LETTER,
        topMargin=0.55 * inch, bottomMargin=0.55 * inch,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch, title="Resume",
    )
    s = _styles()
    story: List = []

    def heading(text: str) -> None:
        story.append(Paragraph(escape(text.upper()), s["heading"]))
        story.append(HRFlowable(width="100%", thickness=1.0, color=_ACCENT, spaceBefore=1, spaceAfter=4))

    def entry_header(title_html: str, date_text: str) -> None:
        left = Paragraph(title_html, s["title"])
        right = Paragraph(f"<i>{escape(date_text)}</i>" if date_text else "", s["date"])
        tbl = Table([[left, right]], colWidths=[doc.width * 0.74, doc.width * 0.26])
        tbl.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(tbl)

    def bullets(items: List[str]) -> None:
        rows = [ListItem(Paragraph(escape(b), s["bullet"]), leftIndent=12) for b in items if b]
        if rows:
            story.append(ListFlowable(rows, bulletType="bullet", start="•", leftIndent=10, spaceAfter=2))

    pi = resume.personal_info
    story.append(Paragraph(escape(pi.full_name or "Your Name"), s["name"]))
    contact = " | ".join(b for b in [pi.email, pi.phone, pi.location, pi.linkedin_url, pi.portfolio_url] if b)
    if contact:
        story.append(Paragraph(escape(contact), s["contact"]))

    if pi.professional_summary:
        heading("Profile")
        story.append(Paragraph(escape(pi.professional_summary), s["body"]))

    if resume.education:
        heading("Education")
        for edu in resume.education:
            degree = ", ".join(x for x in [edu.degree, edu.field_of_study] if x)
            title = f"<b>{escape(degree)}</b>" if degree else ""
            if edu.institution:
                title += f"  &middot;  {escape(edu.institution)}"
            entry_header(title, _date_range(edu.start_date, edu.end_date, edu.is_current))
            if edu.gpa:
                story.append(Paragraph(f"<i>GPA: {escape(edu.gpa)}</i>", s["sub"]))
            bullets(edu.achievements)

    if resume.experience:
        heading("Work Experience")
        for exp in resume.experience:
            title = f"<b>{escape(exp.job_title)}</b>" if exp.job_title else ""
            if exp.company:
                title += f"  &middot;  {escape(exp.company)}"
            entry_header(title, _date_range(exp.start_date, exp.end_date, exp.is_current))
            if exp.location:
                story.append(Paragraph(f"<i>{escape(exp.location)}</i>", s["sub"]))
            bullets(exp.bullet_points)

    if any(cat.skills for cat in resume.skills):
        heading("Skills")
        for cat in resume.skills:
            if cat.skills:
                story.append(Paragraph(f"<b>{escape(cat.category_name)}:</b> {escape(', '.join(cat.skills))}", s["body"]))

    if resume.projects:
        heading("Projects")
        for proj in resume.projects:
            title = f"<b>{escape(proj.name or 'Project')}</b>"
            if proj.technologies:
                title += f'  <font color="{_MUTED}">&middot; {escape(", ".join(proj.technologies))}</font>'
            entry_header(title, "")
            if proj.description:
                story.append(Paragraph(escape(proj.description), s["body"]))
            bullets(proj.bullet_points)
            if proj.url:
                story.append(Paragraph(f'<font color="{_ACCENT}">{escape(proj.url)}</font>', s["sub"]))

    for extra in resume.extra_sections:
        if extra.heading or extra.content:
            heading(extra.heading or "Additional Information")
            if extra.content:
                story.append(Paragraph(escape(extra.content).replace("\n", "<br/>"), s["body"]))

    doc.build(story)
    return buffer.getvalue()
