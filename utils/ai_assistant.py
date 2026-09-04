"""AI writing assistant for Phase 4, backed by Tencent Hunyuan 3 via OpenRouter.

Every function here only ever *rephrases or organizes content the user has
already entered*. The system prompt forbids inventing employers, dates,
metrics, technologies, or skills, and the UI treats all output as a
suggestion the user must explicitly accept -- pages never overwrite the
resume silently. This keeps the whole app aligned with its core rule:
never fabricate experience or qualifications.

Calls the OpenAI-compatible chat API. It defaults to OpenRouter serving
Tencent's Hunyuan 3 free tier (`tencent/hy3:free`), but base URL, model, and
key are all configurable, so any OpenAI-compatible endpoint works. The user
supplies their own key via Streamlit secrets (`OPENROUTER_API_KEY`) or the
environment.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, List, Optional, Tuple

import streamlit as st

from models.resume_data import ResumeData

# Leading "1. " / "2) " style list numbering to strip from model output.
_LIST_NUMBER_RE = re.compile(r"^\d+[.)]\s+")

_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"  # free tier on OpenRouter; override with OPENROUTER_MODEL

# Free-tier endpoints drop requests when the upstream provider is saturated, and
# OpenRouter reports that as a *success* (HTTP 200) whose body carries `error`
# and no `choices`. Retrying the same model usually clears it; when it doesn't,
# a sibling free model does. Override the chain with OPENROUTER_FALLBACK_MODELS
# (comma-separated), or set it empty to disable falling back.
_DEFAULT_FALLBACK_MODELS = ("nvidia/nemotron-3-super-120b-a12b:free",)
_MAX_ATTEMPTS = 3          # per model, including the first try
_RETRY_BACKOFF_SECONDS = 1.5  # doubled on each retry

# Substrings that mark a failure as worth retrying rather than reporting.
_TRANSIENT_HINTS = (
    "rate limit", "rate-limit", "429", "500", "502", "503", "504",
    "timeout", "timed out", "temporarily", "overloaded", "capacity",
    "unavailable", "no choices", "empty response", "connection",
)

_SYSTEM_PROMPT = (
    "You are an expert resume editor. You rewrite resume content to be concise, "
    "professional, achievement-oriented, and ATS-friendly: plain text only, strong "
    "action verbs, standard resume phrasing, no tables, columns, emojis, or graphics.\n\n"
    "ABSOLUTE RULES -- you must never break these:\n"
    "- Only rephrase or reorganize information that is explicitly given to you.\n"
    "- NEVER invent employers, job titles, dates, degrees, metrics, numbers, "
    "percentages, technologies, tools, certifications, or achievements.\n"
    "- If a line has no quantified metric, do NOT fabricate one.\n"
    "- Do NOT add skills or responsibilities the person did not state.\n"
    "- Preserve the original factual meaning exactly; improve only the wording.\n"
    "- Return ONLY the requested text, with no preamble, notes, or explanation."
)


# A cover letter is prose, not résumé bullets, so it needs its own system prompt --
# but the same never-invent contract, restated for letter-specific temptations
# (claiming a skill because the job asked for it, praising a company you know
# nothing about, padding with the usual AI cover-letter clichés).
_COVER_LETTER_SYSTEM = (
    "You write job-application cover letters. You write the way a capable, "
    "self-aware person writes about their own work: specific, plain, and measured, "
    "with no salesmanship.\n\n"
    "ABSOLUTE RULES -- you must never break these:\n"
    "- Use ONLY facts explicitly present in the résumé material you are given.\n"
    "- NEVER invent or imply employers, job titles, dates, degrees, certifications, "
    "metrics, numbers, percentages, tools, technologies, achievements, or "
    "responsibilities.\n"
    "- NEVER claim, imply, or hint at experience with a skill just because the job "
    "description names it. If the résumé does not show it, it does not go in the letter.\n"
    "- Do NOT restate the résumé line by line. Explain in natural prose why the real "
    "experience fits this specific role.\n"
    "- Say nothing about the company beyond what you are told -- no invented praise for "
    "its products, mission, culture, scale, or reputation.\n"
    "- Avoid cliché AI cover-letter phrasing: 'I am writing to express my keen interest', "
    "'proven track record', 'passionate about leveraging', 'dynamic team player', "
    "'perfect fit', 'hit the ground running', 'in today's fast-paced world'. No keyword "
    "stuffing and no buzzword padding.\n"
    "- Before answering, silently check every factual claim against the résumé material "
    "and delete anything you cannot point to.\n"
    "- Output ONLY the letter: salutation, body paragraphs, sign-off. No letterhead, "
    "postal addresses, date, subject line, markdown, preamble, notes, or commentary."
)


class AIError(Exception):
    """Raised for any AI-generation problem (no key, bad model, API error)."""


# --- Configuration -------------------------------------------------------------

def _get_secret(name: str) -> Optional[str]:
    """Read a value from st.secrets (Streamlit Cloud) then the environment.

    Accessing st.secrets with no secrets file raises, so it's guarded.
    """
    try:
        value = st.secrets.get(name)  # type: ignore[attr-defined]
        if value:
            return str(value)
    except Exception:
        pass
    return os.environ.get(name)


def _api_key() -> Optional[str]:
    return _get_secret("OPENROUTER_API_KEY")


def _model_name() -> str:
    return _get_secret("OPENROUTER_MODEL") or _DEFAULT_MODEL


def _base_url() -> str:
    return _get_secret("OPENROUTER_BASE_URL") or _DEFAULT_BASE_URL


def _model_chain() -> List[str]:
    """The configured model first, then the fallbacks, de-duplicated.

    Only the first model is used unless it fails every attempt -- the fallbacks
    exist so a saturated free endpoint doesn't take the whole feature down.
    """
    raw = _get_secret("OPENROUTER_FALLBACK_MODELS")
    if raw is None:
        fallbacks = list(_DEFAULT_FALLBACK_MODELS)
    else:
        fallbacks = [m.strip() for m in raw.split(",") if m.strip()]
    chain: List[str] = []
    for model in [_model_name(), *fallbacks]:
        if model and model not in chain:
            chain.append(model)
    return chain


def is_configured() -> bool:
    """True if an OpenRouter API key is available, so AI features can run."""
    return bool(_api_key())


def render_unavailable_notice() -> None:
    """Shown in place of AI controls when no API key is configured."""
    st.info(
        "**AI features need an OpenRouter API key** (free with the default "
        f"`{_DEFAULT_MODEL}` model).\n\n"
        "1. Get one at https://openrouter.ai/keys.\n"
        "2. **Locally:** add it to `.streamlit/secrets.toml` as "
        "`OPENROUTER_API_KEY = \"your-key\"` (this file is git-ignored).\n"
        "3. **On Streamlit Cloud:** add `OPENROUTER_API_KEY` under the app's "
        "**Settings → Secrets**.\n\n"
        "Then reload this page. The AI never fabricates content -- it only "
        "rewrites what you've already entered."
    )


# --- Core call -----------------------------------------------------------------

def _response_error(response: Any) -> Optional[str]:
    """Pull OpenRouter's error payload off an otherwise-successful response.

    OpenRouter answers HTTP 200 with a body like `{"error": {"code": 429,
    "message": "..."}}` and no `choices` when the *upstream provider* fails --
    rate limits, capacity, moderation. The OpenAI client never raises for those,
    so without reading `error` by hand the only symptom is an empty `choices`
    list and the real reason is lost.
    """
    err = getattr(response, "error", None)
    if err is None:
        extra = getattr(response, "model_extra", None)
        if isinstance(extra, dict):
            err = extra.get("error")
    if not err:
        return None
    if not isinstance(err, dict):
        return str(err)
    message = str(err.get("message") or "").strip()
    metadata = err.get("metadata")
    raw = ""
    if isinstance(metadata, dict):
        raw = str(metadata.get("raw") or "").strip()
    detail = " -- ".join(p for p in (message, raw) if p) or "unknown upstream error"
    code = err.get("code")
    return f"{detail} (code {code})" if code is not None else detail


def _is_transient(reason: str) -> bool:
    """True if a failure is the kind that a retry (or another model) can clear."""
    lowered = reason.lower()
    return any(hint in lowered for hint in _TRANSIENT_HINTS)


def _generate(prompt: str, temperature: float = 0.4, *, system: Optional[str] = None) -> str:
    """Send one prompt to the model (OpenRouter) and return the trimmed text.

    `system` overrides the résumé-editor system prompt for tasks that aren't
    résumé editing (the cover letter writes prose, not ATS bullet lines). The
    never-invent rules are restated in every system prompt used here.

    Free endpoints fail intermittently, so each model in `_model_chain()` gets
    `_MAX_ATTEMPTS` tries with exponential backoff before moving on. Only a
    genuinely transient failure is retried -- a bad key, a bad model slug, or a
    rejected prompt is reported immediately, with the provider's own wording.
    """
    key = _api_key()
    if not key:
        raise AIError("No OpenRouter API key configured.")

    # Imported lazily so a missing package never breaks unrelated pages.
    from openai import OpenAI

    client = OpenAI(
        api_key=key,
        base_url=_base_url(),
        default_headers={"X-Title": "AI Resume Builder"},  # optional OpenRouter attribution
        timeout=120.0,
        max_retries=0,  # retries are handled below, so backoff and fallback stay in one place
    )

    chain = _model_chain()
    last_reason = "no attempt was made"
    last_transient = False
    for model in chain:
        for attempt in range(_MAX_ATTEMPTS):
            reason = ""
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system or _SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=temperature,
                    # These are reasoning models: they spend tokens "thinking"
                    # before writing the answer, and those count against the
                    # budget. A small cap gets fully consumed by reasoning,
                    # leaving no room for the answer (empty content,
                    # finish_reason="length"). So: a large total budget, plus a
                    # hard cap on reasoning tokens so the bulk is always left
                    # for the actual answer.
                    max_tokens=8000,
                    extra_body={"reasoning": {"max_tokens": 1200}},
                )
            except Exception as exc:  # noqa: BLE001 -- surface any API problem to the caller
                reason = str(exc) or exc.__class__.__name__
            else:
                api_error = _response_error(response)
                choices = getattr(response, "choices", None)
                if not choices:
                    # The defining symptom of an upstream failure returned as a
                    # 200: report what the provider actually said, not just
                    # "no choices".
                    reason = api_error or "the model returned no choices"
                else:
                    choice = choices[0]
                    message = getattr(choice, "message", None)
                    text = (getattr(message, "content", None) or "").strip()
                    if text:
                        return text
                    if getattr(choice, "finish_reason", None) == "length":
                        reason = "the response was cut off before any text was produced"
                    else:
                        reason = api_error or "the model returned an empty response"

            # Judge the raw reason, never the decorated one -- a model slug can
            # contain digits that look like status codes.
            last_reason = f"{reason} [model: {model}]"
            last_transient = _is_transient(reason)
            if not last_transient or attempt + 1 >= _MAX_ATTEMPTS:
                break
            time.sleep(_RETRY_BACKOFF_SECONDS * (2 ** attempt))

        if not last_transient:
            # A non-transient failure (bad key, bad slug, rejected prompt) will
            # fail identically on every other model, so stop here.
            break

    hint = (
        " The free tier is busy or your daily free-model quota is used up -- wait a minute "
        "and retry, or set OPENROUTER_MODEL in your secrets to a paid model such as "
        "'tencent/hy3'."
        if last_transient
        else ""
    )
    raise AIError(f"{last_reason}.{hint}")


def _parse_bullets(text: str) -> List[str]:
    """Split a model response into clean bullet lines (drops markers/numbering)."""
    bullets: List[str] = []
    for line in text.splitlines():
        cleaned = line.strip().lstrip("-*•").strip()
        cleaned = _LIST_NUMBER_RE.sub("", cleaned)
        if cleaned:
            bullets.append(cleaned)
    return bullets


def _parse_numbered(text: str, n: int) -> Optional[List[str]]:
    """Pull exactly `n` items out of a `1. ... 2. ...` numbered response.

    The Writer is asked to return one rewritten bullet per numbered line, in the
    same order as the input. This keeps each rewrite aligned to the bullet (and
    therefore the JD requirement) it came from. Returns None if the model didn't
    return the expected count, so the caller can fall back to the originals
    rather than mis-pairing evidence.
    """
    items: List[str] = []
    for line in text.splitlines():
        stripped = line.strip().lstrip("-*•").strip()
        m = re.match(r"^(\d+)[.)]\s*(.+)$", stripped)
        if m:
            items.append(m.group(2).strip())
    if len(items) == n:
        return items
    return None


def _jd_clause(jd: str) -> str:
    """Optional instruction to gently tailor wording toward a target job."""
    if not jd.strip():
        return ""
    return (
        "\n\nTarget job description (for tailoring emphasis ONLY -- do not add "
        "anything the candidate did not state, and do not claim skills from this "
        f"job they don't have):\n{jd.strip()[:2000]}"
    )


# --- Feature functions ---------------------------------------------------------

def _resume_facts(resume: ResumeData) -> str:
    """A compact, factual dump of the resume for grounding generation."""
    lines: List[str] = []
    for exp in resume.experience:
        header = " at ".join(p for p in [exp.job_title, exp.company] if p)
        if header:
            lines.append(f"Role: {header}")
        for b in exp.bullet_points:
            lines.append(f"  - {b}")
    for proj in resume.projects:
        if proj.name:
            tech = f" ({', '.join(proj.technologies)})" if proj.technologies else ""
            lines.append(f"Project: {proj.name}{tech}")
        if proj.description:
            lines.append(f"  {proj.description}")
    for edu in resume.education:
        deg = " , ".join(p for p in [edu.degree, edu.institution] if p)
        if deg:
            lines.append(f"Education: {deg}")
    skills = resume.all_skills_flat()
    if skills:
        lines.append(f"Skills: {', '.join(skills)}")
    return "\n".join(lines)


def generate_summary(resume: ResumeData, jd: str = "") -> str:
    """Write a 2-3 sentence professional summary from the resume's real facts."""
    facts = _resume_facts(resume)
    if not facts.strip():
        raise AIError("Add some experience, projects, or skills first -- there's nothing to summarize yet.")
    prompt = (
        "Write a professional resume summary of 2-3 sentences for this candidate, "
        "using ONLY the facts below. Do not invent anything. Write in first-person "
        "implied style (no 'I'), suitable for the top of a resume.\n\n"
        f"CANDIDATE FACTS:\n{facts}" + _jd_clause(jd)
    )
    return _generate(prompt, temperature=0.5)


