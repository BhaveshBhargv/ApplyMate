"""ATS analyzer -- semantic resume/JD matching (Phase: embeddings rebuild).

The old analyzer scored by keyword overlap: a JD term counted only if the exact
string appeared in the resume. That misses "React" vs "ReactJS", "ML" vs
"machine learning", "built pipelines" vs "data engineering". This version scores
by *meaning* using sentence embeddings, and reports a real breakdown instead of
a single number:

  * Overall match %  -- a calibrated blend of the three sections below.
  * Skills           -- which JD skills are covered by the resume, and which are
                        missing (semantic match, so near-synonyms count).
  * Experience match -- how well the resume's experience bullets cover the JD's
                        responsibilities, with the best-supporting bullet shown.
  * Education match  -- whether the resume meets the JD's stated education need
                        (or "not specified" when the JD asks for none).

It still never rewrites the resume or invents anything -- "missing" skills are
shown as honest gaps, never as something to fabricate. Skill *candidates* are
pulled from the JD with the same curated gazetteer + proper-noun detection as
before; embeddings decide the match, not the raw string.

Degrades gracefully: with no embeddings key, `analyze` falls back to the old
lexical matching and flags `semantic=False`, so the page keeps working -- it
just isn't synonym-aware until a (free) key is added.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from models.resume_data import ResumeData
from utils import embeddings

# ---------------------------------------------------------------------------
# Skills gazetteer -- multi-word and single-word terms we recognise as real,
# matchable keywords. Used only to pull *candidate* skills out of the JD; the
# resume-vs-JD match decision is made by embeddings, not by this list.
# ---------------------------------------------------------------------------

_GAZETTEER = {
    # Languages
    "python", "java", "javascript", "typescript", "c++", "c#", "sql", "r", "scala",
    "go", "golang", "ruby", "php", "swift", "kotlin", "html", "html5", "css", "bash",
    # Data / ML / AI
    "machine learning", "deep learning", "artificial intelligence", "ai", "ml",
    "natural language processing", "nlp", "computer vision", "neural network",
    "neural networks", "data science", "data analysis", "data analytics",
    "statistical modelling", "statistical modeling", "feature engineering",
    "data visualisation", "data visualization", "data pipelines", "data pipeline",
    "model deployment", "predictive modelling", "big data", "etl", "data engineering",
    "generative ai", "llm", "large language models", "prompt engineering",
    # Libraries / frameworks
    "scikit-learn", "sklearn", "tensorflow", "pytorch", "keras", "pandas", "numpy",
    "matplotlib", "spark", "hadoop", "airflow", "spring boot", "spring", "django",
    "flask", "fastapi", "react", "node.js", "angular",
    # Cloud / tools / platforms
    "azure", "aws", "gcp", "google cloud", "docker", "kubernetes", "git", "linux",
    "jupyter", "jupyter notebook", "postman", "tableau", "power bi", "databricks",
    "snowflake", "mysql", "postgresql", "oracle", "mongodb", "redis", "kafka",
    # Certifications / frameworks named in JDs
    "comptia", "comptia data+", "azure ai fundamentals", "aws certified",
    "azure fundamentals", "data+", "security+",
    # Soft skills / ways of working
    "communication", "collaboration", "teamwork", "problem-solving", "problem solving",
    "analytical", "stakeholder", "leadership", "agile", "scrum", "stakeholder management",
    "cross-functional",
}

# Display forms so acronyms/products render nicely (e.g. "AI" not "ai").
_DISPLAY_OVERRIDES = {
    "ai": "AI", "ml": "ML", "nlp": "NLP", "sql": "SQL", "aws": "AWS", "gcp": "GCP",
    "html": "HTML", "html5": "HTML5", "css": "CSS", "etl": "ETL", "llm": "LLM",
    "comptia": "CompTIA", "comptia data+": "CompTIA Data+", "data+": "Data+",
    "azure ai fundamentals": "Azure AI Fundamentals", "power bi": "Power BI",
    "node.js": "Node.js", "scikit-learn": "scikit-learn", "numpy": "NumPy",
    "aws certified": "AWS Certified", "security+": "Security+",
}

# JD boilerplate that looks keyword-ish but isn't a skill.
_JD_NOISE = {
    "role", "organisation", "organization", "company", "team", "looking",
    "opportunity", "apply", "candidate", "experience", "skills", "ability",
    "environment", "requirements", "offer", "salary", "recruitment", "support",
    "position", "access", "industry", "eligible", "required", "considered",
    "development", "professional", "career", "well", "established", "known",
    "real", "world", "modern", "specific", "technical", "power", "diverse",
    "structured", "designed", "completing", "demonstrate", "understanding",
    "complex", "mindset", "capable", "users", "developers", "emerging",
    "passion", "interest", "gaining", "commit", "focuses", "applying", "solve",
    "build", "deploy", "studying", "refining", "utilising", "utilizing",
    "including", "work", "working", "vetting", "badges", "digital", "permanent",
    "comprehensive", "recognised", "recognized", "right", "via",
}

# A small stop set for the lexical fallback and proper-noun filtering. Kept
# local so this module no longer needs scikit-learn.
_BASIC_STOP = {
    "a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "as", "at", "by", "from", "is", "are", "be", "been", "being", "this", "that",
    "these", "those", "it", "its", "we", "you", "your", "our", "will", "have",
    "has", "had", "not", "can", "may", "must", "should", "would", "about",
}
_STOP = _BASIC_STOP | _JD_NOISE

_ACRONYM_RE = re.compile(r"^(?:[A-Z]{2,5}|[A-Za-z][A-Za-z0-9.]*[+#]+)$")
_PROPER_DENYLIST = {"uk", "us", "usa", "eu", "id", "hr", "cv", "pdf", "faq", "ceo", "cto"}

_EDU_HINTS = (
    "degree", "bachelor", "bachelor's", "master", "master's", "phd", "doctorate",
    "bsc", "msc", "ba ", "ma ", "b.sc", "m.sc", "undergraduate", "postgraduate",
    "graduate", "diploma", "hnd", "qualification", "educated to",
)

# --- Tunable thresholds (calibrated for text-embedding-3-small) ---------------
# A JD skill counts as "covered" when its closest resume skill is at least this
# similar. ReactJS~React and ML~machine learning clear it; unrelated skills don't.
_SKILL_MATCH = 0.58
# Cosine calibration for section sub-scores: map a raw cosine band onto 0-100 so
# the numbers read naturally (relevant text pairs sit ~0.35-0.80 with this model).
_CAL_LO = 0.28
_CAL_HI = 0.82
# Section weights for the overall score. Education is dropped and the rest
# renormalised when the JD states no education requirement.
_W_SKILLS = 0.50
_W_EXPERIENCE = 0.35
_W_EDUCATION = 0.15


# --- Result types --------------------------------------------------------------

@dataclass
class SkillMatch:
    """A JD skill and the resume skill that best covers it."""
    jd_skill: str
    resume_skill: str
    similarity: float  # cosine 0-1


@dataclass
class RequirementMatch:
    """A JD responsibility/requirement line and the resume bullet best supporting it."""
    requirement: str
    best_bullet: str
    similarity: float  # cosine 0-1


@dataclass
class SectionScore:
    """A 0-100 sub-score for one section, plus whether the JD asked for it."""
    score: int
    specified: bool
    detail: str = ""


@dataclass
class MatchReport:
    overall: int
    skills_matched: List[SkillMatch] = field(default_factory=list)
    skills_missing: List[str] = field(default_factory=list)
    experience: SectionScore = field(default_factory=lambda: SectionScore(0, False))
    education: SectionScore = field(default_factory=lambda: SectionScore(0, False))
    # JD requirement -> best supporting resume bullet, for the evidence view and
    # the AI Writer. Sorted by similarity descending.
    requirement_matches: List[RequirementMatch] = field(default_factory=list)
    semantic: bool = True  # False when the lexical fallback was used (no key)

    @property
    def skills_total(self) -> int:
        return len(self.skills_matched) + len(self.skills_missing)


# --- JD / resume segmentation --------------------------------------------------

def _display(keyword: str) -> str:
    if keyword in _DISPLAY_OVERRIDES:
        return _DISPLAY_OVERRIDES[keyword]
    return keyword if any(c.isupper() for c in keyword) else keyword.title()


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def extract_keywords(jd_text: str, max_keywords: int = 24) -> List[str]:
    """Pull candidate skill phrases out of a job description.

    Two sources: (1) curated gazetteer terms present in the JD, and (2)
    capitalised proper nouns / acronyms appearing mid-sentence (named
    technologies the gazetteer doesn't list). Deduplicated, capped.
    """
    normalized = _normalize(jd_text)
    ordered: "OrderedDict[str, str]" = OrderedDict()

    def add(display: str) -> None:
        ordered.setdefault(display.lower(), display)

    for term in sorted(_GAZETTEER, key=len, reverse=True):
        pattern = r"(?<![\w+#])" + re.escape(term) + r"(?![\w+#])"
        if re.search(pattern, normalized):
            add(_display(term))

    for phrase in _proper_noun_terms(jd_text):
        add(phrase)
        if len(ordered) >= max_keywords:
            break

    return list(ordered.values())[:max_keywords]


def _proper_noun_terms(jd_text: str) -> List[str]:
    found: "OrderedDict[str, None]" = OrderedDict()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]*", jd_text):
        clean = token.strip(".-")
        if _ACRONYM_RE.match(clean) and clean.lower() not in _PROPER_DENYLIST and clean.lower() not in _STOP:
            found.setdefault(clean, None)
    return list(found.keys())


def _split_requirements(jd_text: str, max_items: int = 20) -> List[str]:
    """Break a JD into responsibility/requirement lines worth matching.

    Handles both bulleted JDs (split on newlines) and prose (split on sentence
    punctuation). Keeps lines with enough substance, drops short boilerplate.
    """
    # Prefer line structure when the JD is bulleted; otherwise fall back to
    # sentence splitting on the whole text.
    raw_lines = [ln.strip(" \t-*•–—") for ln in jd_text.splitlines()]
    lines = [ln for ln in raw_lines if ln]
    if len(lines) < 4:  # not really bulleted -- split into sentences instead
        lines = re.split(r"(?<=[.!?])\s+", " ".join(lines))

    reqs: List[str] = []
    seen = set()
    for ln in lines:
        ln = ln.strip()
        words = ln.split()
        if len(words) < 4 or len(ln) > 300:
            continue
        low = ln.lower()
        if low in seen:
            continue
        seen.add(low)
        reqs.append(ln)
        if len(reqs) >= max_items:
            break
    return reqs


def _education_requirement(jd_text: str) -> str:
    """Return the JD's education requirement text, or '' if none is stated."""
    fragments: List[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n", jd_text):
        low = sentence.lower()
        if any(h in low for h in _EDU_HINTS):
            frag = sentence.strip(" \t-*•–—")
            if frag:
                fragments.append(frag)
    return " ".join(fragments[:3])


def _resume_experience_units(resume: ResumeData) -> List[str]:
    """Experience bullets to match JD responsibilities against.

    Falls back to project bullets/descriptions when there is no work
    experience, so a project-heavy (e.g. student) resume isn't scored zero.
    """
    units = [b.strip() for exp in resume.experience for b in exp.bullet_points if b.strip()]
    if not units:
        for proj in resume.projects:
            if proj.description.strip():
                units.append(proj.description.strip())
            units += [b.strip() for b in proj.bullet_points if b.strip()]
    return units


def _resume_education_units(resume: ResumeData) -> List[str]:
    units: List[str] = []
    for edu in resume.education:
        parts = [edu.degree, edu.field_of_study, edu.institution, *edu.achievements]
        text = ", ".join(p.strip() for p in parts if p.strip())
        if text:
            units.append(text)
    return units


# --- Scoring helpers -----------------------------------------------------------

def _calibrate(cosine: float) -> int:
    """Map a raw cosine onto a readable 0-100 sub-score."""
    frac = (cosine - _CAL_LO) / (_CAL_HI - _CAL_LO)
    return int(round(max(0.0, min(1.0, frac)) * 100))


def _blend_overall(
    skills_pct: Optional[int], exp: SectionScore, edu: SectionScore
) -> int:
    """Weighted blend of the three sections into one headline number."""
    parts: List[Tuple[float, int]] = []
    if skills_pct is not None:
        parts.append((_W_SKILLS, skills_pct))
    if exp.specified:
        parts.append((_W_EXPERIENCE, exp.score))
    if edu.specified:
        parts.append((_W_EDUCATION, edu.score))
    if not parts:
        return 0
    total_w = sum(w for w, _ in parts)
    return int(round(sum(w * s for w, s in parts) / total_w))


# --- Public entry point --------------------------------------------------------

def analyze(resume: ResumeData, jd_text: str, *, api_key: Optional[str] = None) -> MatchReport:
    """Compare a resume against a job description and report a full breakdown.

    Uses embeddings when a key is available (semantic match), otherwise a
    lexical fallback (flagged `semantic=False`). `api_key` may be passed
    explicitly so this is safe to call off the main thread.
    """
    jd_text = jd_text or ""
    key = api_key or (embeddings._api_key() if embeddings.is_configured() else None)
    if not key:
        return _analyze_lexical(resume, jd_text)
    try:
        return _analyze_semantic(resume, jd_text, key)
    except embeddings.EmbeddingError:
        # Never fail the page on an embedding hiccup -- fall back and flag it.
        return _analyze_lexical(resume, jd_text)


def _analyze_semantic(resume: ResumeData, jd_text: str, key: str) -> MatchReport:
    jd_skills = extract_keywords(jd_text)
    jd_reqs = _split_requirements(jd_text)
    jd_edu = _education_requirement(jd_text)

    resume_skills = resume.all_skills_flat()
    exp_units = _resume_experience_units(resume)
    edu_units = _resume_education_units(resume)

    # Embed every group (cached + batched inside embed()).
    jd_skill_vecs = embeddings.embed(jd_skills, api_key=key) if jd_skills else []
    resume_skill_vecs = embeddings.embed(resume_skills, api_key=key) if resume_skills else []
    jd_req_vecs = embeddings.embed(jd_reqs, api_key=key) if jd_reqs else []
    exp_vecs = embeddings.embed(exp_units, api_key=key) if exp_units else []
    edu_unit_vecs = embeddings.embed(edu_units, api_key=key) if edu_units else []
    jd_edu_vec = embeddings.embed_one(jd_edu, api_key=key) if jd_edu else []

    # --- Skills: matched vs missing (semantic) ---
    matched: List[SkillMatch] = []
    missing: List[str] = []
    for skill, svec in zip(jd_skills, jd_skill_vecs):
        idx, score = embeddings.best_match(svec, resume_skill_vecs) if resume_skill_vecs else (-1, 0.0)
        if idx >= 0 and score >= _SKILL_MATCH:
            matched.append(SkillMatch(skill, resume_skills[idx], score))
        else:
            missing.append(skill)
    skills_pct = int(round(len(matched) / len(jd_skills) * 100)) if jd_skills else None

    # --- Experience: JD responsibilities vs resume bullets ---
    req_matches: List[RequirementMatch] = []
    for req, rvec in zip(jd_reqs, jd_req_vecs):
        idx, score = embeddings.best_match(rvec, exp_vecs) if exp_vecs else (-1, 0.0)
        best = exp_units[idx] if idx >= 0 else ""
        req_matches.append(RequirementMatch(req, best, score))
    req_matches.sort(key=lambda m: m.similarity, reverse=True)
    if jd_reqs:
        avg_cos = sum(m.similarity for m in req_matches) / len(req_matches)
        exp_detail = (
            f"{len(exp_units)} experience bullet(s) checked against "
            f"{len(jd_reqs)} JD requirement(s)."
            if exp_units else "No experience bullets in the resume yet."
        )
        experience = SectionScore(_calibrate(avg_cos), specified=True, detail=exp_detail)
    else:
        experience = SectionScore(0, specified=False, detail="JD lists no explicit responsibilities.")

    # --- Education ---
    if jd_edu:
        idx, score = embeddings.best_match(jd_edu_vec, edu_unit_vecs) if edu_unit_vecs else (-1, 0.0)
        edu_score = _calibrate(score)
        met = idx >= 0 and score >= _SKILL_MATCH
        detail = (
            f"Resume education {'meets' if met else 'may not meet'} the JD requirement."
            if edu_units else "JD asks for a qualification; none listed on the resume."
        )
        education = SectionScore(edu_score, specified=True, detail=detail)
    else:
        education = SectionScore(0, specified=False, detail="JD states no education requirement.")

    overall = _blend_overall(skills_pct, experience, education)
    return MatchReport(
        overall=overall,
        skills_matched=matched,
        skills_missing=missing,
        experience=experience,
        education=education,
        requirement_matches=req_matches,
        semantic=True,
    )


# --- Lexical fallback (no key): the previous exact-match behaviour -------------

def _tokenize(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9+#.]+", text.lower()) if w not in _STOP and len(w) > 1}


def _keyword_in_resume(keyword: str, resume_normalized: str) -> bool:
    pattern = r"(?<![\w+#])" + re.escape(keyword.lower()) + r"(?![\w+#])"
    return bool(re.search(pattern, resume_normalized))


def _analyze_lexical(resume: ResumeData, jd_text: str) -> MatchReport:
    """Exact/substring matching used when embeddings are unavailable."""
    resume_text = resume.searchable_text()
    resume_norm = _normalize(resume_text)
    resume_tokens = _tokenize(resume_text)

    # Skills: exact phrase match.
    matched: List[SkillMatch] = []
    missing: List[str] = []
    for skill in extract_keywords(jd_text):
        if _keyword_in_resume(skill, resume_norm):
            matched.append(SkillMatch(skill, skill, 1.0))
        else:
            missing.append(skill)
    skills_pct = int(round(len(matched) / (len(matched) + len(missing)) * 100)) if (matched or missing) else None

    # Experience: token overlap between each requirement and the resume.
    reqs = _split_requirements(jd_text)
    exp_units = _resume_experience_units(resume)
    req_matches: List[RequirementMatch] = []
    for req in reqs:
        req_tokens = _tokenize(req)
        overlap = len(req_tokens & resume_tokens) / len(req_tokens) if req_tokens else 0.0
        # Cheap "best bullet": the exp unit sharing the most tokens with the req.
        best_bullet, best_share = "", 0.0
        for unit in exp_units:
            share = len(req_tokens & _tokenize(unit)) / len(req_tokens) if req_tokens else 0.0
            if share > best_share:
                best_share, best_bullet = share, unit
        req_matches.append(RequirementMatch(req, best_bullet, overlap))
    req_matches.sort(key=lambda m: m.similarity, reverse=True)
    if reqs:
        exp_pct = int(round(sum(m.similarity for m in req_matches) / len(req_matches) * 100))
        experience = SectionScore(exp_pct, specified=True, detail="Keyword overlap (add a free embeddings key for semantic matching).")
    else:
        experience = SectionScore(0, specified=False, detail="JD lists no explicit responsibilities.")

    # Education: token overlap of JD education line vs resume education.
    jd_edu = _education_requirement(jd_text)
    if jd_edu:
        edu_tokens = _tokenize(jd_edu)
        edu_text = " ".join(_resume_education_units(resume))
        overlap = len(edu_tokens & _tokenize(edu_text)) / len(edu_tokens) if edu_tokens else 0.0
        education = SectionScore(int(round(overlap * 100)), specified=True,
                                 detail="Keyword overlap (add a free embeddings key for semantic matching).")
    else:
        education = SectionScore(0, specified=False, detail="JD states no education requirement.")

    overall = _blend_overall(skills_pct, experience, education)
    return MatchReport(
        overall=overall,
        skills_matched=matched,
        skills_missing=missing,
        experience=experience,
        education=education,
        requirement_matches=req_matches,
        semantic=False,
    )
