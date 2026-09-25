"""Best-effort parser for uploaded resume files (.pdf, .docx, .txt).

Resumes come in wildly inconsistent formats, so this is deliberately a
heuristic, rule-based parser (regex + section-heading detection) rather than
an attempt at perfect extraction. It only ever transcribes what the uploaded
file actually says -- never invents content -- but "faithful" isn't the same
as "accurate": a line can still be misread or misfiled. The calling page
loads the result straight into the resume data (it shows up immediately on
every page, including Review), and each page's own "Save" button remains
available to correct anything the parser got wrong.

Content that can't be confidently mapped to a known section (Education,
Experience, Projects, Skills) is preserved verbatim as an ExtraSection
tagged with its original heading from the resume, so nothing is silently
dropped -- see split_into_sections().
"""
from __future__ import annotations

import calendar
import io
import re
from typing import Dict, List, Optional, Tuple

from models.resume_data import (
    EducationEntry,
    ExperienceEntry,
    ExtraSection,
    PersonalInfo,
    ProjectEntry,
    ResumeData,
    SkillCategory,
)

# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------


def extract_text(file_bytes: bytes, filename: str) -> str:
    """Extract raw text from an uploaded .pdf, .docx, or .txt file."""
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return _extract_pdf_text(file_bytes)
    if lower.endswith(".docx"):
        return _extract_docx_text(file_bytes)
    if lower.endswith(".txt"):
        return file_bytes.decode("utf-8", errors="ignore")
    raise ValueError(f"Unsupported file type: {filename}. Please upload a .pdf, .docx, or .txt file.")


def _extract_pdf_text(file_bytes: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(file_bytes))
    return "\n".join(_extract_pdf_page_text(page) for page in reader.pages)


def _extract_pdf_page_text(page) -> str:
    """Extract one page's text, preserving reading order for narrow-sidebar
    résumé templates (a slim label column -- CONTACT, EXPERIENCE, EDUCATION...
    -- beside a wide content column).

    pypdf's default extraction follows the PDF content stream's write order,
    which for these templates groups every sidebar label together ahead of
    every column's content, misfiling entire sections. "layout" mode instead
    reconstructs each output line from actual character positions, so a
    sidebar label lands on the same line as the content beside it -- which in
    turn glues the two together (undone below by _split_glued_sidebar_lines)
    and can occasionally merge two tightly-kerned words with no space between
    them. That's a minor readability glitch, not lost or misfiled content,
    and a fair trade for whole sections landing in the right place.
    """
    plain = page.extract_text() or ""
    try:
        layout = page.extract_text(extraction_mode="layout") or ""
    except Exception:  # noqa: BLE001 -- fall back to plain extraction
        return plain
    if len(layout.strip()) < 0.5 * len(plain.strip()):
        return plain  # layout mode produced far less text; trust plain instead

    split = _split_glued_sidebar_lines(layout)
    collapsed = "\n".join(re.sub(r" {2,}", " ", line).rstrip() for line in split.splitlines())
    return _split_inline_colon_headers(collapsed)


# A sidebar label glued onto its content row by layout-mode extraction: a
# short leading token, then a wide run of column-alignment spaces (real
# inter-word gaps within justified body text are never this wide), then the
# row's actual content.
_GLUED_HEADER_RE = re.compile(r"^(\s*)(\S(?:.*\S)?)(\s{14,})(\S.*)$")
_NO_SPLIT_LABELS = {
    "contact", "contact info", "contact information", "contact details",
    "personal info", "personal information", "details",
}