def rewrite_bullets(job_title: str, company: str, bullets: List[str], jd: str = "") -> List[str]:
    """Rewrite existing experience bullets, preserving count and all facts."""
    if not bullets:
        raise AIError("This role has no bullet points to improve yet.")
    numbered = "\n".join(f"{i + 1}. {b}" for i, b in enumerate(bullets))
    role = " at ".join(p for p in [job_title, company] if p) or "this role"
    prompt = (
        f"Rewrite each of the following {len(bullets)} resume bullet points for {role} "
        "to be more concise and impactful, starting each with a strong action verb. "
        "Keep every factual detail (tools, numbers, outcomes) exactly as given -- add "
        "nothing new. Return exactly one rewritten bullet per line, no numbering, no "
        "blank lines.\n\n"
        f"BULLETS:\n{numbered}" + _jd_clause(jd)
    )
    result = _parse_bullets(_generate(prompt))
    return result or bullets


def enhance_project(
    name: str, description: str, technologies: List[str], bullets: List[str], jd: str = ""
) -> Tuple[str, List[str]]:
    """Improve a project's description and bullets. Returns (description, bullets)."""
    if not (description.strip() or bullets):
        raise AIError("Add a project description or some bullet points first.")
    tech = ", ".join(technologies)
    existing_bullets = "\n".join(f"- {b}" for b in bullets) if bullets else "(none)"
    prompt = (
        f"Improve the wording of this project for a resume. Project name: {name}. "
        f"Technologies: {tech or 'not specified'}.\n"
        f"Current description: {description or '(none)'}\n"
        f"Current bullet points:\n{existing_bullets}\n\n"
        "Keep all facts exactly; only improve clarity and impact, ATS-friendly. "
        "Respond in EXACTLY this format:\n"
        "DESCRIPTION: <improved one-sentence description>\n"
        "BULLETS:\n"
        "- <improved bullet>\n"
        "- <improved bullet>\n"
        "(Include a BULLETS section only if bullet points were provided.)"
        + _jd_clause(jd)
    )
    text = _generate(prompt)

    new_description = description
    new_bullets: List[str] = []
    in_bullets = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("DESCRIPTION:"):
            new_description = stripped.split(":", 1)[1].strip() or description
        elif stripped.upper().startswith("BULLETS"):
            in_bullets = True
        elif in_bullets:
            cleaned = stripped.lstrip("-*•").strip()
            if cleaned:
                new_bullets.append(cleaned)
    return new_description, (new_bullets or bullets)


