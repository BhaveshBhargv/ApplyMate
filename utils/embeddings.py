"""Sentence-embedding client for the ATS matcher and RAG retrieval.

The ATS page no longer scores by keyword overlap; it compares *meaning* using
sentence embeddings (so "React" in a resume matches "ReactJS" in a JD). The AI
Writer no longer sees the whole resume + JD either -- a retriever embeds each
resume unit once and passes only the most relevant snippets into the prompt.

Both of those need one thing: turn text into vectors. This module is that one
thing. It calls a hosted embeddings API over HTTP, so the app stays light -- no
torch, no local model, fast cold starts on Streamlit Cloud's free tier.

It is **provider-neutral**. Any embeddings endpoint that speaks the OpenAI
`/embeddings` shape works by setting three secrets -- key, base URL, model --
and the default is **Google Gemini's free tier** (`text-embedding-004` via
Gemini's OpenAI-compatible endpoint), which needs only a no-cost Google AI
Studio key (no billing card). Swap to Jina, OpenAI, or a local server by
changing `EMBEDDINGS_BASE_URL` / `EMBEDDINGS_MODEL` -- no code change.

The key is a separate secret from the chat model's (`OPENROUTER_API_KEY`),
because OpenRouter does not serve embeddings. Read as `EMBEDDINGS_API_KEY`,
falling back to `GEMINI_API_KEY` / `OPENAI_API_KEY` so an existing key of
either kind still works.

Everything degrades gracefully: with no key, `is_configured()` is False and the
callers fall back to lexical matching instead of crashing. Embeddings are
cached in-process (keyed by model + text hash) so re-running an analysis on the
same resume/JD is instant and free.
"""
from __future__ import annotations

import hashlib
import math
import os
import threading
from typing import Dict, List, Optional, Sequence

import streamlit as st

# Default: Google Gemini's free tier via its OpenAI-compatible endpoint. A free
# Google AI Studio key (no billing) drives real semantic embeddings; override
# either value to point at Jina, OpenAI, or a local server.
_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
_DEFAULT_MODEL = "text-embedding-004"  # Gemini free tier; override with EMBEDDINGS_MODEL

# The API accepts many inputs per request; keep batches modest so one oversized
# call never fails the whole analysis. Gemini's OpenAI-compat embeddings cap the
# batch smaller than OpenAI does, so stay conservative.
_BATCH_SIZE = 64

# Process-wide embedding cache: (model, sha1(text)) -> vector. Embeddings are
# deterministic for a given text+model, so caching is always safe and makes a
# repeat analysis of the same resume/JD free. Guarded because Streamlit reruns
# and background threads can touch it concurrently.
_CACHE: Dict[str, List[float]] = {}
_CACHE_LOCK = threading.Lock()


class EmbeddingError(Exception):
    """Raised for any embedding problem (no key, API error, empty result)."""


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
    # Provider-neutral name first, then common provider-specific names so an
    # existing Gemini or OpenAI key already in secrets just works.
    return (
        _get_secret("EMBEDDINGS_API_KEY")
        or _get_secret("GEMINI_API_KEY")
        or _get_secret("OPENAI_API_KEY")
    )


def _model_name() -> str:
    return _get_secret("EMBEDDINGS_MODEL") or _DEFAULT_MODEL


def _base_url() -> str:
    return _get_secret("EMBEDDINGS_BASE_URL") or _DEFAULT_BASE_URL


def is_configured() -> bool:
    """True if an embeddings API key is available, so semantic features run."""
    return bool(_api_key())


def render_unavailable_notice() -> None:
    """Shown in place of embedding-backed controls when no key is configured."""
    st.info(
        "**Semantic matching needs a (free) embeddings key.** The ATS analysis "
        "and AI tailoring compare *meaning*, not just keywords.\n\n"
        "The default is **Google Gemini's free tier** -- no billing card:\n"
        "1. Get a free key at https://aistudio.google.com/apikey.\n"
        "2. **Locally:** add it to `.streamlit/secrets.toml` as "
        "`EMBEDDINGS_API_KEY = \"your-key\"` (this file is git-ignored).\n"
        "3. **On Streamlit Cloud:** add `EMBEDDINGS_API_KEY` under the app's "
        "**Settings -> Secrets**.\n\n"
        "Any OpenAI-compatible embeddings provider works too -- set "
        "`EMBEDDINGS_BASE_URL` and `EMBEDDINGS_MODEL` to point elsewhere."
    )