def _split_glued_sidebar_lines(text: str) -> str:
    """Pull a glued sidebar label back onto its own line.

    Undoes exactly the side effect _extract_pdf_page_text relies on layout
    mode for: a label like "EDUCATION" landing on the same output line as
    the content beside it, which would otherwise never be short/standalone
    enough for _looks_like_header to recognize as a section boundary.
    "Contact"-like labels are left glued -- their info stays part of the
    preamble that parse_personal_info scans, same as today.

    A label that itself word-wraps across two sidebar rows (e.g. "PAPER" /
    "PUBLISHED", or "PROFESSIONAL" / "EXPERIENCE") produces two separate
    synthetic header lines back to back; when the first of those isn't
    already a recognized section on its own, the second is folded into it
    (both label and content) instead of starting a new section, so the pair
    reads as the one heading it visually is.
    """
    out_lines: List[str] = []
    pending_header_idx: Optional[int] = None
    for line in text.splitlines():
        match = _GLUED_HEADER_RE.match(line)
        if match:
            label, rest = match.group(2), match.group(4)
            normalized = re.sub(r"[^a-z ]", "", label.lower()).strip()
            if _looks_like_header(label) and normalized not in _NO_SPLIT_LABELS:
                if pending_header_idx is not None and _canonical_section(out_lines[pending_header_idx]) is None:
                    out_lines[pending_header_idx] = f"{out_lines[pending_header_idx]} {label}"
                else:
                    out_lines.append(label)
                    pending_header_idx = len(out_lines) - 1
                out_lines.append(rest)
                continue
        pending_header_idx = None
        out_lines.append(line)
    return "\n".join(out_lines)


# An inline sub-heading that names a known section but shares its row with
# that section's own first line of content (e.g. "TECHNICAL SKILLS: Auto
# CAD, Revit"), rather than sitting in a narrow sidebar column -- so it never
# has the wide alignment gap _GLUED_HEADER_RE looks for. Only split out when
# the label itself is a recognized alias, so an ordinary "Note: ..." or
# "Location: London" line is never mistaken for a section boundary.
_COLON_HEADER_RE = re.compile(r"^\s*([A-Za-z][A-Za-z &/]{1,28}):\s+(\S.*)$")


def _split_inline_colon_headers(text: str) -> str:
    out_lines: List[str] = []
    for line in text.splitlines():
        match = _COLON_HEADER_RE.match(line)
        if match and _canonical_section(match.group(1)) is not None:
            out_lines.append(match.group(1))
            out_lines.append(match.group(2))
            continue
        out_lines.append(line)
    return "\n".join(out_lines)


def _extract_docx_text(file_bytes: bytes) -> str:
    import docx

    document = docx.Document(io.BytesIO(file_bytes))
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


# ---------------------------------------------------------------------------
# Section detection
# ---------------------------------------------------------------------------

_SECTION_ALIASES: Dict[str, List[str]] = {
    "summary": ["summary", "professional summary", "objective", "profile", "about", "about me"],
    "education": ["education", "academic background", "academics", "education  training"],
    "experience": [
        "experience",
        "work experience",
        "employment history",
        "professional experience",
        "work history",
        "career history",
    ],
    "projects": ["projects", "personal projects", "academic projects", "key projects"],
    "skills": ["skills", "technical skills", "core competencies", "skills  tools", "skills and tools"],
}

_MAX_HEADER_LEN = 40


def _canonical_section(header: str) -> Optional[str]:
    normalized = re.sub(r"[^a-z& ]", "", header.lower()).strip()
    for canonical, aliases in _SECTION_ALIASES.items():
        if normalized in aliases:
            return canonical
    return None


def _looks_like_header(line: str) -> bool:
    """A line is treated as a section heading if it's short, standalone
    (not a bullet), doesn't end like a sentence, and has no digits (which
    rules out date ranges and "GPA: 3.8" style lines). Beyond that:

    - ALL CAPS lines are accepted outright -- a strong, distinctive signal
      resumes use specifically for section headings.
    - Title Case lines are only accepted if they match a known section
      alias (e.g. "Education", "Work Experience"). A bare Title Case check
      would also match a person's name or a job title line, which are not
      section headings -- under-detecting a custom Title Case heading
      (like "Volunteer Experience") is the safer failure mode than
      swallowing real content into the wrong section.
    """
    stripped = line.strip()
    if not stripped or len(stripped) > _MAX_HEADER_LEN:
        return False
    if stripped.startswith(("-", "*", "•", "–", "—")):
        return False
    if stripped.endswith((".", ",", ";")):
        return False
    if not any(c.isalpha() for c in stripped):
        return False
    if any(c.isdigit() for c in stripped):
        return False

    if stripped.upper() == stripped:
        return True
    return _canonical_section(stripped) is not None


