"""Agent workflow for JD-tailored, evidence-based resume suggestions.

Instead of dumping the whole resume and job description into one big LLM call,
the work is split into four small, single-purpose agents. Three of them are
deterministic (embeddings + arithmetic, no LLM) so they're fast, cheap, and
testable; only the last one calls the language model, and even then it sees
only a handful of retrieved snippets, never the whole resume.

    1. Scorer    -- ats_analyzer.analyze(): the skills / experience / education
                    breakdown and each JD requirement's best-supporting bullet.
                    (Lives in utils/ats_analyzer.py; called by the page.)
    2. Retriever -- embeds every resume bullet once and, for each JD
                    requirement, retrieves the single most relevant bullet
                    (with provenance, so a rewrite can be applied back). This is
                    the RAG step: only retrieved bullets reach the LLM.
    3. Evidence  -- binds each retrieved bullet to the JD requirement that
                    surfaced it and to a confidence derived from the retrieval
                    similarity. Nothing here is model-generated, so the "why"
                    behind every suggestion is grounded, not hallucinated.
    4. Writer    -- ai_assistant.tailor_bullets(): the one LLM call. Rewrites
                    each retrieved bullet to emphasise its requirement, never
                    inventing anything not already in the bullet.

The result is a list of Suggestion cards:

    Suggestion  ->  which JD requirement caused it
                ->  which resume bullet is being modified (with the rewrite)
                ->  confidence.

Degrades without an embeddings key: retrieval falls back to token overlap, so
suggestions still work with only the chat key -- they just aren't synonym-aware.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from models.resume_data import ResumeData
from utils import ai_assistant, ats_analyzer, embeddings

# Below this retrieval strength (0-1) there isn't enough genuine evidence that a
# bullet supports a requirement, so we don't propose a rewrite -- rewriting a
# barely-related bullet is exactly where fabrication would creep in.
_MIN_EVIDENCE_SEMANTIC = 0.30
_MIN_EVIDENCE_LEXICAL = 0.15
# Cap how many suggestion cards we generate (and how many bullets hit the LLM).
_MAX_SUGGESTIONS = 8


# --- Data contracts ------------------------------------------------------------

@dataclass
class ResumeUnit:
    """One editable resume bullet, with enough provenance to apply a rewrite."""
    text: str
    source: str        # 'experience' | 'project'
    entry_id: str
    bullet_index: int
    role_label: str    # e.g. "Data Analyst at Acme" -- for display only


@dataclass
class Suggestion:
    """An evidence-based tailoring suggestion, ready to render as a card."""
    original: str        # the bullet as it stands
    proposed: str        # the AI rewrite (never invents; only rephrases)
    jd_requirement: str  # the JD line that triggered this suggestion
    role_label: str      # which resume item is being modified
    source: str          # 'experience' | 'project'
    entry_id: str
    bullet_index: int
    confidence: str      # 'High' | 'Medium' | 'Low'
    strength: float      # retrieval similarity 0-1 (sort/debug)


# --- Retriever agent -----------------------------------------------------------

def collect_units(resume: ResumeData) -> List[ResumeUnit]:
    """Gather every editable bullet (experience first, then projects) with provenance."""
    units: List[ResumeUnit] = []
    for exp in resume.experience:
        label = " at ".join(p for p in [exp.job_title, exp.company] if p) or "Experience"
        for i, bullet in enumerate(exp.bullet_points):
            if bullet.strip():
                units.append(ResumeUnit(bullet.strip(), "experience", exp.id, i, label))
    for proj in resume.projects:
        label = proj.name or "Project"
        for i, bullet in enumerate(proj.bullet_points):
            if bullet.strip():
                units.append(ResumeUnit(bullet.strip(), "project", proj.id, i, label))
    return units


def _retrieve_semantic(
    reqs: Sequence[str], units: Sequence[ResumeUnit], key: str
) -> List[Tuple[ResumeUnit, str, float]]:
    """For each JD requirement, retrieve its most similar resume bullet (embeddings)."""
    if not reqs or not units:
        return []
    req_vecs = embeddings.embed(list(reqs), api_key=key)
    unit_vecs = embeddings.embed([u.text for u in units], api_key=key)
    out: List[Tuple[ResumeUnit, str, float]] = []
    for req, rvec in zip(reqs, req_vecs):
        idx, score = embeddings.best_match(rvec, unit_vecs)
        if idx >= 0:
            out.append((units[idx], req, score))
    return out


def _retrieve_lexical(
    reqs: Sequence[str], units: Sequence[ResumeUnit]
) -> List[Tuple[ResumeUnit, str, float]]:
    """Token-overlap retrieval used when embeddings aren't configured."""
    unit_tokens = [(u, ats_analyzer._tokenize(u.text)) for u in units]
    out: List[Tuple[ResumeUnit, str, float]] = []
    for req in reqs:
        rt = ats_analyzer._tokenize(req)
        if not rt:
            continue
        best_u, best_share = None, 0.0
        for u, ut in unit_tokens:
            share = len(rt & ut) / len(rt)
            if share > best_share:
                best_share, best_u = share, u
        if best_u is not None:
            out.append((best_u, req, best_share))
    return out


