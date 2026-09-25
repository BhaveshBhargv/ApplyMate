"""Render a ResumeData object to the live "paper" preview HTML.

This is the on-screen resume sheet shown on the Dashboard. Its layout follows
the reference format: centered name, a pipe-separated contact line, blue
uppercase section headings with a hairline rule, a single column, and entries
whose dates sit right-aligned against the title. The .docx / .pdf exporters
mirror this same format so what you see is what you download.

All user content is HTML-escaped -- the resume text is untrusted input.
"""
from __future__ import annotations

from html import escape
from typing import List

from models.resume_data import ResumeData
from utils import social_links

_ACCENT = "#2B5A9E"
_INK = "#1a2740"


def _date(start: str, end: str, is_current: bool) -> str:
    end_text = "Present" if is_current else end
    if start and end_text:
        return f"{escape(start)} &ndash; {escape(end_text)}"
    return escape(start or end_text or "")


def _link(url: str, label: str) -> str:
    href = url if url.startswith(("http://", "https://", "mailto:")) else f"https://{url}"
    return f'<a href="{escape(href, quote=True)}" target="_blank">{escape(label)}</a>'


def _icon_link(url: str, icon_data_uri: str, handle: str) -> str:
    href = url if url.startswith(("http://", "https://", "mailto:")) else f"https://{url}"
    img = f'<img class="rb-social-icon" src="{icon_data_uri}" alt="">'
    return (f'<a class="rb-social-link" href="{escape(href, quote=True)}" target="_blank">'
            f'{img}/{escape(handle)}</a>')


def _entry(title_html: str, right: str, sub: str, bullets: List[str], tail: str = "") -> str:
    parts = ['<div class="rb-entry">']
    parts.append('<div class="rb-entry-head">')
    parts.append(f'<span class="rb-entry-title">{title_html}</span>')
    if right:
        parts.append(f'<span class="rb-entry-date">{right}</span>')
    parts.append("</div>")
    if sub:
        parts.append(f'<div class="rb-entry-sub">{sub}</div>')
    if bullets:
        parts.append('<ul class="rb-bullets">')
        parts += [f"<li>{escape(b)}</li>" for b in bullets if b]
        parts.append("</ul>")
    if tail:
        parts.append(f'<div class="rb-entry-tail">{tail}</div>')
    parts.append("</div>")
    return "".join(parts)


def _section(title: str, body: str) -> str:
    if not body:
        return ""
    return f'<h2 class="rb-h2">{escape(title)}</h2>{body}'