def tailor_bullets(pairs: List[Tuple[str, str]]) -> List[str]:
    """Rewrite existing bullets to emphasise the JD aspect each one supports.

    This is the LLM half of the evidence-based Writer agent. `pairs` is a list
    of (original_bullet, jd_requirement): the retriever has already decided
    *which* resume bullet best answers *which* JD requirement, so the model's
    only job is to rephrase that bullet to foreground the relevant angle --
    never to invent skills, tools, or metrics the bullet doesn't already state.

    Returns one rewrite per input, in the same order (so the caller can keep
    each rewrite bound to its evidence). On any parsing/count mismatch it falls
    back to the original bullets, so a bad response degrades to a no-op rather
    than mis-attributing changes.
    """
    if not pairs:
        return []
    originals = [p[0] for p in pairs]
    lines = []
    for i, (bullet, requirement) in enumerate(pairs, 1):
        emphasis = requirement.strip() or "general relevance to the role"
        lines.append(f"{i}. ORIGINAL: {bullet.strip()} || EMPHASIS (from job description): {emphasis}")
    numbered = "\n".join(lines)
    prompt = (
        f"Rewrite each of the following {len(pairs)} resume bullet points so it better "
        "emphasises the EMPHASIS aspect drawn from the target job description, while "
        "keeping every factual detail (tools, numbers, outcomes, technologies) exactly "
        "as written in the ORIGINAL. Do NOT add any skill, tool, metric, or achievement "
        "that is not already in the ORIGINAL -- if the emphasis asks for something the "
        "bullet doesn't contain, just improve the wording without inventing it. Start "
        "each rewrite with a strong action verb.\n\n"
        "Return EXACTLY one rewritten bullet per line, numbered 1 to "
        f"{len(pairs)} in the same order, with no other text.\n\n"
        f"ITEMS:\n{numbered}"
    )
    text = _generate(prompt, temperature=0.4)
    parsed = _parse_numbered(text, len(pairs))
    if parsed is None:
        # Last resort: try a loose bullet parse; only use it if the count lines up.
        loose = _parse_bullets(text)
        parsed = loose if len(loose) == len(pairs) else originals
    return parsed


