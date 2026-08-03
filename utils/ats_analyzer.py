"""ATS (Applicant Tracking System) analyzer for Phase 3.

Given a job description and the user's resume text, this module:
  1. Extracts the keywords a recruiter / ATS would key on from the JD,
  2. Checks which of them actually appear anywhere in the resume,
  3. Reports an overall match score, a TF-IDF text-similarity score, and the
     matched vs missing keyword lists.

It is intentionally rule-based (a curated skills gazetteer + proper-noun and
n-gram detection) with scikit-learn's TF-IDF for the similarity score, rather
than an LLM. Its job is to surface *gaps* honestly -- it never rewrites the
resume or suggests claiming a skill the user doesn't have. "Missing" keywords
are shown as information, not as something to fabricate.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import List, Optional

from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# ---------------------------------------------------------------------------
# Skills gazetteer -- multi-word and single-word terms we recognise as real,
# matchable keywords. Kept lowercase; display casing is restored separately.
# This is not meant to be exhaustive; proper-noun detection (below) catches
# named technologies that aren't listed here.
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

# JD boilerplate that looks keyword-ish but isn't a skill. Supplements
# scikit-learn's English stop words.
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

_STOP = set(ENGLISH_STOP_WORDS) | _JD_NOISE

# Acronyms (AI, AWS, SQL) and tech-punctuation tokens (C++, C#, Data+, .NET) --
# the two proper-noun shapes the gazetteer might miss. Named products written in
# ordinary title case (Azure, CompTIA, Python) are handled by the gazetteer, so
# we deliberately do NOT try to detect those here, which keeps out the ordinary
# capitalised words that open bullet points ("Developing", "Strong", "Access").
_ACRONYM_RE = re.compile(r"^(?:[A-Z]{2,5}|[A-Za-z][A-Za-z0-9.]*[+#]+)$")
# Non-skill acronyms to ignore.
_PROPER_DENYLIST = {"uk", "us", "usa", "eu", "id", "hr", "cv", "pdf", "faq", "ceo", "cto"}


@dataclass
class ATSResult:
    score: int  # keyword-coverage percentage, 0-100
    similarity: int  # TF-IDF cosine similarity percentage, 0-100
    matched: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)

    @property
    def total_keywords(self) -> int:
        return len(self.matched) + len(self.missing)


def _display(keyword: str) -> str:
    """Human-friendly casing for a lowercased keyword."""
    if keyword in _DISPLAY_OVERRIDES:
        return _DISPLAY_OVERRIDES[keyword]
    return keyword if any(c.isupper() for c in keyword) else keyword.title()


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def extract_keywords(jd_text: str, max_keywords: int = 20) -> List[str]:
    """Pull the meaningful, matchable keywords out of a job description.

    Two sources, in descending confidence: (1) gazetteer terms found in the
    JD (skills, tools, certs, soft skills), and (2) capitalised proper nouns
    / acronyms that appear *mid-sentence* -- named technologies like "Azure",
    "CompTIA", "Python" -- which catches real skills the gazetteer doesn't
    list while ignoring ordinary words capitalised at the start of a bullet.
    Results are deduplicated (case-insensitively) and capped at max_keywords.
    """
    normalized = _normalize(jd_text)
    ordered: "OrderedDict[str, str]" = OrderedDict()  # lowercase key -> display value

    def add(display: str) -> None:
        ordered.setdefault(display.lower(), display)

    # (1) Gazetteer terms (longest first so "machine learning" wins over "learning").
    for term in sorted(_GAZETTEER, key=len, reverse=True):
        pattern = r"(?<![\w+#])" + re.escape(term) + r"(?![\w+#])"
        if re.search(pattern, normalized):
            add(_display(term))

    # (2) Capitalised proper-noun runs that appear mid-sentence.
    for phrase in _proper_noun_terms(jd_text):
        add(phrase)
        if len(ordered) >= max_keywords:
            break

    return list(ordered.values())[:max_keywords]


def _proper_noun_terms(jd_text: str) -> List[str]:
    """Acronyms (AI, AWS) and tech-punctuation tokens (C++, Data+) from the JD,
    minus a denylist of non-skill acronyms. See _ACRONYM_RE for why this is
    kept deliberately narrow."""
    found: "OrderedDict[str, None]" = OrderedDict()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]*", jd_text):
        clean = token.strip(".-")
        if _ACRONYM_RE.match(clean) and clean.lower() not in _PROPER_DENYLIST and clean.lower() not in _STOP:
            found.setdefault(clean, None)
    return list(found.keys())


def _keyword_in_resume(keyword: str, resume_normalized: str) -> bool:
    """Whole-word (phrase) match of a keyword against normalized resume text."""
    kw = keyword.lower()
    pattern = r"(?<![\w+#])" + re.escape(kw) + r"(?![\w+#])"
    return bool(re.search(pattern, resume_normalized))


def _tfidf_similarity(resume_text: str, jd_text: str) -> int:
    """Overall TF-IDF cosine similarity between the two documents, as a percentage."""
    try:
        vectorizer = TfidfVectorizer(stop_words="english")
        matrix = vectorizer.fit_transform([resume_text, jd_text])
    except ValueError:
        return 0
    similarity = cosine_similarity(matrix[0:1], matrix[1:2])[0][0]
    return int(round(similarity * 100))


def analyze(resume_text: str, jd_text: str) -> ATSResult:
    """Compare a resume against a job description and report the match."""
    keywords = extract_keywords(jd_text)
    resume_normalized = _normalize(resume_text)

    matched: List[str] = []
    missing: List[str] = []
    for keyword in keywords:
        if _keyword_in_resume(keyword, resume_normalized):
            matched.append(keyword)
        else:
            missing.append(keyword)

    score = int(round(len(matched) / len(keywords) * 100)) if keywords else 0
    similarity = _tfidf_similarity(resume_text, jd_text) if resume_text.strip() else 0

    return ATSResult(score=score, similarity=similarity, matched=matched, missing=missing)
