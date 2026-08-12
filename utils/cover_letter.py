"""Grounded, job-specific cover letters: retrieve evidence -> write -> verify.

Same shape as the tailoring agent in utils/agents.py -- the deterministic work
happens here, and exactly one LLM call sits in the middle:

    1. Retriever  -- ats_analyzer.analyze() scores the résumé against the job and
                     returns, per job requirement, the résumé bullet that best
                     supports it, plus which of the job's skills the résumé
                     genuinely covers and which it doesn't. The strongest of
                     those pairs are the letter's *only* subject matter.
    2. Writer     -- ai_assistant.write_cover_letter(): one call, seeing only the
                     retrieved evidence, the résumé's own facts, and an explicit
                     do-not-claim list built from the skills the résumé lacks.
    3. Verifier   -- _verify() re-reads the finished letter and flags every
                     skill-like term and every number in it that does not appear
                     in the résumé. A failing draft (unsupported claim, or a word
                     count outside the target band) is sent back once with those
                     complaints; the better of the two drafts wins, and anything
                     still flagged is surfaced to the user rather than hidden.

The résumé text is the authority: a claim counts as supported when it is present
in what the user actually wrote. Nothing here writes to the résumé, and the page
shows the letter in an editable box, so the user always reviews before download.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from models.resume_data import ResumeData
from utils import ai_assistant, ats_analyzer, embeddings, job_search
# Same evidence floors as the tailoring agent, so both features agree on what
# counts as a genuine match rather than a coincidental one.
from utils.agents import _MIN_EVIDENCE_LEXICAL, _MIN_EVIDENCE_SEMANTIC

# Target length (rule: ~250-350 words). The accept band is wider because
# "approximately" shouldn't trigger a retry over a handful of words.
MIN_WORDS = 250
MAX_WORDS = 350
_MIN_ACCEPT = 235
_MAX_ACCEPT = 375

# How many requirement/bullet pairs the letter is built from. More than this and
# the letter turns into a list; fewer and it has nothing concrete to stand on.
_MAX_HIGHLIGHTS = 5
_MAX_SAFE_SKILLS = 14

_FENCE_RE = re.compile(r"^\s*```[A-Za-z]*\s*$")
_SALUTATION_RE = re.compile(r"^(dear\b|to whom it may concern|hello\b|hi\b|good (morning|afternoon)\b)", re.I)
_PREAMBLE_RE = re.compile(
    r"^(sure|certainly|of course|absolutely|here(?:'s| is)|below is|i've written|"
    r"i have written|as requested|cover letter\b|\*\*cover letter)", re.I,
)
_TRAILER_RE = re.compile(
    r"^(note[s]?\s*[:\-]|word count|words\s*[:\-]|p\.?s\.?\s*[:\-]|disclaimer|"
    r"\[|\(note|-{3,}|={3,}|\*{3,})", re.I,
)
_SIGNOFF_RE = re.compile(r"^(sincerely|kind regards|best regards|regards|yours (sincerely|faithfully)|thank you)[,.]?$", re.I)
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?%?")
_TOKEN_RE = re.compile(r"[A-Za-z0-9+#.]+")

# Working-style words the gazetteer treats as skills, but which a letter uses as
# ordinary prose ("the analytical side of the work"). Flagging them fires on
# almost every letter and buries the flags that matter, so the verifier ignores
# them and concentrates on what letters actually fabricate: a named tool,
# platform, or certification the résumé never mentions, and invented figures.
# Named practices (agile, scrum) stay checked -- claiming those is claiming
# experience.
_SOFT_TERMS = {
    "communication", "collaboration", "teamwork", "problem-solving", "problem solving",
    "analytical", "stakeholder", "stakeholder management", "leadership", "cross-functional",
}


# --- Data contracts ------------------------------------------------------------

@dataclass
class Highlight:
    """One job requirement and the résumé bullet the retriever matched to it."""
    requirement: str
    bullet: str
    similarity: float  # 0-1 retrieval strength


@dataclass
class Flag:
    """A claim in the finished letter that the résumé doesn't back up."""
    text: str
    kind: str   # 'skill' | 'number'
    note: str