def write_cover_letter(
    *,
    facts: str,
    highlights: List[Tuple[str, str]],
    covered_skills: List[str],
    forbidden_skills: List[str],
    company: str,
    role: str,
    candidate_name: str,
    min_words: int = 250,
    max_words: int = 350,
    corrections: str = "",
) -> str:
    """The single LLM call behind the cover letter. Returns the raw letter text.

    Everything the model sees has already been assembled and vetted by
    utils.cover_letter: `facts` is the résumé's own content, `highlights` are the
    (job requirement, supporting résumé bullet) pairs the retriever judged
    strongest, `covered_skills` are skills the résumé genuinely evidences, and
    `forbidden_skills` are the ones the job wants but the résumé does not show --
    passed in explicitly so the model is told what it must not claim.

    `corrections` carries the verifier's complaints on a second attempt (an
    unsupported claim that slipped through, or a word count out of range).
    """
    if not facts.strip():
        raise AIError("Add your experience, projects, or skills first -- there's nothing to write from yet.")

    company = company.strip()
    role = role.strip()
    target = role or "the advertised role"

    evidence_block = "\n".join(
        f"{i}. THE JOB ASKS: {req}\n   RÉSUMÉ EVIDENCE: {bullet}"
        for i, (req, bullet) in enumerate(highlights, 1)
    ) or "(no requirement-level matches were retrieved -- rely on the résumé facts below)"

    if company:
        addressing = (
            f"Name the role ({role or 'the advertised role'}) and the company ({company}) "
            "in the opening. Address it 'Dear Hiring Manager' unless a named contact "
            "appears in the job description."
        )
    else:
        addressing = (
            "The company name is not known, so do NOT guess or name one. Address the "
            "letter 'Dear Hiring Manager' and refer to 'this role'."
        )

    prompt = (
        f"Write a cover letter for {candidate_name or 'this candidate'} applying for {target}"
        f"{f' at {company}' if company else ''}.\n\n"
        f"{addressing}\n\n"
        f"Length: {min_words}-{max_words} words. Shape it as a brief opening, two or three "
        "body paragraphs built on the strongest matches below, and a short close. End with "
        "a sign-off ('Sincerely,') and "
        f"{'the name ' + candidate_name if candidate_name else 'the candidate name'} on its own line.\n\n"
        "Build the letter around the STRONGEST MATCHES: each one pairs something this job "
        "asks for with the résumé evidence that actually supports it. Turn those into "
        "natural explanations of fit -- do not quote the bullets verbatim and do not walk "
        "through the résumé section by section.\n\n"
        f"STRONGEST MATCHES:\n{evidence_block}\n\n"
        f"SKILLS THE RÉSUMÉ GENUINELY EVIDENCES (safe to mention):\n"
        f"{', '.join(covered_skills) if covered_skills else '(none identified)'}\n\n"
        "SKILLS THIS JOB WANTS BUT THE RÉSUMÉ DOES NOT SHOW -- never claim, imply, or "
        "reference these as the candidate's own, and do not promise to learn them:\n"
        f"{', '.join(forbidden_skills) if forbidden_skills else '(none)'}\n\n"
        f"RÉSUMÉ FACTS (the only permitted source of claims):\n{facts}"
    )
    if corrections.strip():
        prompt += (
            "\n\nA previous draft was rejected by a verifier. Fix exactly these problems "
            f"and change nothing else about the approach:\n{corrections.strip()}"
        )

    return _generate(prompt, temperature=0.5, system=_COVER_LETTER_SYSTEM)


def suggest_improvements(resume: ResumeData, jd: str = "") -> str:
    """Return read-only, actionable suggestions to improve the whole resume."""
    facts = _resume_facts(resume)
    if not facts.strip():
        raise AIError("Fill in some resume sections first, then ask for suggestions.")
    prompt = (
        "Review this resume and give specific, actionable suggestions to improve it "
        "(structure, wording, quantification, ATS-friendliness, and any gaps). Do NOT "
        "rewrite it or invent content -- give advice as a short markdown bullet list. "
        "Where you suggest adding a metric or skill, phrase it as 'consider adding X "
        "IF you have it'.\n\n"
        f"RESUME:\n{facts}" + _jd_clause(jd)
    )
    return _generate(prompt, temperature=0.4)