def _merge_wrapped_headers(
    headers: List[Tuple[int, str]]
) -> Tuple[List[Tuple[int, str]], set]:
    """Merge a section label that word-wraps across two consecutive header
    lines (e.g. "PROFESSIONAL" / "EXPERIENCE" from a narrow sidebar column)
    back into the one recognized header their join spells out -- otherwise
    the content between them would land in a same-named-but-unrecognized
    section instead of the real one. Only fires when the *combined* text
    matches a known alias that neither line matches alone, so two genuinely
    separate custom headings are never merged on a guess.

    Returns the merged header list, plus the set of original line numbers
    absorbed into a merge (so their now-redundant label text can be dropped
    from the section body by the caller).
    """
    merged: List[Tuple[int, str]] = []
    consumed: set = set()
    i = 0
    while i < len(headers):
        line_no, header_text = headers[i]
        if i + 1 < len(headers) and _canonical_section(header_text) is None:
            next_line_no, next_text = headers[i + 1]
            combined = f"{header_text} {next_text}"
            if _canonical_section(combined) is not None:
                merged.append((line_no, combined))
                consumed.add(next_line_no)
                i += 2
                continue
        merged.append((line_no, header_text))
        i += 1
    return merged, consumed


def split_into_sections(text: str) -> Tuple[str, Dict[str, str], Dict[str, str]]:
    """Split resume text into (preamble, known_sections, extra_sections).

    preamble is everything before the first detected heading (usually
    contact info, sometimes a summary). known_sections maps a canonical
    name ("education", "experience", "projects", "skills", "summary") to
    its raw text. extra_sections maps the ORIGINAL heading text (exactly as
    written in the resume) to its raw content, for anything that isn't a
    recognized section -- e.g. "Certifications", "Awards", "Languages".
    """
    lines = text.splitlines()
    raw_headers = [(i, line.strip()) for i, line in enumerate(lines) if _looks_like_header(line)]

    if not raw_headers:
        return text.strip(), {}, {}

    headers, consumed = _merge_wrapped_headers(raw_headers)
    preamble = "\n".join(lines[: headers[0][0]]).strip()

    known: Dict[str, str] = {}
    extra: Dict[str, str] = {}
    for idx, (line_no, header_text) in enumerate(headers):
        start = line_no + 1
        end = headers[idx + 1][0] if idx + 1 < len(headers) else len(lines)
        block_lines = [ln for i, ln in enumerate(lines[start:end], start=start) if i not in consumed]
        block = "\n".join(block_lines).strip()
        if not block:
            continue
        canonical = _canonical_section(header_text)
        if canonical:
            known[canonical] = (known.get(canonical, "") + "\n" + block).strip()
        else:
            extra[header_text] = (extra.get(header_text, "") + "\n" + block).strip()

    return preamble, known, extra


# ---------------------------------------------------------------------------
# Contact info / personal details
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"(\+?\d[\d .()-]{7,}\d)")
_LINKEDIN_RE = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/\S+", re.IGNORECASE)
_GITHUB_RE = re.compile(r"(?:https?://)?(?:www\.)?github\.com/\S+", re.IGNORECASE)


def parse_personal_info(preamble: str) -> PersonalInfo:
    """Best-effort extraction of name/contact info from the text before the
    first section heading. Fields we can't confidently detect (e.g.
    location) are left blank rather than guessed."""
    info = PersonalInfo()
    lines = [line.strip() for line in preamble.splitlines() if line.strip()]

    email_match = _EMAIL_RE.search(preamble)
    if email_match:
        info.email = email_match.group(0).rstrip(".,;")

    phone_match = _PHONE_RE.search(preamble)
    if phone_match:
        info.phone = phone_match.group(0).strip()

    linkedin_match = _LINKEDIN_RE.search(preamble)
    if linkedin_match:
        info.linkedin_url = linkedin_match.group(0).rstrip(".,;")

    github_match = _GITHUB_RE.search(preamble)
    if github_match:
        info.portfolio_url = github_match.group(0).rstrip(".,;")

    # First line that isn't itself contact info is assumed to be the name.
    for line in lines:
        if _EMAIL_RE.search(line) or _PHONE_RE.search(line) or _LINKEDIN_RE.search(line):
            continue
        if len(line) <= 60:
            info.full_name = line
            break

    # Location: a "City, Country/State" segment on the pipe-delimited contact
    # line (e.g. "... | GitHub | Exeter, UK"). Only accepted when it clearly
    # isn't an email/phone/URL/social handle, so we never guess.
    for line in lines:
        if "|" not in line:
            continue
        for segment in line.split("|"):
            segment = segment.strip()
            if not segment or "," not in segment or len(segment) > 40:
                continue
            if any(c.isdigit() for c in segment):
                continue
            if _EMAIL_RE.search(segment) or re.search(r"linkedin|github|http|www\.|portfolio", segment, re.I):
                continue
            info.location = segment
            break
        if info.location:
            break

    return info


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