@dataclass
class CoverLetter:
    """A generated letter plus everything needed to judge and export it."""
    text: str
    company: str = ""
    role: str = ""
    highlights: List[Highlight] = field(default_factory=list)
    safe_skills: List[str] = field(default_factory=list)
    withheld_skills: List[str] = field(default_factory=list)
    word_count: int = 0
    flags: List[Flag] = field(default_factory=list)
    semantic: bool = True   # False when the lexical fallback was used (no key)
    revised: bool = False   # True when the verifier's second attempt was kept

    @property
    def in_range(self) -> bool:
        return _MIN_ACCEPT <= self.word_count <= _MAX_ACCEPT

    @property
    def verified(self) -> bool:
        """True when nothing in the letter contradicts the résumé."""
        return not self.flags


# --- Evidence (deterministic) --------------------------------------------------

@dataclass
class _Evidence:
    highlights: List[Highlight]
    safe_skills: List[str]
    withheld_skills: List[str]
    semantic: bool


def build_evidence(resume: ResumeData, jd_text: str, *, api_key: Optional[str] = None) -> _Evidence:
    """Score the résumé against the job and keep only the well-supported matches."""
    report = ats_analyzer.analyze(resume, jd_text, api_key=api_key)
    floor = _MIN_EVIDENCE_SEMANTIC if report.semantic else _MIN_EVIDENCE_LEXICAL

    highlights = [
        Highlight(m.requirement, m.best_bullet, m.similarity)
        for m in report.requirement_matches
        if m.best_bullet and m.similarity >= floor
    ][:_MAX_HIGHLIGHTS]

    # Name skills in the résumé's own wording -- that's what the candidate can
    # honestly claim -- deduplicated, order preserved.
    safe: List[str] = []
    seen = set()
    for m in report.skills_matched:
        term = (m.resume_skill or "").strip()
        if term and term.lower() not in seen:
            safe.append(term)
            seen.add(term.lower())

    return _Evidence(
        highlights=highlights,
        safe_skills=safe[:_MAX_SAFE_SKILLS],
        withheld_skills=list(report.skills_missing),
        semantic=report.semantic,
    )


def _resume_facts(resume: ResumeData) -> str:
    """A compact, factual dump of the résumé -- the model's only source of claims.

    Richer than ai_assistant._resume_facts: a letter also draws on the summary,
    dates, and locations, so those are included (and they're what the verifier
    checks numbers against).
    """
    lines: List[str] = []
    pi = resume.personal_info
    if pi.full_name:
        lines.append(f"Name: {pi.full_name}")
    if pi.location:
        lines.append(f"Location: {pi.location}")
    if pi.professional_summary:
        lines.append(f"Summary: {pi.professional_summary}")

    for exp in resume.experience:
        header = " at ".join(p for p in [exp.job_title, exp.company] if p)
        dates = _date_range(exp.start_date, exp.end_date, exp.is_current)
        if header or dates:
            suffix = f" ({dates})" if dates else ""
            lines.append(f"Role: {header or 'Role'}{suffix}"
                         + (f", {exp.location}" if exp.location else ""))
        for bullet in exp.bullet_points:
            if bullet.strip():
                lines.append(f"  - {bullet.strip()}")

    for proj in resume.projects:
        if proj.name:
            tech = f" ({', '.join(proj.technologies)})" if proj.technologies else ""
            lines.append(f"Project: {proj.name}{tech}")
        if proj.description:
            lines.append(f"  {proj.description.strip()}")
        for bullet in proj.bullet_points:
            if bullet.strip():
                lines.append(f"  - {bullet.strip()}")

    for edu in resume.education:
        degree = ", ".join(p for p in [edu.degree, edu.field_of_study] if p)
        parts = [p for p in [degree, edu.institution] if p]
        dates = _date_range(edu.start_date, edu.end_date, edu.is_current)
        if parts:
            line = f"Education: {' — '.join(parts)}"
            if dates:
                line += f" ({dates})"
            if edu.gpa:
                line += f", GPA {edu.gpa}"
            lines.append(line)
        for achievement in edu.achievements:
            if achievement.strip():
                lines.append(f"  - {achievement.strip()}")

    skills = resume.all_skills_flat()
    if skills:
        lines.append(f"Skills: {', '.join(skills)}")

    for extra in resume.extra_sections:
        if extra.heading or extra.content:
            lines.append(f"{extra.heading or 'Additional'}: {extra.content.strip()}")

    return "\n".join(lines)


def _date_range(start: str, end: str, is_current: bool) -> str:
    end_text = "Present" if is_current else end
    if start and end_text:
        return f"{start} – {end_text}"
    return start or end_text or ""


# --- Cleaning -------------------------------------------------------------------

