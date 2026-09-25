"""Export a ResumeData object to an ATS-friendly Word (.docx) document.

Matches the on-screen preview (utils/resume_html.py) and the reference format:
centered navy name, a pipe-separated contact line, blue uppercase section
headings with a hairline rule, right-aligned dates, single column, real
bullet lists. No tables-for-layout, columns, or graphics -- the things ATS
parsers choke on. Section order: Summary, Education, Experience, Skills,
Projects, then any extra sections carried over from an upload.
"""
from __future__ import annotations

from io import BytesIO

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from models.resume_data import ResumeData
from utils import social_links

_ACCENT = RGBColor(0x2B, 0x5A, 0x9E)
_ACCENT_HEX = "2B5A9E"
_INK = RGBColor(0x1A, 0x27, 0x40)
_MUTED = RGBColor(0x55, 0x60, 0x7A)
_RIGHT_TAB = Inches(7.1)  # usable width with 0.7" side margins on Letter


def _add_hyperlink(paragraph, href: str, text: str, *, size_pt: float, color_hex: str) -> None:
    """Append a real clickable hyperlink run to `paragraph`.

    python-docx has no built-in hyperlink support -- paragraph.add_run() only
    ever produces plain text -- so this builds the OOXML <w:hyperlink>
    element by hand; it's the standard recipe for this in python-docx.
    `href` must already be a fully-qualified URL/mailto.
    """
    part = paragraph.part
    r_id = part.relate_to(href, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)

    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)

    run = OxmlElement("w:r")
    run_pr = OxmlElement("w:rPr")

    color = OxmlElement("w:color")
    color.set(qn("w:val"), color_hex)
    run_pr.append(color)

    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    run_pr.append(underline)

    size = OxmlElement("w:sz")
    size.set(qn("w:val"), str(int(size_pt * 2)))  # w:sz is in half-points
    run_pr.append(size)

    run.append(run_pr)
    text_el = OxmlElement("w:t")
    text_el.text = text
    run.append(text_el)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def _date_range(start: str, end: str, is_current: bool) -> str:
    end_text = "Present" if is_current else end
    if start and end_text:
        return f"{start} – {end_text}"
    return start or end_text or ""


def _add_bottom_border(paragraph) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "8")
    bottom.set(qn("w:space"), "2")
    bottom.set(qn("w:color"), "2B5A9E")
    borders.append(bottom)
    p_pr.append(borders)