_MONTH_NAMES = {
    "jan": "Jan", "january": "Jan",
    "feb": "Feb", "february": "Feb",
    "mar": "Mar", "march": "Mar",
    "apr": "Apr", "april": "Apr",
    "may": "May",
    "jun": "Jun", "june": "Jun",
    "jul": "Jul", "july": "Jul",
    "aug": "Aug", "august": "Aug",
    "sep": "Sep", "sept": "Sep", "september": "Sep",
    "oct": "Oct", "october": "Oct",
    "nov": "Nov", "november": "Nov",
    "dec": "Dec", "december": "Dec",
}

# Dash characters used interchangeably for ranges: hyphen, non-breaking hyphen,
# figure dash, en dash, em dash, horizontal bar. PDFs use several of these.
_DASH = "-‐‑‒–—―"
# A month must be an actual month name -- matching any 3-9 letter word would
# swallow ordinary words that happen to precede a year (e.g. "Exeter 2026").
_MONTH_TOKEN = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
_DATE_TOKEN = rf"(?:{_MONTH_TOKEN}\s+\d{{4}}|\d{{1,2}}[/\-]\d{{4}}|\d{{4}})"
_DATE_RANGE_RE = re.compile(
    rf"(?P<start>{_DATE_TOKEN})"
    rf"\s*(?:[{_DASH}]|to)\s*"
    rf"(?P<end>{_DATE_TOKEN}|[Pp]resent|[Cc]urrent|[Nn]ow)"
)


def _normalize_date_fragment(fragment: str) -> str:
    """Convert a fragment like 'August 2019' or '08/2019' into this app's
    'Mon YYYY' format. Returns '' if the month can't be confidently
    determined -- a bare year like '2019' is left blank rather than
    guessing a month that was never stated."""
    fragment = fragment.strip().rstrip(".")

    match = re.match(r"([A-Za-z]{3,9})\.?\s+(\d{4})$", fragment)
    if match:
        month = _MONTH_NAMES.get(match.group(1).lower())
        if month:
            return f"{month} {match.group(2)}"

    match = re.match(r"(\d{1,2})[/\-](\d{4})$", fragment)
    if match:
        month_num = int(match.group(1))
        if 1 <= month_num <= 12:
            return f"{calendar.month_abbr[month_num]} {match.group(2)}"

    return ""


def find_date_range(text: str) -> Tuple[str, str, bool]:
    """Find the first date range in text. Returns (start_date, end_date,
    is_current) in this app's 'Mon YYYY' format; either date is '' if it
    couldn't be confidently parsed."""
    match = _DATE_RANGE_RE.search(text)
    if not match:
        return "", "", False
    start = _normalize_date_fragment(match.group("start"))
    end_raw = match.group("end")
    if re.match(r"present|current|now", end_raw, re.IGNORECASE):
        return start, "Present", True
    return start, _normalize_date_fragment(end_raw), False


# ---------------------------------------------------------------------------
# Line classification + splitting a section into per-entry blocks
# ---------------------------------------------------------------------------

# Bullet markers. Deliberately excludes the middle dot U+00B7, which many
# resumes use as a *separator* ("Title · Company"), not a bullet.
_BULLET_RE = re.compile(r"^\s*[•‣◦▪▸●○*]\s+|^\s*[-‐‑‒–—―]\s+")
# The middle dot / pipe separators that join a title to its organisation on a
# single header line: "Degree · Institution", "Job Title | Company".
_ORG_SEPARATORS = ("·", "|", "•")
# No \b before the year: a PDF extraction can glue a preceding label straight
# onto it with no space (e.g. a decorative "I" used as a separator producing
# "I2022"), and \w-\w has no boundary there. A digit lookbehind still blocks
# matching the tail of a longer number.
_BARE_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}\b")
_INPROGRESS_RE = re.compile(r"in progress|present|current|ongoing|expected|now", re.IGNORECASE)