def _clean(text: str, candidate_name: str = "") -> str:
    """Reduce the model's response to the letter itself.

    The system prompt forbids preamble, letterheads, and trailing notes, but the
    rule is "output ONLY the letter", so it's enforced here rather than trusted:
    anything before the salutation is dropped, as are trailing notes and
    placeholder signature lines.
    """
    lines = [ln for ln in text.replace("\r\n", "\n").split("\n") if not _FENCE_RE.match(ln)]
    lines = [re.sub(r"^\s*#{1,6}\s*", "", ln).replace("**", "") for ln in lines]

    # Everything before the salutation is letterhead/date/preamble the letter
    # shouldn't carry (the PDF supplies the header).
    salutation_at = next(
        (i for i, ln in enumerate(lines[:14]) if _SALUTATION_RE.match(ln.strip())), None
    )
    if salutation_at is not None:
        lines = lines[salutation_at:]
    else:
        while lines and (not lines[0].strip() or _PREAMBLE_RE.match(lines[0].strip())):
            lines.pop(0)

    while lines and (not lines[-1].strip() or _TRAILER_RE.match(lines[-1].strip())):
        lines.pop()

    # Collapse runs of blank lines to a single paragraph break.
    cleaned: List[str] = []
    for line in lines:
        if not line.strip():
            if cleaned and not cleaned[-1]:
                continue
            cleaned.append("")
        else:
            cleaned.append(line.rstrip())

    body = _unwrap("\n".join(cleaned).strip())
    # A dropped "[Your Name]" placeholder can leave a dangling sign-off.
    if candidate_name.strip() and body:
        tail = body.split("\n")[-1].strip()
        if _SIGNOFF_RE.match(tail):
            body += f"\n{candidate_name.strip()}"
    return body


def _unwrap(text: str) -> str:
    """Normalise line structure: one line per paragraph, sign-off blocks intact.

    A model that hard-wraps its prose would otherwise render as ragged lines in
    the PDF. Long-line blocks are joined into a single paragraph; short-line
    blocks (a sign-off over the candidate's name) keep their breaks.
    """
    blocks: List[str] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        if len(lines) > 1 and sum(len(ln) for ln in lines) / len(lines) > 60:
            blocks.append(" ".join(lines))
        else:
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def word_count(text: str) -> int:
    """Whitespace-separated word count of the whole letter, as a reader would count."""
    return len(text.split())


def paragraphs(text: str) -> List[str]:
    """Split a letter into paragraph blocks; internal newlines are real breaks."""
    return [b.strip() for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]


# --- Verifier (deterministic) ---------------------------------------------------

def _resume_haystack(resume: ResumeData) -> str:
    """Everything the user wrote, as one string -- the authority for verification.

    ResumeData.searchable_text() omits dates and contact details, so those are
    appended: a letter mentioning a real year or city shouldn't be flagged.
    """
    parts = [resume.searchable_text()]
    pi = resume.personal_info
    parts += [pi.full_name, pi.location, pi.email, pi.phone]
    for exp in resume.experience:
        parts += [exp.start_date, exp.end_date, exp.location,
                  "Present" if exp.is_current else ""]
    for edu in resume.education:
        parts += [edu.start_date, edu.end_date, edu.gpa,
                  "Present" if edu.is_current else ""]
    for proj in resume.projects:
        parts.append(proj.url)
    return " ".join(p for p in parts if p)


def _allowed_tokens(company: str, role: str, candidate_name: str, location: str) -> set:
    """Words that belong to the addressee/candidate, not to a factual claim."""
    joined = " ".join([company, role, candidate_name, location])
    return {t.lower() for t in _TOKEN_RE.findall(joined)}


def _verify(
    letter: str,
    resume: ResumeData,
    *,
    company: str = "",
    role: str = "",
) -> List[Flag]:
    """Flag every skill-like term and number in the letter that the résumé lacks.

    Deliberately narrow and deterministic: it checks the two things a letter
    fabricates in practice -- a technology the candidate hasn't used, and a
    metric nobody stated -- against the résumé's own text. Company and role
    wording is exempt (naming the employer isn't a claim about the candidate).
    """
    haystack = _resume_haystack(resume)
    allowed = _allowed_tokens(company, role, resume.personal_info.full_name,
                              resume.personal_info.location)
    flags: List[Flag] = []
    seen = set()

    for term in ats_analyzer.extract_keywords(letter, max_keywords=40):
        key = term.lower()
        if key in seen or key in _SOFT_TERMS:
            continue
        tokens = {t.lower() for t in _TOKEN_RE.findall(term)}
        if tokens and tokens <= allowed:
            continue
        if job_search.skills_in_text([term], haystack):
            continue
        seen.add(key)
        flags.append(Flag(term, "skill", "not stated anywhere in your résumé"))

    for raw in _NUMBER_RE.findall(letter):
        key = raw.lower()
        if key in seen or key.rstrip("%") in allowed:
            continue
        number = raw.rstrip("%")
        pattern = r"(?<!\d)" + re.escape(number) + r"(?!\d)"
        if re.search(pattern, haystack):
            continue
        seen.add(key)
        flags.append(Flag(raw, "number", "no matching figure in your résumé"))

    return flags