def build_docx(resume: ResumeData) -> bytes:
    doc = Document()

    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = _INK
    for section in doc.sections:
        section.top_margin = section.bottom_margin = Inches(0.55)
        section.left_margin = section.right_margin = Inches(0.7)

    pi = resume.personal_info

    def heading(text: str) -> None:
        para = doc.add_paragraph()
        para.paragraph_format.space_before = Pt(9)
        para.paragraph_format.space_after = Pt(3)
        run = para.add_run(text.upper())
        run.bold = True
        run.font.size = Pt(11.5)
        run.font.color.rgb = _ACCENT
        _add_bottom_border(para)

    def entry_header(title: str, company: str, date_text: str) -> None:
        para = doc.add_paragraph()
        para.paragraph_format.space_before = Pt(4)
        para.paragraph_format.tab_stops.add_tab_stop(_RIGHT_TAB, WD_TAB_ALIGNMENT.RIGHT)
        if title:
            para.add_run(title).bold = True
        if company:
            para.add_run(f"  ·  {company}")
        if date_text:
            para.add_run("\t")
            dr = para.add_run(date_text)
            dr.italic = True
            dr.font.color.rgb = _MUTED

    # --- Header ---------------------------------------------------------------
    name_p = doc.add_paragraph()
    name_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    name_run = name_p.add_run(pi.full_name or "Your Name")
    name_run.bold = True
    name_run.font.size = Pt(21)
    name_run.font.color.rgb = _INK

    # (text, href, icon_path) -- href is None for plain (non-linked) items;
    # icon_path is None when there's no badge for that item (phone, location,
    # a non-GitHub portfolio site).
    contact_items = []
    if pi.email:
        contact_items.append((pi.email, f"mailto:{pi.email}", None))
    if pi.phone:
        contact_items.append((pi.phone, None, None))
    if pi.location:
        contact_items.append((pi.location, None, None))
    if pi.linkedin_url:
        contact_items.append((social_links.linkedin_handle(pi.linkedin_url),
                              social_links.display_href(pi.linkedin_url),
                              social_links.LINKEDIN_ICON_PATH))
    if pi.portfolio_url:
        if social_links.is_github_url(pi.portfolio_url):
            contact_items.append((social_links.github_handle(pi.portfolio_url),
                                  social_links.display_href(pi.portfolio_url),
                                  social_links.GITHUB_ICON_PATH))
        else:
            contact_items.append((social_links.portfolio_domain_label(pi.portfolio_url),
                                  social_links.display_href(pi.portfolio_url), None))

    if contact_items:
        c = doc.add_paragraph()
        c.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for i, (text, href, icon_path) in enumerate(contact_items):
            if i > 0:
                sep = c.add_run("  |  ")
                sep.font.size = Pt(9)
                sep.font.color.rgb = _MUTED
            if icon_path:
                # The icon sits right before its handle but isn't itself part
                # of the hyperlink run (python-docx has no easy way to put a
                # picture inside a <w:hyperlink>) -- the adjoining "/handle"
                # text carries the actual click target.
                icon_run = c.add_run()
                icon_run.add_picture(icon_path, height=Pt(9))
                _add_hyperlink(c, href, f"/{text}", size_pt=9, color_hex=_ACCENT_HEX)
            elif href:
                _add_hyperlink(c, href, text, size_pt=9, color_hex=_ACCENT_HEX)
            else:
                run = c.add_run(text)
                run.font.size = Pt(9)
                run.font.color.rgb = _MUTED

    # --- Summary --------------------------------------------------------------
    if pi.professional_summary:
        heading("Profile")
        doc.add_paragraph(pi.professional_summary)

    # --- Education (before experience, matching the reference) -----------------
    if resume.education:
        heading("Education")
        for edu in resume.education:
            degree = ", ".join(x for x in [edu.degree, edu.field_of_study] if x)
            entry_header(degree, edu.institution, _date_range(edu.start_date, edu.end_date, edu.is_current))
            if edu.gpa:
                gpa_p = doc.add_paragraph()
                gr = gpa_p.add_run(f"GPA: {edu.gpa}")
                gr.italic = True
                gr.font.color.rgb = _MUTED
            for ach in edu.achievements:
                doc.add_paragraph(ach, style="List Bullet")

    # --- Experience -----------------------------------------------------------
    if resume.experience:
        heading("Work Experience")
        for exp in resume.experience:
            entry_header(exp.job_title, exp.company, _date_range(exp.start_date, exp.end_date, exp.is_current))
            if exp.location:
                loc = doc.add_paragraph()
                lr = loc.add_run(exp.location)
                lr.italic = True
                lr.font.color.rgb = _MUTED
            for bullet in exp.bullet_points:
                doc.add_paragraph(bullet, style="List Bullet")

    # --- Skills ---------------------------------------------------------------
    if any(cat.skills for cat in resume.skills):
        heading("Skills")
        for cat in resume.skills:
            if cat.skills:
                para = doc.add_paragraph()
                para.add_run(f"{cat.category_name}: ").bold = True
                para.add_run(", ".join(cat.skills))

    # --- Projects -------------------------------------------------------------
    if resume.projects:
        heading("Projects")
        for proj in resume.projects:
            entry_header(proj.name or "Project", ", ".join(proj.technologies), "")
            if proj.description:
                doc.add_paragraph(proj.description)
            for bullet in proj.bullet_points:
                doc.add_paragraph(bullet, style="List Bullet")
            if proj.url:
                link = doc.add_paragraph()
                lr = link.add_run(proj.url)
                lr.font.color.rgb = _ACCENT

    # --- Extra sections -------------------------------------------------------
    for extra in resume.extra_sections:
        if extra.heading or extra.content:
            heading(extra.heading or "Additional Information")
            if extra.content:
                doc.add_paragraph(extra.content)

    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()
