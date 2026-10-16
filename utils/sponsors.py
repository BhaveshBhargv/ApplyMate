"""Visa-sponsorship signals for job listings (UK Skilled Worker route).

Two independent signals, both honest about what they can and can't prove:

  * LICENSED -- the employer is on the Home Office "Register of licensed sponsors: workers"
    (Skilled Worker route). That means the company is ALLOWED to sponsor; it doesn't mean this
    particular role is sponsored. The register is downloaded from GOV.UK (about 11 MB, ~140k
    organisations) and cached for a few hours per server process.
  * TEXT -- the listing itself says sponsorship is (or isn't) available.

Company names are matched after normalising (case, punctuation, "Ltd/Limited/PLC/UK" endings).
An exact normalised match is required, so a similar-looking name is never treated as a match.
When the register can't be fetched, `is_licensed` returns None (unknown) -- never a guess.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Iterable, Optional

import requests
import streamlit as st

_PAGE = "https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers"
_HEADERS = {"User-Agent": "ApplyMate/1.0 (+job search tool)"}
_CSV_RE = re.compile(r'href="(https://assets\.publishing\.service\.gov\.uk/[^"]+\.csv)"')
_ROUTES = ("skilled worker",)           # routes that count as "can sponsor a Skilled Worker visa"

_SUFFIXES = {"ltd", "limited", "plc", "llp", "llc", "inc", "incorporated", "corp", "corporation",
             "co", "company", "uk", "gb"}


def normalize_company(name: str) -> str:
    """Lowercase, '&' -> 'and', punctuation out, a leading 'the' and trailing legal/country
    words (Ltd, Limited, PLC, UK ...) dropped: 'The Acme Group (UK) Ltd.' -> 'acme group'."""
    text = (name or "").lower().replace("&", " and ")
    tokens = re.sub(r"[^a-z0-9]+", " ", text).split()
    if tokens and tokens[0] == "the":
        tokens = tokens[1:]
    stripped = list(tokens)
    while len(stripped) > 1 and stripped[-1] in _SUFFIXES:
        stripped.pop()
    return " ".join(stripped or tokens)


def _download_register() -> Optional[frozenset]:
    try:
        page = requests.get(_PAGE, headers=_HEADERS, timeout=20)
        page.raise_for_status()
        match = _CSV_RE.search(page.text)
        if not match:
            return None
        data = requests.get(match.group(1), headers=_HEADERS, timeout=90)
        data.raise_for_status()
    except requests.RequestException:
        return None
    names = set()
    reader = csv.DictReader(io.StringIO(data.content.decode("utf-8-sig", "replace")))
    for row in reader:
        if (row.get("Route") or "").strip().lower() in _ROUTES:
            key = normalize_company(row.get("Organisation Name") or "")
            if key:
                names.add(key)
    return frozenset(names) if names else None


@st.cache_resource(ttl=6 * 3600, show_spinner=False)
def _register() -> Optional[frozenset]:
    """The normalised licensed-sponsor names (None if GOV.UK couldn't be reached). Shared by
    every session in the process and refreshed every 6 hours."""
    return _download_register()


def register_available() -> bool:
    return _register() is not None


def is_licensed(company: str, register: Optional[frozenset] = None) -> Optional[bool]:
    """True/False if the register says so; None if the company is blank or the register is unavailable."""
    key = normalize_company(company)
    if not key:
        return None
    register = _register() if register is None else register
    if register is None:
        return None
    return key in register


_NEGATIVE = re.compile(
    r"\b(no|not|unable to|cannot|can't|can not|won't|will not|do not|don't|does not|doesn't|without)\b"
    r"[^.\n]{0,40}\b(visa )?sponsor", re.I)
_NEGATIVE_2 = re.compile(r"\bsponsorship\b[^.\n]{0,30}\b(is )?(not|unavailable|isn't)\b", re.I)
_POSITIVE = re.compile(
    r"\b(visa sponsorship|sponsorship (is )?(available|provided|offered|considered)|"
    r"(we|can|will|able to|happy to|willing to) (\w+ ){0,2}sponsor|skilled worker visa|"
    r"certificate of sponsorship|cos\b|sponsor(ing)? (a )?(work )?visa)", re.I)


def sponsorship_in_text(text: str) -> str:
    """'yes' if the listing says it offers visa sponsorship, 'no' if it says it doesn't, else ''.
    A negative statement wins over a positive one ('we cannot sponsor ... visa sponsorship ...')."""
    if not text:
        return ""
    if _NEGATIVE.search(text) or _NEGATIVE_2.search(text):
        return "no"
    if _POSITIVE.search(text):
        return "yes"
    return ""


def annotate(jobs: Iterable) -> None:
    """Fill `sponsor_licensed` and `sponsorship_text` on each JobPosting (in place)."""
    register = _register()
    for job in jobs:
        job.sponsor_licensed = is_licensed(job.company, register)
        job.sponsorship_text = sponsorship_in_text(f"{job.title}\n{job.description}")


# --- UK location test (for "UK jobs only") -----------------------------------------

# Explicit UK markers: enough on their own.
_UK_EXPLICIT = {"uk", "united kingdom", "great britain", "britain", "england", "scotland", "wales",
                "northern ireland"}
# Cities that are (almost) never anywhere else. Ambiguous names (York, Cambridge, Manchester,
# Birmingham, Bristol, Reading, Bath, Oxford, Newcastle ...) are deliberately NOT here: they only
# count when the listing also says UK/England/etc, so "New York City" or "Cambridge, MA" never match.
_UK_CITIES = {"london", "glasgow", "edinburgh", "leeds", "liverpool", "sheffield", "nottingham",
              "leicester", "cardiff", "belfast", "aberdeen", "southampton", "brighton", "milton keynes",
              "swindon", "coventry", "norwich"}
# Phrases containing a UK word that are not the UK.
_NOT_UK = ("new england", "new south wales", "prince of wales", "new wales")


def is_uk_location(location: str, trusted_uk_source: bool = False) -> bool:
    """True if the location text names the UK or a (non-ambiguous) UK city. `trusted_uk_source`
    is for results that came from a UK-restricted query (Adzuna with country=gb), whose locations
    are often just 'Slough, Berkshire' with no country word. A multi-location listing counts if
    any one of its locations is in the UK ('London; New York City')."""
    if trusted_uk_source:
        return True
    text = re.sub(r"[^a-z ]+", " ", (location or "").lower())
    text = re.sub(r" +", " ", text)
    for phrase in _NOT_UK:
        text = text.replace(phrase, " ")
    padded = f" {text} "
    return any(f" {w} " in padded for w in (_UK_EXPLICIT | _UK_CITIES))