def _is_bullet(line: str) -> bool:
    return bool(_BULLET_RE.match(line))


def _is_url_only(line: str) -> bool:
    """True if the whole line is just a URL/handle (e.g. a project repo link)."""
    stripped = line.strip()
    if not stripped or " " in stripped:
        return False
    return bool(re.match(r"^(?:https?://)?[\w.-]+\.[a-z]{2,}(?:/\S*)?$", stripped, re.IGNORECASE))


def _strip_bullet(line: str) -> str:
    return re.sub(r"^\s*[•‣◦▪▸●○*\-‐‑‒–—―]+\s*", "", line).strip()


def _is_date_only(line: str) -> bool:
    """True if a line carries nothing but a date/duration (e.g. 'Jun 2023 -
    Present'). Such lines belong to the entry above them, not a new entry."""
    rest = _DATE_RANGE_RE.sub(" ", line)
    rest = re.sub(r"\((?:[^)]*)\)", " ", rest)  # drop parentheticals like "(In Progress)"
    rest = _BARE_YEAR_RE.sub(" ", rest)
    rest = rest.strip(" \t·|,-‐‑‒–—―")
    return rest == ""


def _is_entry_header(line: str) -> bool:
    """True if a line looks like the *start* of a new entry -- i.e. a title
    line such as "Senior Software Engineer · Capgemini  July 2021 - Dec 2025"
    or "ECG Classification · Coursework 2026". A header is a non-bullet,
    non-URL, non-date-only line that carries a distinguishing signal: an org
    separator, or a year/date. Plain attribute lines (a bare degree, a
    "GPA: 3.8" line) carry neither and stay attached to the entry above."""
    if not line.strip() or _is_bullet(line) or _is_url_only(line) or _is_date_only(line):
        return False
    has_separator = any(sep in line for sep in _ORG_SEPARATORS)
    has_date = bool(_DATE_RANGE_RE.search(line) or _BARE_YEAR_RE.search(line))
    return has_separator or has_date


def _split_entries(section_text: str) -> List[str]:
    """Split a section into one text block per entry.

    Blank lines are the most reliable separator, so if the section uses them
    that wins. Otherwise (common in PDFs, which collapse blank lines) split at
    every "entry header" line -- see _is_entry_header. If no header lines are
    found the whole section is returned as a single block."""
    by_blank = [b.strip() for b in re.split(r"\n\s*\n", section_text) if b.strip()]
    if len(by_blank) > 1:
        return by_blank

    lines = section_text.splitlines()
    header_indices = [i for i, line in enumerate(lines) if _is_entry_header(line)]
    if not header_indices:
        return [section_text.strip()] if section_text.strip() else []

    blocks: List[str] = []
    for idx, start in enumerate(header_indices):
        end = header_indices[idx + 1] if idx + 1 < len(header_indices) else len(lines)
        # Fold any stray lines before the very first header into that first entry.
        if idx == 0 and start > 0:
            start = 0
        block = "\n".join(lines[start:end]).strip()
        if block:
            blocks.append(block)
    return blocks


# A compact "Title, Company" / "Degree, Institution" header is short; a line
# past this many words is far more likely an ordinary sentence that merely
# happens to contain a comma (e.g. a paragraph-style bullet with no real
# title/company line at all), so ", " is not trusted as a separator past it.
_MAX_WORDS_FOR_COMMA_SPLIT = 10


def _split_header(header: str) -> Tuple[str, str]:
    """Split an entry header into (left, right) around its organisation
    separator, after stripping out the trailing date/duration. For
    "MSc Data Science · University of Exeter 2026 - 2027" this returns
    ("MSc Data Science", "University of Exeter"); when there's no separator
    the whole (date-stripped) header is returned as `left`."""
    cleaned = _DATE_RANGE_RE.sub(" ", header)
    cleaned = re.sub(r"\((?:[^)]*)\)", " ", cleaned)  # drop "(In Progress)" etc.
    cleaned = _BARE_YEAR_RE.sub(" ", cleaned)  # drop a trailing project year
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" \t·|,-‐‑‒–—―")

    for sep in (" · ", " | ", " — ", " – ", " - ", ", ", " at ", " @ "):
        if sep not in cleaned:
            continue
        if sep == ", " and len(cleaned.split()) > _MAX_WORDS_FOR_COMMA_SPLIT:
            continue
        left, right = cleaned.split(sep, 1)
        return left.strip(" \t·|,-‐‑‒–—―"), right.strip(" \t·|,-‐‑‒–—―")
    # Fall back to the bare separators without surrounding spaces.
    for sep in ("·", "|"):
        if sep in cleaned:
            left, right = cleaned.split(sep, 1)
            return left.strip(" \t·|,-‐‑‒–—―"), right.strip(" \t·|,-‐‑‒–—―")
    return cleaned.strip(), ""