def _corrections(flags: List[Flag], words: int) -> str:
    """Turn the verifier's findings into instructions for one more attempt."""
    notes: List[str] = []
    unsupported = [f.text for f in flags]
    if unsupported:
        notes.append(
            "- Remove every mention of the following -- the résumé does not support "
            f"them: {', '.join(unsupported)}. Do not replace them with different "
            "unsupported claims; use only what the résumé states."
        )
    if words < _MIN_ACCEPT:
        notes.append(f"- The draft was {words} words. Expand it to {MIN_WORDS}-{MAX_WORDS} "
                     "words using only the evidence already provided.")
    elif words > _MAX_ACCEPT:
        notes.append(f"- The draft was {words} words. Cut it to {MIN_WORDS}-{MAX_WORDS} "
                     "words by tightening the prose, not by dropping the evidence.")
    return "\n".join(notes)


def _penalty(flags: List[Flag], words: int) -> Tuple[int, int]:
    """Rank a draft: fewest unsupported claims first, then closest to the band."""
    if words < _MIN_ACCEPT:
        distance = _MIN_ACCEPT - words
    elif words > _MAX_ACCEPT:
        distance = words - _MAX_ACCEPT
    else:
        distance = 0
    return len(flags), distance


# --- Orchestration --------------------------------------------------------------

def generate(
    resume: ResumeData,
    jd_text: str,
    *,
    company: str = "",
    role: str = "",
    api_key: Optional[str] = None,
) -> CoverLetter:
    """Retrieve evidence, write the letter, verify it, and return the best draft.

    Raises ai_assistant.AIError if the résumé/job description is too thin to work
    from or the chat model is unavailable (the page shows the notice).
    """
    if not jd_text.strip():
        raise ai_assistant.AIError(
            "Paste the job description first -- the letter is written against that specific job."
        )
    facts = _resume_facts(resume)
    if not facts.strip():
        raise ai_assistant.AIError(
            "Your résumé is empty -- fill it in on the Dashboard first, then come back."
        )

    key = api_key or (embeddings._api_key() if embeddings.is_configured() else None)
    evidence = build_evidence(resume, jd_text, api_key=key)
    candidate_name = resume.personal_info.full_name.strip()

    def attempt(corrections: str = "") -> Tuple[str, List[Flag], int]:
        raw = ai_assistant.write_cover_letter(
            facts=facts,
            highlights=[(h.requirement, h.bullet) for h in evidence.highlights],
            covered_skills=evidence.safe_skills,
            forbidden_skills=evidence.withheld_skills,
            company=company,
            role=role,
            candidate_name=candidate_name,
            min_words=MIN_WORDS,
            max_words=MAX_WORDS,
            corrections=corrections,
        )
        text = _clean(raw, candidate_name)
        return text, _verify(text, resume, company=company, role=role), word_count(text)

    text, flags, words = attempt()
    revised = False

    # One corrective pass, kept only if it's genuinely better -- so a worse
    # second draft can never replace a good first one.
    if _penalty(flags, words) != (0, 0):
        try:
            retry_text, retry_flags, retry_words = attempt(_corrections(flags, words))
        except ai_assistant.AIError:
            retry_text = ""  # keep the first draft; the user still gets a letter
        if retry_text and _penalty(retry_flags, retry_words) < _penalty(flags, words):
            text, flags, words, revised = retry_text, retry_flags, retry_words, True

    return CoverLetter(
        text=text,
        company=company.strip(),
        role=role.strip(),
        highlights=evidence.highlights,
        safe_skills=evidence.safe_skills,
        withheld_skills=evidence.withheld_skills,
        word_count=words,
        flags=flags,
        semantic=evidence.semantic,
        revised=revised,
    )
