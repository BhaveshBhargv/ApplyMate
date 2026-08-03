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
from typing import List, Optional, Tuple

import streamlit as st

from models.resume_data import ResumeData

# Leading "1. " / "2) " style list numbering to strip from model output.
_LIST_NUMBER_RE = re.compile(r"^\d+[.)]\s+")

_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"  # Hunyuan 3 free tier on OpenRouter; override with OPENROUTER_MODEL

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


def is_configured() -> bool:
    """True if an OpenRouter API key is available, so AI features can run."""
    return bool(_api_key())


def render_unavailable_notice() -> None:
    """Shown in place of AI controls when no API key is configured."""
    st.info(
        "**AI features need an OpenRouter API key** (free with the "
        "`tencent/hy3:free` model).\n\n"
        "1. Get one at https://openrouter.ai/keys.\n"
        "2. **Locally:** add it to `.streamlit/secrets.toml` as "
        "`OPENROUTER_API_KEY = \"your-key\"` (this file is git-ignored).\n"
        "3. **On Streamlit Cloud:** add `OPENROUTER_API_KEY` under the app's "
        "**Settings → Secrets**.\n\n"
        "Then reload this page. The AI never fabricates content -- it only "
        "rewrites what you've already entered."
    )


# --- Core call -----------------------------------------------------------------

def _generate(prompt: str, temperature: float = 0.4) -> str:
    """Send one prompt to the model (OpenRouter) and return the trimmed text."""
    key = _api_key()
    if not key:
        raise AIError("No OpenRouter API key configured.")

    # Imported lazily so a missing package never breaks unrelated pages.
    from openai import OpenAI

    try:
        client = OpenAI(
            api_key=key,
            base_url=_base_url(),
            default_headers={"X-Title": "AI Resume Builder"},  # optional OpenRouter attribution
        )
        response = client.chat.completions.create(
            model=_model_name(),
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            # hy3 is a reasoning model: it spends tokens "thinking" before it
            # writes the answer, and those thinking tokens count against the
            # budget. A small cap gets fully consumed by reasoning, leaving no
            # room for the answer (empty content, finish_reason="length").
            # So: a large total budget, plus a hard cap on reasoning tokens so
            # the bulk is always left for the actual answer.
            max_tokens=8000,
            extra_body={"reasoning": {"max_tokens": 1200}},
        )
    except Exception as exc:  # noqa: BLE001 -- surface any API problem to the caller
        raise AIError(str(exc)) from exc

    if not response.choices:
        raise AIError("The model returned no choices. Try again.")
    choice = response.choices[0]
    text = (choice.message.content or "").strip()
    if not text:
        if getattr(choice, "finish_reason", None) == "length":
            raise AIError("The response was cut off before any text was produced. Try again.")
        raise AIError(
            "The model returned an empty response. Try again -- if it keeps happening, "
            "the free tier may be busy; set OPENROUTER_MODEL to 'tencent/hy3'."
        )
    return text


def _parse_bullets(text: str) -> List[str]:
    """Split a model response into clean bullet lines (drops markers/numbering)."""
    bullets: List[str] = []
    for line in text.splitlines():
        cleaned = line.strip().lstrip("-*•").strip()
        cleaned = _LIST_NUMBER_RE.sub("", cleaned)
        if cleaned:
            bullets.append(cleaned)
    return bullets


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