def _split_top_level(text: str) -> List[str]:
    """Split on commas/semicolons that are NOT inside parentheses, so
    "Python (scikit-learn, pandas), Java" yields ["Python (scikit-learn,
    pandas)", "Java"] rather than splitting the parenthesised list."""
    parts: List[str] = []
    depth = 0
    current = ""
    for char in text:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        if char in ",;" and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    parts.append(current)
    return [p.strip().lstrip("-*•· ").strip() for p in parts if p.strip()]


# ---------------------------------------------------------------------------
# Education
# ---------------------------------------------------------------------------

# Full-word degree names (case-insensitive) ...
_DEGREE_WORD_RE = re.compile(
    r"\b(bachelor|master|associate|doctor(?:ate)?|ph\.?\s?d|mba|diploma"
    r"|m\.?sc|b\.?sc|msc|bsc|m\.?tech|b\.?tech|mtech|btech|m\.?eng|b\.?eng|meng|beng)\b",
    re.IGNORECASE,
)
# ... and short uppercase degree codes (BE, B.S, M.A) matched case-sensitively,
# so the ordinary lowercase English words "be"/"ms"/"ma" don't false-positive.
_DEGREE_CODE_RE = re.compile(r"\b(?:B|M)\.?(?:E|S|A|Tech|Sc)\b")
_GPA_RE = re.compile(r"(?:gpa|cgpa)[:\s-]*(\d{1,3}(?:\.\d{1,2})?)", re.IGNORECASE)


def _has_degree_kw(text: str) -> bool:
    return bool(_DEGREE_WORD_RE.search(text) or _DEGREE_CODE_RE.search(text))


def _info_lines(lines: List[str]) -> List[str]:
    """Non-bullet, non-URL lines within an entry block (the header plus any
    degree/organisation lines)."""
    return [ln for ln in lines if not _is_bullet(ln) and not _is_url_only(ln)]


def parse_education(section_text: str) -> List[EducationEntry]:
    entries: List[EducationEntry] = []
    for block in _split_entries(section_text):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue

        entry = EducationEntry()
        entry.start_date, entry.end_date, entry.is_current = find_date_range(block)
        if not entry.is_current and _INPROGRESS_RE.search(block):
            entry.is_current = True

        gpa_match = _GPA_RE.search(block)
        if gpa_match:
            entry.gpa = gpa_match.group(1)

        info = _info_lines(lines)
        header = info[0] if info else lines[0]
        left, right = _split_header(header)

        if right:
            # Single-line header ("Degree · Institution"): the side carrying a
            # degree keyword is the degree, the other is the institution.
            later_degree = next((ln for ln in info[1:] if _has_degree_kw(ln)), None)
            if _has_degree_kw(right) and not _has_degree_kw(left):
                entry.degree, entry.institution = right, left
            elif _has_degree_kw(left) and not _has_degree_kw(right):
                entry.degree, entry.institution = left, right
            elif later_degree:
                # Neither side of a weak ", "-based split names a degree, but
                # a later line does (e.g. its own "Bachelor of Engineering in
                # ..." line) -- trust that over guessing from the split, and
                # assume the split's left side is the institution.
                entry.institution, entry.degree = left, later_degree
            else:
                entry.degree, entry.institution = left, right
        else:
            # No separator: header is one field; the other may be a nearby line.
            if _has_degree_kw(left):
                entry.degree = left
            else:
                entry.institution = left
            for other in info[1:]:
                if _has_degree_kw(other) and not entry.degree:
                    entry.degree = other
                elif not entry.institution and other != entry.degree:
                    entry.institution = other

        entry.achievements = [_strip_bullet(ln) for ln in lines if _is_bullet(ln)]
        entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# Experience
