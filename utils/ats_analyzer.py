"""ATS analyzer -- how well does a résumé cover a job description?

How real ATS checkers (Jobscan, Resume Worded, ...) score, and what this does
the same way:

  * The job description is read for KEYWORDS -- hard skills (tools, methods,
    certifications, domain terms), soft skills and other repeated terms -- and the
    score is the share of them found anywhere in the résumé. Hard skills weigh
    most. A job title match and a stated education level add to it.
  * Matching is on word STEMS and known variants, not exact strings, so
    "supervising" matches "supervision", "JS" matches "JavaScript" and
    "CI/CD" matches "continuous integration". A keyword counts wherever it
    appears -- skills list, bullets, projects, summary -- not only in Skills.

What is different from before (and why scores were low): keywords used to come
from a tech-only word list plus capitalised acronyms, so a civil-engineering or
business JD produced a handful of odd terms; with embeddings on, a JD skill only
matched the résumé's *Skills list*, not the rest of the résumé; and the
"experience" score compared whole JD sentences to bullets word-for-word with no
stemming, so even a strong résumé scored ~30%.

Embeddings (optional, EMBEDDINGS_API_KEY) are now only a rescue pass: keywords
the text match missed are re-checked by meaning, and requirement lines get the
better of the lexical or semantic score. Nothing is ever invented -- "missing"
stays an honest gap.
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
    "autocad": "AutoCAD", "revit": "Revit", "staad pro": "STAAD Pro", "etabs": "ETABS", "bim": "BIM",
    "cad": "CAD", "sap": "SAP", "erp": "ERP", "crm": "CRM", "seo": "SEO", "plc": "PLC", "cnc": "CNC",
    "scada": "SCADA", "microsoft excel": "Microsoft Excel", "ms project": "MS Project",
    "powerpoint": "PowerPoint", "photoshop": "Photoshop", "solidworks": "SolidWorks", "matlab": "MATLAB",
    "ui design": "UI Design", "ux design": "UX Design", "rest api": "REST API", "devops": "DevOps",
    "jira": "Jira", "ansys": "ANSYS", "catia": "CATIA", "pcb design": "PCB Design",
    "health and safety": "Health and Safety", "ci/cd": "CI/CD", "ab testing": "A/B Testing",
    "ppc": "PPC", "ios": "iOS", "hvac": "HVAC", "ui ux": "UI/UX",
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

# --- Tunable thresholds --------------------------------------------------------
# A keyword the text match missed is rescued when an embedding of it is at least
# this close to some résumé line (text-embedding-3-small / Gemini scale).
_SEMANTIC_RESCUE = 0.62
# Cosine calibration for display of raw semantic similarity (legacy helper).
_CAL_LO = 0.28
_CAL_HI = 0.82
# A requirement line counts as fully covered once its best résumé bullet shares
# this fraction of its content words (the rest of a JD sentence is filler).
_REQ_FULL_COVERAGE = 0.5
# Keyword weights inside the keyword score: hard skills dominate (as in Jobscan).
_KIND_WEIGHT = {"hard": 1.0, "soft": 0.5, "keyword": 0.5}
# Section weights for the overall score; unspecified sections drop out and the
# rest are renormalised.
_W_KEYWORDS = 0.65
_W_EXPERIENCE = 0.15
_W_TITLE = 0.10
_W_EDUCATION = 0.10
_MAX_TERMS = 32


# --- Result types --------------------------------------------------------------

@dataclass
class SkillMatch:
    """A JD keyword and the résumé wording that covers it."""
    jd_skill: str
    resume_skill: str
    similarity: float  # 1.0 for a text match, cosine for a semantic rescue
    kind: str = "hard"  # hard | soft | keyword


@dataclass
class RequirementMatch:
    """A JD responsibility/requirement line and the résumé bullet best supporting it."""
    requirement: str
    best_bullet: str
    similarity: float  # raw strength: stem coverage (lexical) or cosine (semantic)
    score: float = 0.0  # 0-1 how well covered, for display


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
    title: SectionScore = field(default_factory=lambda: SectionScore(0, False))
    jd_title: str = ""
    keyword_pct: Optional[int] = None  # weighted share of JD keywords covered
    # JD requirement -> best supporting résumé bullet, for the evidence view and
    # the AI Writer. Sorted by strength descending.
    requirement_matches: List[RequirementMatch] = field(default_factory=list)
    semantic: bool = False  # True only when embeddings actually contributed
    missing_kinds: dict = field(default_factory=dict)  # missing keyword -> kind

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


# --- Text normalisation, stems and variants ------------------------------------
# Both the job description and the résumé go through the same pipeline, so any
# variant spelling lands on the same token: lowercase -> punctuation to spaces ->
# known aliases to one canonical form -> light stemming.

# First entry is the canonical form; the rest are variants that map onto it.
_ALIAS_GROUPS = [
    ("javascript", "js", "ecmascript", "es6"),
    ("typescript", "ts"),
    ("machine learning", "ml"),
    ("artificial intelligence", "ai"),
    ("natural language processing", "nlp"),
    ("large language model", "large language models", "llm", "llms"),
    ("nodejs", "node js", "node"),
    ("reactjs", "react js", "react"),
    ("vuejs", "vue js", "vue"),
    ("angularjs", "angular js", "angular"),
    ("postgresql", "postgres", "psql"),
    ("kubernetes", "k8s"),
    ("continuous integration", "ci cd", "cicd", "ci"),
    ("amazon web services", "aws"),
    ("google cloud platform", "gcp", "google cloud"),
    ("microsoft excel", "ms excel", "excel"),
    ("power bi", "powerbi"),
    ("autocad", "auto cad", "autodesk autocad"),
    ("revit", "autodesk revit", "auto desk revit"),
    ("cplusplus", "c plus plus"),
    ("csharp", "c sharp"),
    ("search engine optimization", "search engine optimisation", "seo"),
    ("user experience", "ux"),
    ("user interface", "ui"),
    ("human resources", "hr"),
    ("key performance indicator", "key performance indicators", "kpi", "kpis"),
    ("return on investment", "roi"),
    ("customer relationship management", "crm"),
    ("enterprise resource planning", "erp"),
    ("quality assurance", "qa"),
    ("building information modelling", "building information modeling", "bim"),
    ("computer aided design", "cad"),
    ("health and safety", "hse", "ehs"),
    ("problem solving", "problem solver"),
    ("object oriented programming", "oop"),
    ("api", "apis"),
    ("rest api", "restful api", "rest apis", "restful apis"),
    ("data analysis", "data analytics"),
    ("ab testing", "a b testing", "split testing"),
    ("pay per click", "ppc"),
    ("containerization", "containerisation", "containers", "container", "docker"),
    ("information technology", "it"),
]
_ALIAS_MAP = {}
for _group in _ALIAS_GROUPS:
    for _variant in _group[1:]:
        _ALIAS_MAP[_variant] = _group[0]
_ALIAS_RE = re.compile(
    r"(?<=\s)(" + "|".join(re.escape(v) for v in sorted(_ALIAS_MAP, key=len, reverse=True)) + r")(?=\s)"
)

_SPECIAL_TOKENS = (("c++", "cplusplus"), ("c#", "csharp"), (".net", "dotnet"),
                   ("node.js", "nodejs"), ("react.js", "reactjs"), ("vue.js", "vuejs"))

_SUFFIXES = ("ations", "ation", "ments", "ment", "ings", "ing", "ions", "ion", "ship", "ers",
             "er", "ors", "or", "ies", "es", "ed", "ly", "al", "s", "e")


def _canon_text(text: str) -> str:
    """Lowercase, flatten punctuation and map known variants to one form."""
    t = (text or "").lower()
    for src, dst in _SPECIAL_TOKENS:
        t = t.replace(src, f" {dst} ")
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = f" {t.strip()} "
    # Run twice so adjacent aliases ("js ml") that share a space both convert.
    for _ in range(2):
        t = _ALIAS_RE.sub(lambda m: _ALIAS_MAP[m.group(1)], t)
    return t.strip()


def _stem(word: str) -> str:
    """A deliberately light stemmer: enough that supervising/supervision/supervise
    agree, with a floor so short words are left alone."""
    if len(word) <= 3 or word.isdigit():
        return word
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]   # plurals, including short ones (apis -> api, tools -> tool)
    for _ in range(4):
        for suffix in _SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= 4:
                word = word[: -len(suffix)]
                break
        else:
            break
    return word


def _stems(text: str) -> List[str]:
    """Stems of the meaningful words in `text`, in order (stop words dropped)."""
    return [_stem(w) for w in _canon_text(text).split() if w not in _STOP and len(w) > 1]


def content_stems(text: str) -> set:
    """Set of stems for a piece of text -- used for coverage / retrieval overlap."""
    return set(_stems(text))


def _contains(stems: Sequence[str], term: Sequence[str], slack: int = 3) -> bool:
    """True if `term` (a tuple of stems) occurs in `stems`: as a run, or -- for a
    multi-word term -- all its words within a short window (any order)."""
    n = len(term)
    if n == 0 or not stems:
        return False
    if n == 1:
        return term[0] in stems
    want = set(term)
    if not want <= set(stems):
        return False
    width = n + slack
    for i in range(len(stems)):
        if stems[i] in want and want <= set(stems[i:i + width]):
            return True
    return False


# --- What a job description is asking for --------------------------------------

# Hard skills beyond the tech list above: tools, methods and domain terms from
# engineering, business, health, design, education, trades. Matched by stem, so
# this only needs the canonical wording.
_GAZETTEER_EXTRA = {
    "project management", "site supervision", "construction management", "structural analysis",
    "structural design", "autocad", "revit", "staad pro", "etabs", "primavera", "ms project",
    "quantity surveying", "estimating", "cost estimation", "budgeting", "procurement",
    "health and safety", "risk assessment", "quality control", "quality assurance",
    "contract management", "financial modelling", "financial analysis", "forecasting",
    "accounting", "bookkeeping", "microsoft excel", "powerpoint", "microsoft office", "sap", "erp",
    "crm", "salesforce", "search engine optimization", "content marketing", "social media",
    "copywriting", "photoshop", "illustrator", "figma", "ui design", "ux design", "wireframing",
    "prototyping", "user research", "customer service", "sales", "negotiation", "recruitment",
    "payroll", "training", "teaching", "lesson planning", "patient care", "nursing", "clinical",
    "first aid", "data entry", "scheduling", "supply chain", "logistics", "inventory management",
    "lean", "six sigma", "solidworks", "catia", "ansys", "matlab", "simulink", "plc", "scada",
    "embedded systems", "circuit design", "pcb design", "cnc", "rest api", "microservices",
    "devops", "continuous integration", "unit testing", "test automation", "selenium", "jira",
    "object oriented programming", "data structures", "algorithms", "building information modelling",
    "computer aided design", "construction", "civil engineering", "geotechnical", "surveying",
    "site management", "concrete", "steel", "drawings", "specifications", "tendering",
    "stakeholder management", "reporting", "documentation", "compliance", "auditing",
    "business analysis", "requirements gathering", "product management", "agile", "scrum",
}

_GAZETTEER_EXTRA |= {
    "email marketing", "content strategy", "content creation", "marketing automation", "paid media",
    "paid search", "ppc", "google analytics", "google ads", "meta ads", "linkedin ads", "social media marketing",
    "ab testing", "landing pages", "conversion optimization", "brand management", "market research",
    "campaign management", "mailchimp", "hubspot", "wordpress", "shopify", "looker studio", "trello", "asana",
    "zendesk", "quickbooks", "xero", "financial reporting", "budget management", "accounts payable",
    "accounts receivable", "reconciliation", "tax", "audit", "financial planning", "talent acquisition",
    "employee relations", "onboarding", "performance management", "customer success", "account management",
    "business development", "lead generation", "cold calling", "pipeline management", "cloud computing",
    "cybersecurity", "network security", "penetration testing", "incident response", "technical support",
    "troubleshooting", "system administration", "database administration", "data modelling", "api design",
    "mobile development", "ios", "android", "ui ux", "responsive design", "accessibility", "seo",
    "mechanical design", "electrical design", "hvac", "maintenance", "commissioning", "welding",
    "fabrication", "machining", "manufacturing", "production planning", "process improvement",
    "clinical trials", "medical records", "patient assessment", "medication administration", "safeguarding",
    "curriculum development", "classroom management", "student assessment", "policy development",
    "grant writing", "fundraising", "public speaking", "event planning", "vendor management",
}
_SOFT_SKILLS = {
    "communication", "teamwork", "collaboration", "leadership", "problem solving", "analytical",
    "time management", "adaptability", "attention to detail", "interpersonal", "critical thinking",
    "creativity", "initiative", "organisational", "organizational", "planning", "multitasking",
    "self motivated", "decision making", "presentation", "mentoring", "flexibility",
}

# Words that appear in nearly every job description and say nothing about fit.
_GENERIC = _JD_NOISE | {
    "will", "year", "years", "team", "teams", "also", "ensure", "provide", "providing", "need",
    "needs", "across", "within", "using", "used", "new", "help", "make", "making", "join",
    "benefits", "include", "includes", "related", "relevant", "excellent", "good", "great",
    "strong", "knowledge", "preferred", "plus", "must", "able", "day", "days", "time", "part",
    "full", "contract", "based", "location", "hours", "week", "weeks", "per", "etc", "more",
    "other", "such", "both", "each", "all", "any", "one", "two", "our", "their", "they", "who",
    "what", "when", "where", "how", "why", "about", "than", "then", "them", "very", "just",
    "like", "key", "high", "level", "senior", "junior", "lead", "manager", "business",
    "customers", "customer", "clients", "client", "services", "service", "people", "projects",
    "project", "applications", "application", "tasks", "task", "activities", "responsibilities",
    "responsibility", "duties", "needed", "looking", "seeking", "successful", "responsible",
    "ideal", "ideally", "candidates", "equal", "opportunities", "employer", "benefit", "package",
    "competitive", "bonus", "pension", "holiday", "remote", "hybrid", "office", "onsite",
}

_AMBIGUOUS = {"go", "r", "c", "it", "ai", "ml", "data+", "security+"}

# Real in-demand nouns, but too generic to count as a named skill.
_WEAK_HARD = {"specifications", "compliance", "reporting", "documentation", "drawings", "steel",
              "concrete", "training", "sales", "construction", "logistics", "stakeholder"}
_SOFT_SKILLS |= {"collaboration", "teamwork", "analytical", "leadership", "problem-solving", "stakeholder"}

_LEAD_RE = re.compile(
    r"(?:experience\s+(?:with|in|of|using)|knowledge\s+of|proficien\w*\s+(?:in|with|at)|"
    r"familiar\w*\s+with|skilled\s+(?:in|at|with)|expertise\s+in|understanding\s+of|"
    r"ability\s+to\s+use|working\s+with|competen\w*\s+(?:in|with)|hands[- ]on\s+(?:experience\s+)?"
    r"(?:with|in)|using|such\s+as|including|e\.g\.?|like)\s*:?\s*([^.;\n]{3,140})", re.I)
_LIST_SPLIT_RE = re.compile(r",|;|/|\band\b|\bor\b|&|\(|\)|\bas well as\b", re.I)
_LEAD_WORDS = {"the", "a", "an", "strong", "good", "solid", "excellent", "proven", "demonstrable",
               "extensive", "working", "basic", "advanced", "hands-on", "practical", "some",
               "relevant", "any", "all", "of", "in", "with", "using", "use", "to", "by", "on"}
_KIND_ORDER = {"hard": 0, "soft": 1, "keyword": 2}

_CAP_SEQ_RE = re.compile(r"\b[A-Z][A-Za-z0-9]+(?:[ ][A-Z][A-Za-z0-9]+){0,2}\b")
_MIXED_CASE_RE = re.compile(r"[a-z][A-Z]")
_LIST_CONTEXT_RE = re.compile(
    r"(?:\b(?:using|with|in|via|on|through|including|like|as|and|or|across|for|of|from|into)|[,/(])\s*$", re.I)
_CAP_DENY = {
    "london", "manchester", "birmingham", "leeds", "glasgow", "edinburgh", "bristol", "liverpool",
    "cardiff", "belfast", "uk", "england", "scotland", "wales", "ireland", "europe", "usa", "america",
    "canada", "india", "australia", "remote", "hybrid", "january", "february", "march", "april", "may",
    "june", "july", "august", "september", "october", "november", "december", "monday", "tuesday",
    "wednesday", "thursday", "friday", "ltd", "inc", "llc", "plc", "limited", "group", "company",
    "english", "equal", "opportunities", "employer", "about", "benefits", "requirements", "responsibilities",
}


def _tool_names(jd_text: str) -> List[str]:
    """Named tools/products written with capitals ("Google Analytics", "HubSpot"):
    CamelCase names anywhere, other capitalised names only inside a list of
    skills ("using X, Y and Z") so places and company names are left out."""
    out: List[str] = []
    for m in _CAP_SEQ_RE.finditer(jd_text):
        words = m.group(0).split(" ")
        while words and words[0].lower() in _CAP_DENY | _STOP | _GENERIC:
            words.pop(0)
        while words and words[-1].lower() in _CAP_DENY | _STOP | _GENERIC:
            words.pop()
        if not words:
            continue
        phrase = " ".join(words)
        mixed = bool(_MIXED_CASE_RE.search(phrase))
        before = jd_text[max(0, m.start() - 16):m.start()]
        in_list = bool(_LIST_CONTEXT_RE.search(before))
        if mixed or (in_list and len(phrase) >= 3):
            out.append(phrase)
    return list(dict.fromkeys(out))


@dataclass
class JDTerm:
    display: str
    kind: str                  # hard | soft | keyword
    stems: Tuple[str, ...]


def _clean_item(raw: str) -> str:
    words = [w for w in re.split(r"\s+", raw.strip(" \t-*•–—:\"'")) if w]
    while words and words[0].lower() in _LEAD_WORDS:
        words.pop(0)
    while words and words[-1].lower() in _LEAD_WORDS:
        words.pop()
    if not words or len(words) > 4:
        return ""
    stems = [s for s in _stems(" ".join(words))]
    if not stems or all(w.lower() in _STOP or w.lower() in _GENERIC for w in words):
        return ""
    if len(" ".join(words)) < 3:
        return ""
    return " ".join(words)


def extract_jd_terms(jd_text: str, *, skip_title: bool = True) -> List[JDTerm]:
    """Everything in a job description worth looking for in a résumé.

    Sources, in priority order: curated hard-skill terms (tech + other
    fields); items in "experience with / knowledge of / such as ..." lists
    (which is how most JDs name their skills, in any field); named tools and
    acronyms; soft skills; and finally words/phrases the JD repeats."""
    jd_text = jd_text or ""
    jd_stems = _stems(jd_text)
    found: "OrderedDict[Tuple[str, ...], JDTerm]" = OrderedDict()

    def add(display: str, kind: str) -> None:
        stems = tuple(_stems(display))
        if not stems:
            return
        existing = found.get(stems)
        if existing is None:
            found[stems] = JDTerm(display, kind, stems)
        elif _KIND_ORDER[kind] < _KIND_ORDER[existing.kind]:
            existing.kind = kind

    # 1. curated hard skills (generic nouns and the shared soft-skill words are filed lower)
    for term in sorted(_GAZETTEER | _GAZETTEER_EXTRA, key=len, reverse=True):
        if term.endswith("+") or term in _AMBIGUOUS:
            continue   # "data+", "go", "r": only trusted when written the unmistakable way (step 3)
        if _contains(jd_stems, tuple(_stems(term)), slack=0):
            kind = "soft" if term in _SOFT_SKILLS else "keyword" if term in _WEAK_HARD else "hard"
            add(_display(term), kind)

    # 2. lists introduced by "experience with ...", "such as ...", "including ..."
    for sentence in re.split(r"\n|(?<=[.!?])\s+", jd_text):
        m = _LEAD_RE.search(sentence)
        if not m:
            continue
        for item in _LIST_SPLIT_RE.split(m.group(1)):
            cleaned = _clean_item(item)
            if cleaned:
                add(cleaned if any(c.isupper() for c in cleaned) else cleaned.title(), "hard")

    # 3. named tools / acronyms (CAD, SAP, SQL, ...)
    joined = re.sub(r"\b([A-Z]{2,5})/([A-Z]{2,5})\b", r"\1\2", jd_text)   # CI/CD -> CICD (one term)
    for phrase in _proper_noun_terms(joined):
        add(phrase, "hard")

    for phrase in _tool_names(joined):
        add(phrase, "hard")

    # 4. soft skills
    for term in sorted(_SOFT_SKILLS, key=len, reverse=True):
        if _contains(jd_stems, tuple(_stems(term)), slack=0):
            add(term.title(), "soft")

    # 5. words and word pairs the JD keeps coming back to
    covered = {st for st in found}

    def is_covered(stems: Tuple[str, ...]) -> bool:
        return any(set(stems) <= set(c) for c in covered)

    words = [w for w in _canon_text(jd_text).split()]
    uni: "OrderedDict[str, List[str]]" = OrderedDict()
    bi: "OrderedDict[Tuple[str, str], List[str]]" = OrderedDict()
    for i, w in enumerate(words):
        if w in _STOP or w in _GENERIC or len(w) < 4 or w.isdigit():
            continue
        uni.setdefault(_stem(w), []).append(w)
        if i + 1 < len(words):
            n = words[i + 1]
            if n not in _STOP and n not in _GENERIC and len(n) >= 4 and not n.isdigit():
                bi.setdefault((_stem(w), _stem(n)), []).append(f"{w} {n}")
    extras: List[Tuple[int, str]] = []
    for stems, forms in bi.items():
        if len(forms) >= 2 and not is_covered(stems):
            extras.append((len(forms) * 2, forms[0]))
    for stem, forms in uni.items():
        if len(forms) >= 3 and not is_covered((stem,)):
            extras.append((len(forms), forms[0]))
    extras.sort(key=lambda t: -t[0])
    for _, phrase in extras[:8]:
        add(phrase.title(), "keyword")

    if skip_title:
        title_stems = set(_stems(detect_jd_title(jd_text)))
        for stems in [k for k, t in found.items() if t.kind == "keyword" and set(k) <= title_stems]:
            del found[stems]   # the title is scored on its own; its words aren't extra keywords
    # A term wholly contained in another hard term ("marketing automation" inside
    # "marketing automation tools") is the same ask: keep the shorter, cleaner one.
    hard_sets = [(k, set(k)) for k, t in found.items() if t.kind == "hard"]
    for key, kset in hard_sets:
        if any(kset > other for _, other in hard_sets if other != kset):
            found.pop(key, None)
    # Show names the way the job description writes them ("HubSpot", not "Hubspot").
    for term in found.values():
        m = re.search(re.escape(term.display), jd_text, re.I)
        if m and any(c.isupper() for c in m.group(0)[1:]) and term.display.lower() not in _DISPLAY_OVERRIDES:
            term.display = m.group(0)
    ordered = sorted(found.values(), key=lambda t: _KIND_ORDER[t.kind])  # stable: JD order within kind
    hard = [t for t in ordered if t.kind == "hard"][:24]
    soft = [t for t in ordered if t.kind == "soft"][:5]
    kw = [t for t in ordered if t.kind == "keyword"][:8]
    return hard + soft + kw


_TITLE_LABEL_RE = re.compile(r"^\s*(?:job\s*title|position|role|vacancy)\s*[:\-–]\s*(.+?)\s*$", re.I | re.M)
_NOT_A_TITLE = ("about", "we ", "we're", "the ", "job description", "overview", "company", "who we",
                "position summary", "summary", "description", "responsibilit", "requirement",
                "apply", "location", "salary", "our ", "join ", "are you", "do you")
_SENIORITY = {"senior", "junior", "sr", "jr", "lead", "principal", "staff", "mid", "entry", "level",
              "graduate", "trainee", "intern"}


_SENIORITY_STEMS = {_stem(w) for w in _SENIORITY}


def detect_jd_title(jd_text: str) -> str:
    """The job's title, if the description states one ("Job title: X" or a short
    first line); else ''."""
    jd_text = (jd_text or "").strip()
    if not jd_text:
        return ""
    m = _TITLE_LABEL_RE.search(jd_text)
    cand = m.group(1) if m else next((ln.strip() for ln in jd_text.splitlines() if ln.strip()), "")
    cand = re.split(r"\s+[-–—|@]\s+|\s*[\(\[,:].*$", cand)[0].strip(" -–—|")
    low = cand.lower()
    if (not cand or len(cand) > 70 or len(cand.split()) > 8 or cand.endswith((".", "!", "?"))
            or any(low.startswith(p) for p in _NOT_A_TITLE) or not re.search(r"[A-Za-z]{3}", cand)):
        return ""
    return cand


def _title_score(jd_title: str, resume: ResumeData, resume_stems: List[str]) -> SectionScore:
    if not jd_title:
        return SectionScore(0, False, "No job title found at the top of the description.")
    want = tuple(s for s in _stems(jd_title) if s not in _SENIORITY_STEMS)
    if not want:
        return SectionScore(0, False, "No usable job title.")
    titles = [_stems(e.job_title) for e in resume.experience if e.job_title.strip()]
    if any(_contains(t, want, slack=0) for t in titles):
        return SectionScore(100, True, f"“{jd_title}” matches a title on your résumé.")
    best = 0.0
    for t in titles:
        best = max(best, len(set(want) & set(t)) / len(want))
    score = int(round(best * 100))
    if _contains(resume_stems, want, slack=3):
        score = max(score, 70)
        detail = f"“{jd_title}” isn't one of your titles, but its words appear in your résumé."
    elif len(set(want) & set(resume_stems)) * 2 >= len(want):
        score = max(score, 30)
        detail = f"Part of “{jd_title}” appears in your résumé."
    else:
        detail = f"“{jd_title}” doesn't appear on your résumé."
    return SectionScore(score, True, detail)


# --- Education -----------------------------------------------------------------

_LEVEL_WORDS_JD = [
    (4, ("phd", "doctorate", "doctoral")),
    (3, ("master", "msc", "mba", "postgraduate", "mtech", "meng")),
    (2, ("bachelor", "bsc", "btech", "beng", "undergraduate", "degree", "graduate")),
    (1, ("diploma", "hnd", "hnc", "associate", "certificate", "qualification")),
]
_LEVEL_PHRASES_RESUME = [
    (4, (" phd ", " doctor", " dphil ")),
    (3, (" master", " msc ", " m sc ", " mba ", " mtech ", " m tech ", " meng ", " m eng ", " ms ", " m s ", " ma ", " m a ")),
    (2, (" bachelor", " bsc ", " b sc ", " btech ", " b tech ", " be ", " b e ", " beng ", " b eng ", " ba ", " b a ", " degree")),
    (1, (" diploma", " hnd ", " hnc ", " associate", " certificate")),
]
_LEVEL_NAME = {1: "diploma-level", 2: "bachelor's", 3: "master's", 4: "doctoral"}
_OPTIONAL_RE = re.compile(r"\b(preferred|desirable|desired|advantageous|a plus|bonus|nice to have|beneficial)\b", re.I)
_EQUIV_RE = re.compile(r"\b(or equivalent|equivalent (?:experience|qualification)|or relevant experience|or similar experience)\b", re.I)


def _required_education(jd_text: str) -> Tuple[int, bool, str]:
    """(minimum level required, equivalent experience accepted, JD wording)."""
    fragments, levels, flexible = [], [], False
    for sentence in re.split(r"(?<=[.!?])\s+|\n", jd_text):
        low = sentence.lower()
        if not any(h in low for h in _EDU_HINTS):
            continue
        fragments.append(sentence.strip(" \t-*•–—"))
        if _EQUIV_RE.search(sentence):
            flexible = True
        optional = bool(_OPTIONAL_RE.search(sentence))
        tokens = set(_canon_text(sentence).split())
        for level, words in _LEVEL_WORDS_JD:
            if tokens & set(words) and not (optional and level >= 3):
                levels.append(level)
    return (min(levels) if levels else 0), flexible, " ".join(fragments[:3])


def _resume_education_level(resume: ResumeData) -> int:
    best = 0
    for edu in resume.education:
        text = f" {_canon_text(f'{edu.degree} {edu.field_of_study}')} "
        for level, phrases in _LEVEL_PHRASES_RESUME:
            if any(p in text for p in phrases):
                best = max(best, level)
    return best


def _education_score(jd_text: str, resume: ResumeData) -> SectionScore:
    required, flexible, wording = _required_education(jd_text)
    if not required:
        return SectionScore(0, False, "JD states no education requirement.")
    have = _resume_education_level(resume)
    has_experience = any(e.bullet_points for e in resume.experience)
    if have >= required:
        return SectionScore(100, True, f"Your education meets the job's {_LEVEL_NAME[required]} requirement.")
    if not resume.education:
        score = 60 if (flexible and has_experience) else 0
        return SectionScore(score, True, "The job asks for a qualification; none is listed on your résumé.")
    if have == 0:
        return SectionScore(50, True, "The job asks for a qualification; couldn't read a degree level from your education entries.")
    score = 50 if have == required - 1 else 0
    if flexible and has_experience:
        score = max(score, 70)
    return SectionScore(score, True, f"The job asks for a {_LEVEL_NAME[required]} qualification; "
                                     f"your highest listed is {_LEVEL_NAME[have]}.")


# --- Résumé side ----------------------------------------------------------------

def _evidence_units(resume: ResumeData) -> List[str]:
    """Résumé lines a JD requirement can be matched against: every bullet (work
    and projects), project descriptions and the summary."""
    units = [b.strip() for e in resume.experience for b in e.bullet_points if b.strip()]
    for proj in resume.projects:
        if proj.description.strip():
            units.append(proj.description.strip())
        units += [b.strip() for b in proj.bullet_points if b.strip()]
    summary = resume.personal_info.professional_summary.strip()
    if summary:
        units += [s.strip() for s in re.split(r"(?<=[.!?])\s+", summary) if s.strip()]
    for category in resume.skills:
        if category.skills:
            label = category.category_name.strip()
            units.append((f"{label}: " if label else "Skills: ") + ", ".join(category.skills))
    return units


def _resume_wording(term: JDTerm, resume: ResumeData, resume_norm: str) -> str:
    """How the résumé itself words this keyword ('' if it only matches via a
    variant spelling) -- the cover letter may only claim skills in this form."""
    for skill in resume.all_skills_flat():
        if _contains(_stems(skill), term.stems, slack=0):
            return skill
    if re.search(r"(?<![\w+#])" + re.escape(term.display.lower()) + r"(?![\w+#])", resume_norm):
        return term.display
    return ""


def _coverage(req_stems: set, unit_stems: set) -> float:
    return len(req_stems & unit_stems) / len(req_stems) if req_stems else 0.0


# --- Scoring helpers -----------------------------------------------------------

def _calibrate(cosine: float) -> int:
    """Map a raw cosine onto a readable 0-100 sub-score."""
    frac = (cosine - _CAL_LO) / (_CAL_HI - _CAL_LO)
    return int(round(max(0.0, min(1.0, frac)) * 100))


def _blend_overall(keywords_pct: Optional[int], exp: SectionScore, title: SectionScore,
                   edu: SectionScore) -> int:
    """Weighted blend of the sections that apply into one headline number."""
    parts: List[Tuple[float, int]] = []
    if keywords_pct is not None:
        parts.append((_W_KEYWORDS, keywords_pct))
    if exp.specified:
        parts.append((_W_EXPERIENCE, exp.score))
    if title.specified:
        parts.append((_W_TITLE, title.score))
    if edu.specified:
        parts.append((_W_EDUCATION, edu.score))
    if not parts:
        return 0
    total_w = sum(w for w, _ in parts)
    return int(round(sum(w * s for w, s in parts) / total_w))


# --- Public entry point --------------------------------------------------------

def analyze(resume: ResumeData, jd_text: str, *, api_key: Optional[str] = None) -> MatchReport:
    """Score a résumé against a job description, with a full breakdown.

    The text match (stems + variants over the whole résumé) always runs. If an
    embeddings key is available it then rescues keywords the text match missed
    and can lift a requirement's score -- it never lowers one. `api_key` may be
    passed explicitly so this is safe to call off the main thread.
    """
    jd_text = jd_text or ""
    terms = extract_jd_terms(jd_text)
    resume_text = resume.searchable_text()
    resume_norm = re.sub(r"\s+", " ", resume_text.lower())
    resume_stems = _stems(resume_text)

    covered = {t.stems: _contains(resume_stems, t.stems) for t in terms}
    matched: List[SkillMatch] = []
    missing_terms: List[JDTerm] = []
    for t in terms:
        if covered[t.stems]:
            matched.append(SkillMatch(t.display, _resume_wording(t, resume, resume_norm), 1.0, t.kind))
        else:
            missing_terms.append(t)

    units = _evidence_units(resume)
    reqs = _split_requirements(jd_text)

    key = api_key or (embeddings._api_key() if embeddings.is_configured() else None)
    used_semantic = False
    unit_vecs: List[List[float]] = []
    if key and (missing_terms or reqs) and (units or resume.all_skills_flat()):
        try:
            pool = list(dict.fromkeys(resume.all_skills_flat() + units))[:300]
            pool_vecs = embeddings.embed(pool, api_key=key)
            if missing_terms:
                term_vecs = embeddings.embed([t.display for t in missing_terms], api_key=key)
                still_missing: List[JDTerm] = []
                for t, vec in zip(missing_terms, term_vecs):
                    idx, score = embeddings.best_match(vec, pool_vecs)
                    if idx >= 0 and score >= _SEMANTIC_RESCUE:
                        matched.append(SkillMatch(t.display, pool[idx] if len(pool[idx]) <= 40 else "",
                                                  score, t.kind))
                        covered[t.stems] = True
                        used_semantic = True
                    else:
                        still_missing.append(t)
                missing_terms = still_missing
            if units and reqs:
                unit_vecs = embeddings.embed(units, api_key=key)
        except embeddings.EmbeddingError:
            unit_vecs = []

    matched.sort(key=lambda m: _KIND_ORDER.get(m.kind, 3))
    total_w = sum(_KIND_WEIGHT[t.kind] for t in terms)
    got_w = sum(_KIND_WEIGHT[t.kind] for t in terms if covered[t.stems])
    keyword_pct = int(round(got_w / total_w * 100)) if total_w else None

    # --- Experience: how well do the résumé's bullets cover what the job asks? ---
    unit_stems = [content_stems(u) for u in units]
    term_stem_sets = [set(t.stems) for t in terms if t.kind != "soft"]
    relevant_reqs = [r for r in reqs if len(content_stems(r)) >= 3
                     and not any(h in r.lower() for h in _EDU_HINTS)]   # degrees are scored separately
    # Keep the lines that name a skill/tool; benefits and company blurb don't.
    focused = [r for r in relevant_reqs if any(ts <= content_stems(r) for ts in term_stem_sets)]
    scored_reqs = focused or relevant_reqs
    req_vecs = embeddings.embed(scored_reqs, api_key=key) if (unit_vecs and scored_reqs) else []
    req_matches: List[RequirementMatch] = []
    for i, req in enumerate(scored_reqs):
        rs = content_stems(req)
        best_idx, best_cov = -1, 0.0
        for j, us in enumerate(unit_stems):
            cov = _coverage(rs, us)
            if cov > best_cov:
                best_idx, best_cov = j, cov
        score, raw = min(1.0, best_cov / _REQ_FULL_COVERAGE), best_cov
        if req_vecs:
            sem_idx, cos = embeddings.best_match(req_vecs[i], unit_vecs)
            sem_score = _calibrate(cos) / 100
            if sem_idx >= 0 and sem_score > score:
                score, raw, best_idx, used_semantic = sem_score, cos, sem_idx, True
        req_matches.append(RequirementMatch(req, units[best_idx] if best_idx >= 0 else "", raw, score))
    req_matches.sort(key=lambda m: m.score, reverse=True)
    if req_matches:
        exp_pct = int(round(sum(m.score for m in req_matches) / len(req_matches) * 100))
        detail = (f"{len(units)} résumé line(s) checked against {len(req_matches)} job requirement(s)."
                  if units else "No bullets on the résumé yet.")
        experience = SectionScore(exp_pct, True, detail)
    else:
        experience = SectionScore(0, False, "JD lists no explicit responsibilities.")

    jd_title = detect_jd_title(jd_text)
    title = _title_score(jd_title, resume, resume_stems)
    education = _education_score(jd_text, resume)

    return MatchReport(
        overall=_blend_overall(keyword_pct, experience, title, education),
        skills_matched=matched,
        skills_missing=[t.display for t in missing_terms],
        experience=experience,
        education=education,
        title=title,
        jd_title=jd_title,
        keyword_pct=keyword_pct,
        requirement_matches=req_matches,
        semantic=used_semantic,
        missing_kinds={t.display: t.kind for t in missing_terms},
    )


# --- Plain tokenisation kept for the suggestion retriever -----------------------

def _tokenize(text: str) -> set:
    """Stem set of the meaningful words in `text` (retrieval overlap)."""
    return content_stems(text)