# --- Core embedding ------------------------------------------------------------

def _cache_key(model: str, text: str) -> str:
    return f"{model}:{hashlib.sha1(text.encode('utf-8')).hexdigest()}"


def embed(texts: Sequence[str], *, api_key: Optional[str] = None) -> List[List[float]]:
    """Embed a list of texts, returning one vector per input (order preserved).

    Results are cached per (model, text); only cache misses hit the API, and
    those are sent in batches. Blank inputs get a zero vector without an API
    call (their similarity to anything is 0, which is the honest answer).

    `api_key` can be passed explicitly so this is safe to call from a worker
    thread -- resolve the key on the main thread and hand it in, since reading
    st.secrets off-thread is unreliable. When omitted, the key is resolved here.
    """
    items = [t if isinstance(t, str) else "" for t in texts]
    if not items:
        return []

    model = _model_name()
    results: List[Optional[List[float]]] = [None] * len(items)

    # Figure out which texts we still need to fetch (non-blank + not cached).
    to_fetch: List[str] = []
    fetch_positions: Dict[str, List[int]] = {}
    with _CACHE_LOCK:
        for i, text in enumerate(items):
            if not text.strip():
                results[i] = []  # zero-length vector => similarity 0 everywhere
                continue
            ck = _cache_key(model, text)
            cached = _CACHE.get(ck)
            if cached is not None:
                results[i] = cached
            else:
                if text not in fetch_positions:
                    fetch_positions[text] = []
                    to_fetch.append(text)
                fetch_positions[text].append(i)

    if to_fetch:
        key = api_key or _api_key()
        if not key:
            raise EmbeddingError("No embeddings API key configured.")
        fetched = _fetch_embeddings(to_fetch, key=key, model=model)
        with _CACHE_LOCK:
            for text, vector in zip(to_fetch, fetched):
                _CACHE[_cache_key(model, text)] = vector
                for pos in fetch_positions[text]:
                    results[pos] = vector

    # Any remaining None would be a bug; coerce defensively to a zero vector.
    return [r if r is not None else [] for r in results]


def embed_one(text: str, *, api_key: Optional[str] = None) -> List[float]:
    """Convenience wrapper: embed a single string."""
    return embed([text], api_key=api_key)[0]


def _fetch_embeddings(texts: List[str], *, key: str, model: str) -> List[List[float]]:
    """Call the embeddings API for a list of (non-blank) texts, in batches."""
    from openai import OpenAI

    client = OpenAI(api_key=key, base_url=_base_url())
    vectors: List[List[float]] = []
    for start in range(0, len(texts), _BATCH_SIZE):
        batch = texts[start : start + _BATCH_SIZE]
        try:
            response = client.embeddings.create(model=model, input=batch)
        except Exception as exc:  # noqa: BLE001 -- surface any API problem to caller
            raise EmbeddingError(str(exc)) from exc
        # OpenAI returns an `index` on each item; sort by it to be safe. Some
        # OpenAI-compatible providers (e.g. Gemini) omit it -- then fall back to
        # the response's own order, which these providers preserve.
        data = response.data
        if data and getattr(data[0], "index", None) is not None:
            data = sorted(data, key=lambda d: d.index)
        vectors.extend([list(d.embedding) for d in data])
    return vectors


# --- Vector math (pure Python; vectors are small and lists are short) ----------

def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two vectors, in [-1, 1]. 0 if either is empty."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def best_match(query: Sequence[float], candidates: Sequence[Sequence[float]]) -> tuple[int, float]:
    """Return (index, cosine) of the candidate most similar to `query`.

    Returns (-1, 0.0) when there are no candidates. Used to answer "which
    resume bullet best supports this JD requirement?" and similar questions.
    """
    best_i = -1
    best_score = 0.0
    for i, cand in enumerate(candidates):
        score = cosine(query, cand)
        if score > best_score or best_i == -1:
            best_i = i
            best_score = score
    return best_i, best_score