# --- Evidence agent ------------------------------------------------------------

def _confidence(strength: float, semantic: bool) -> str:
    """Map a retrieval strength (0-1) to a High/Medium/Low label."""
    pct = ats_analyzer._calibrate(strength) if semantic else int(round(strength * 100))
    if pct >= 60:
        return "High"
    if pct >= 40:
        return "Medium"
    return "Low"


def _dedupe_by_unit(
    matches: List[Tuple[ResumeUnit, str, float]]
) -> List[Tuple[ResumeUnit, str, float]]:
    """Keep only the strongest (requirement, strength) per bullet.

    Several JD requirements can point at the same bullet; rewriting it once,
    tied to its strongest-matching requirement, avoids conflicting edits.
    """
    best: dict = {}
    for unit, req, strength in matches:
        pkey = (unit.source, unit.entry_id, unit.bullet_index)
        if pkey not in best or strength > best[pkey][2]:
            best[pkey] = (unit, req, strength)
    return sorted(best.values(), key=lambda t: t[2], reverse=True)


# --- Orchestration -------------------------------------------------------------

def generate_suggestions(
    resume: ResumeData,
    jd_text: str,
    *,
    api_key: Optional[str] = None,
    max_suggestions: int = _MAX_SUGGESTIONS,
) -> List[Suggestion]:
    """Run the retriever -> evidence -> writer pipeline and return suggestion cards.

    Raises ai_assistant.AIError if the chat model is unavailable (the page shows
    the notice). Returns [] when there's nothing worth suggesting (no bullets,
    no requirements, or no bullet clears the evidence threshold).
    """
    units = collect_units(resume)
    reqs = ats_analyzer._split_requirements(jd_text)
    if not units or not reqs:
        return []

    key = api_key or (embeddings._api_key() if embeddings.is_configured() else None)
    semantic = bool(key)
    if semantic:
        try:
            matches = _retrieve_semantic(reqs, units, key)  # type: ignore[arg-type]
        except embeddings.EmbeddingError:
            semantic = False
            matches = _retrieve_lexical(reqs, units)
    else:
        matches = _retrieve_lexical(reqs, units)

    # Evidence: dedupe to one requirement per bullet, keep only well-supported
    # pairs, and take the strongest few.
    floor = _MIN_EVIDENCE_SEMANTIC if semantic else _MIN_EVIDENCE_LEXICAL
    ranked = [m for m in _dedupe_by_unit(matches) if m[2] >= floor][:max_suggestions]
    if not ranked:
        return []

    # Writer: the single LLM call, over only the retrieved bullets.
    pairs = [(unit.text, req) for unit, req, _ in ranked]
    rewrites = ai_assistant.tailor_bullets(pairs)

    suggestions: List[Suggestion] = []
    for (unit, req, strength), rewrite in zip(ranked, rewrites):
        proposed = (rewrite or "").strip()
        # Skip no-ops: if the model returned the original (or nothing), there's
        # no change to offer.
        if not proposed or proposed.lower() == unit.text.strip().lower():
            continue
        suggestions.append(
            Suggestion(
                original=unit.text,
                proposed=proposed,
                jd_requirement=req,
                role_label=unit.role_label,
                source=unit.source,
                entry_id=unit.entry_id,
                bullet_index=unit.bullet_index,
                confidence=_confidence(strength, semantic),
                strength=strength,
            )
        )
    return suggestions