# ---------------------------------------------------------------------------


def parse_experience(section_text: str) -> List[ExperienceEntry]:
    entries: List[ExperienceEntry] = []
    for block in _split_entries(section_text):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue

        entry = ExperienceEntry()
        entry.start_date, entry.end_date, entry.is_current = find_date_range(block)

        info = _info_lines(lines)
        header = info[0] if info else lines[0]
        entry.job_title, entry.company = _split_header(header)

        bullets = [_strip_bullet(ln) for ln in lines if _is_bullet(ln)]
        if not bullets:
            # No explicit bullet glyphs -- common when a PDF's wrapped
            # sentences lose their bullet marker in extraction (it renders as
            # a vector glyph pypdf can't map back to a character). Fall back
            # to every remaining descriptive line so the description isn't
            # silently dropped.
            bullets = [ln for ln in lines if ln != header and not _is_url_only(ln) and not _is_date_only(ln)]
        entry.bullet_points = bullets
        entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

_TECH_LINE_RE = re.compile(r"technolog(?:y|ies)\s*(?:used)?\s*[:\-]\s*(.+)", re.IGNORECASE)


def parse_projects(section_text: str) -> List[ProjectEntry]:
    entries: List[ProjectEntry] = []
    for block in _split_entries(section_text):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue

        entry = ProjectEntry()
        info = _info_lines(lines)
        header = info[0] if info else lines[0]
        name, descriptor = _split_header(header)
        entry.name = name
        if descriptor:
            entry.description = descriptor

        url_line = next((ln for ln in lines if _is_url_only(ln)), None)
        if url_line:
            entry.url = url_line.strip()

        tech_line = next((ln for ln in lines if _TECH_LINE_RE.search(ln)), None)
        if tech_line:
            match = _TECH_LINE_RE.search(tech_line)
            entry.technologies = [t.strip() for t in _split_top_level(match.group(1))]

        bullets = [_strip_bullet(ln) for ln in lines if _is_bullet(ln)]
        if not bullets:
            # See parse_experience: no bullet glyphs survived extraction, so
            # fall back to every remaining descriptive line instead of
            # dropping the project's write-up.
            skip = {header, url_line, tech_line}
            bullets = [ln for ln in lines if ln not in skip and not _is_date_only(ln)]
        entry.bullet_points = bullets
        entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


def parse_skills(section_text: str) -> List[SkillCategory]:
    lines = [line.strip() for line in section_text.splitlines() if line.strip()]
    categories: List[SkillCategory] = []

    for line in lines:
        if ":" in line and len(line.split(":", 1)[0]) <= 30:
            label, rest = line.split(":", 1)
            skills = _split_top_level(rest)
            if skills:
                categories.append(SkillCategory(category_name=label.strip(), skills=skills))

    if not categories:
        all_skills: List[str] = []
        for line in lines:
            all_skills.extend(_split_top_level(line))
        if all_skills:
            categories.append(SkillCategory(category_name="Skills", skills=all_skills))

    return categories


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def parse_resume(file_bytes: bytes, filename: str) -> ResumeData:
    """Parse an uploaded resume file into a ResumeData object.

    This is best-effort: accuracy depends heavily on how the original
    resume was formatted. Treat the result as *pending* data for the user
    to review on each page -- not as already-confirmed content.
    """
    text = extract_text(file_bytes, filename)
    preamble, known_sections, extra_sections = split_into_sections(text)

    resume = ResumeData()
    resume.personal_info = parse_personal_info(preamble)

    if "education" in known_sections:
        resume.education = parse_education(known_sections["education"])
    if "experience" in known_sections:
        resume.experience = parse_experience(known_sections["experience"])
    if "projects" in known_sections:
        resume.projects = parse_projects(known_sections["projects"])
    if "skills" in known_sections:
        resume.skills = parse_skills(known_sections["skills"])
    if "summary" in known_sections:
        # Collapse the wrapped lines of the summary into a single paragraph.
        summary_lines = [ln.strip() for ln in known_sections["summary"].splitlines() if ln.strip()]
        resume.personal_info.professional_summary = " ".join(summary_lines)

    resume.extra_sections = [
        ExtraSection(heading=heading, content=content) for heading, content in extra_sections.items()
    ]

    return resume
