"""Month + Year dropdown pair for resume dates.

Resumes conventionally show "Aug 2019", not a specific day, so this uses two
st.selectbox widgets instead of st.date_input's full calendar -- it avoids
asking the user for a meaningless day-of-month and keeps stored dates in the
plain "Mon YYYY" format the rest of the app (and the future document
exporter) expects.
"""
from datetime import date
from typing import Optional, Tuple

import streamlit as st

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONTH_OPTIONS = ["Select month"] + _MONTHS

_CURRENT_YEAR = date.today().year
_YEAR_OPTIONS = ["Select year"] + [str(year) for year in range(_CURRENT_YEAR + 1, 1950, -1)]


def parse_month_year(value: str) -> Tuple[str, str]:
    """Parse a stored 'Mon YYYY' string back into (month, year) dropdown defaults."""
    parts = value.strip().split()
    if len(parts) == 2 and parts[0] in _MONTHS and parts[1].isdigit():
        return parts[0], parts[1]
    return "Select month", "Select year"


def month_year_input(value: str, key_prefix: str) -> str:
    """Render a Month + Year dropdown pair.

    Returns the combined "Mon YYYY" string, or "" if either dropdown is
    still on its placeholder option.
    """
    default_month, default_year = parse_month_year(value)

    # A stored year can fall outside the default dropdown range -- e.g. an
    # expected future graduation date ("Sep 2028") parsed from an uploaded
    # resume, or a very old date. Inject it so the value is preserved and
    # selectable rather than crashing list.index() on render.
    year_options = _YEAR_OPTIONS
    if default_year not in year_options:
        year_options = [_YEAR_OPTIONS[0], default_year] + _YEAR_OPTIONS[1:]
    month_index = _MONTH_OPTIONS.index(default_month) if default_month in _MONTH_OPTIONS else 0
    year_index = year_options.index(default_year) if default_year in year_options else 0

    col1, col2 = st.columns(2)
    with col1:
        month = st.selectbox(
            "Month",
            _MONTH_OPTIONS,
            index=month_index,
            key=f"{key_prefix}_month",
            label_visibility="collapsed",
        )
    with col2:
        year = st.selectbox(
            "Year",
            year_options,
            index=year_index,
            key=f"{key_prefix}_year",
            label_visibility="collapsed",
        )
    if month == "Select month" or year == "Select year":
        return ""
    return f"{month} {year}"


def month_year_to_ordinal(value: str) -> Optional[int]:
    """Convert a stored 'Mon YYYY' string into a comparable integer (year * 12 + month index).

    Returns None if the value is empty or unparseable, so callers can skip
    ordering checks when a date hasn't been fully selected.
    """
    month, year = parse_month_year(value)
    if month == "Select month" or year == "Select year":
        return None
    return int(year) * 12 + _MONTHS.index(month)


def is_start_after_end(start_value: str, end_value: str) -> bool:
    """True only if both dates are set and start comes strictly after end."""
    start_ord = month_year_to_ordinal(start_value)
    end_ord = month_year_to_ordinal(end_value)
    if start_ord is None or end_ord is None:
        return False
    return start_ord > end_ord