def render_resume_html(resume: ResumeData) -> str:
    """Return a self-contained HTML string for the resume preview sheet."""
    pi = resume.personal_info
    body: List[str] = []

    # Header: name + contact line
    name = escape(pi.full_name) if pi.full_name else '<span class="rb-ph">Your Name</span>'
    body.append(f'<div class="rb-name">{name}</div>')

    contact: List[str] = []
    if pi.email:
        contact.append(_link(f"mailto:{pi.email}", pi.email))
    if pi.phone:
        contact.append(escape(pi.phone))
    if pi.location:
        contact.append(escape(pi.location))
    if pi.linkedin_url:
        contact.append(_icon_link(pi.linkedin_url, social_links.linkedin_icon_data_uri(),
                                  social_links.linkedin_handle(pi.linkedin_url)))
    if pi.portfolio_url:
        if social_links.is_github_url(pi.portfolio_url):
            contact.append(_icon_link(pi.portfolio_url, social_links.github_icon_data_uri(),
                                      social_links.github_handle(pi.portfolio_url)))
        else:
            contact.append(_link(pi.portfolio_url, social_links.portfolio_domain_label(pi.portfolio_url)))
    if contact:
        body.append('<div class="rb-contact">' + ' <span class="rb-sep">|</span> '.join(contact) + "</div>")

    # Profile / summary
    if pi.professional_summary:
        body.append(_section("Profile", f'<p class="rb-para">{escape(pi.professional_summary)}</p>'))

    # Education (before experience, matching the reference format)
    edu_html = ""
    for ed in resume.education:
        deg = ", ".join(x for x in [ed.degree, ed.field_of_study] if x)
        title = " &middot; ".join(x for x in [f"<b>{escape(deg)}</b>" if deg else "",
                                              escape(ed.institution) if ed.institution else ""] if x)
        sub = f"GPA: {escape(ed.gpa)}" if ed.gpa else ""
        edu_html += _entry(title, _date(ed.start_date, ed.end_date, ed.is_current), sub, ed.achievements)
    body.append(_section("Education", edu_html))

    # Experience
    exp_html = ""
    for e in resume.experience:
        title = " &middot; ".join(x for x in [f"<b>{escape(e.job_title)}</b>" if e.job_title else "",
                                              escape(e.company) if e.company else ""] if x)
        exp_html += _entry(title, _date(e.start_date, e.end_date, e.is_current),
                           escape(e.location) if e.location else "", e.bullet_points)
    body.append(_section("Work Experience", exp_html))

    # Skills
    if any(c.skills for c in resume.skills):
        rows = "".join(
            f'<div class="rb-skill"><b>{escape(c.category_name)}:</b> {escape(", ".join(c.skills))}</div>'
            for c in resume.skills if c.skills
        )
        body.append(_section("Skills", rows))

    # Projects
    proj_html = ""
    for p in resume.projects:
        title = f"<b>{escape(p.name)}</b>" if p.name else ""
        if p.technologies:
            title += f' <span class="rb-muted">&middot; {escape(", ".join(p.technologies))}</span>'
        tail = _link(p.url, p.url) if p.url else ""
        sub = escape(p.description) if p.description else ""
        proj_html += _entry(title, "", sub, p.bullet_points, tail)
    body.append(_section("Projects", proj_html))

    # Extra sections (from an uploaded resume)
    for extra in resume.extra_sections:
        if extra.heading or extra.content:
            para = "<br>".join(escape(line) for line in extra.content.splitlines() if line.strip())
            body.append(_section(extra.heading or "Additional", f'<p class="rb-para">{para}</p>'))

    inner = "".join(body)
    if not inner or inner == f'<div class="rb-name"><span class="rb-ph">Your Name</span></div>':
        inner += '<p class="rb-empty">Start filling in the panel on the left &mdash; your résumé appears here, live.</p>'

    return f"""
<style>
/* Scroll the sheet in place: fixed viewport height, content scrolls inside,
   so a long résumé no longer stretches the whole page. */
.rb-scroll {{
  max-height: calc(100vh - 40px);
  overflow-y: auto;
  overflow-x: hidden;
  padding: 6px 10px 6px 2px;
  scrollbar-width: thin;
  scrollbar-color: #c3c9d4 transparent;
}}
.rb-scroll::-webkit-scrollbar {{ width: 9px; }}
.rb-scroll::-webkit-scrollbar-thumb {{
  background: #c3c9d4; border-radius: 6px; border: 2px solid transparent; background-clip: content-box;
}}
.rb-scroll::-webkit-scrollbar-thumb:hover {{ background: #a8b0be; background-clip: content-box; }}
@keyframes rb-doc-in {{ from {{ opacity: .85; }} to {{ opacity: 1; }} }}
.rb-doc {{
  background: #fff; color: {_INK};
  font-family: "Calibri", "Carlito", "Segoe UI", system-ui, sans-serif;
  font-size: 12.6px; line-height: 1.44;
  padding: 40px 44px; border-radius: 4px;
  box-shadow: 0 12px 30px rgba(22,35,62,.12), 0 2px 6px rgba(22,35,62,.08);
  border: 1px solid #eceff4;
  /* Whisper-settle so a save reads as "the sheet refreshed", not a flash. */
  animation: rb-doc-in .22s ease-out both;
}}
@media (prefers-reduced-motion: reduce) {{ .rb-doc {{ animation: none; }} }}
.rb-doc .rb-name {{
  text-align: center; font-size: 25px; font-weight: 700;
  color: {_INK}; letter-spacing: .2px; margin-bottom: 4px;
}}
.rb-doc .rb-ph, .rb-doc .rb-empty {{ color: #9aa3b2; font-weight: 400; }}
.rb-doc .rb-empty {{ text-align: center; font-style: italic; margin-top: 28px; }}
.rb-doc .rb-contact {{ text-align: center; font-size: 11px; color: #48505f; margin-bottom: 6px; }}
.rb-doc .rb-contact a {{ color: {_ACCENT}; text-decoration: none; }}
.rb-doc .rb-sep {{ color: #c3c9d4; }}
.rb-doc .rb-social-link {{ display: inline-flex; align-items: center; gap: 3px; vertical-align: middle; }}
.rb-doc .rb-social-icon {{ width: 12px; height: 12px; border-radius: 3px; display: inline-block; }}
.rb-doc .rb-h2 {{
  font-size: 12.5px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 1.1px; color: {_ACCENT};
  border-bottom: 1.4px solid {_ACCENT}; padding-bottom: 2px;
  margin: 14px 0 7px;
}}
.rb-doc .rb-entry {{ margin-bottom: 8px; }}
.rb-doc .rb-entry-head {{ display: flex; justify-content: space-between; gap: 12px; align-items: baseline; }}
.rb-doc .rb-entry-title {{ }}
.rb-doc .rb-entry-date {{ font-style: italic; color: #55607a; white-space: nowrap; font-size: 11.5px; }}
.rb-doc .rb-entry-sub {{ color: #55607a; font-size: 11.5px; }}
.rb-doc .rb-entry-tail {{ font-size: 11px; margin-top: 1px; }}
.rb-doc .rb-entry-tail a {{ color: {_ACCENT}; text-decoration: none; }}
.rb-doc .rb-para {{ margin: 0 0 2px; text-align: justify; }}
.rb-doc .rb-skill {{ margin-bottom: 2px; }}
.rb-doc .rb-muted {{ color: #6b7488; font-weight: 400; }}
.rb-doc ul.rb-bullets {{ margin: 2px 0 0; padding-left: 18px; }}
.rb-doc ul.rb-bullets li {{ margin-bottom: 1px; }}
</style>
<div class="rb-scroll"><div class="rb-doc">{inner}</div></div>
"""
