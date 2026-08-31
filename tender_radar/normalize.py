"""Accent-stripping, term compiling and loose date/number parsing.

The scorer works on text that has been lower-cased and stripped of accents, so
"numérisation" and "numerisation" are the same string by the time we match. That
is why the keyword lists in config.yaml are written without accents.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Iterable, Optional

from dateutil import parser as dateparser

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s&']", re.UNICODE)


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalise_text(text: Optional[str]) -> str:
    """Lower-case, de-accent, collapse punctuation and whitespace."""
    if not text:
        return ""
    text = strip_accents(str(text)).lower()
    text = text.replace("’", "'").replace("`", "'")
    text = _PUNCT.sub(" ", text)
    return _WS.sub(" ", text).strip()


def compile_term(term: str) -> re.Pattern:
    """Turn a config term into a word-boundary regex.

    'demenag*'      -> matches demenagement, demenageur, ...
    'fine art'      -> matches the phrase, any run of whitespace between words
    'ff&e'          -> literal, & survives normalisation
    """
    term = normalise_text(term)
    wildcard = term.endswith("*")
    if wildcard:
        term = term[:-1]
    parts = [re.escape(p) for p in term.split(" ") if p]
    if not parts:
        return re.compile(r"(?!x)x")  # never matches
    body = r"\s+".join(parts)
    tail = r"\w*" if wildcard else ""
    return re.compile(rf"(?<!\w){body}{tail}(?!\w)")


def compile_terms(terms: Iterable[str]) -> list[tuple[str, re.Pattern]]:
    return [(t, compile_term(t)) for t in terms]


def parse_date(value) -> Optional[date]:
    if value in (None, "", "N/A"):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    # Common ISO-with-offset shapes first, they are the majority.
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[: len(fmt) + 2].strip(), fmt).date()
        except ValueError:
            pass
    try:
        return dateparser.parse(text, dayfirst=True, fuzzy=True).date()
    except (ValueError, OverflowError, TypeError):
        return None


_NUM = re.compile(r"-?\d+(?:[ .,]\d{3})*(?:[.,]\d+)?")


def parse_value(value) -> Optional[float]:
    """Parse '1 250 000,00 EUR', '1,250,000.00', 1250000 into a float."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = _NUM.search(str(value))
    if not m:
        return None
    raw = m.group(0).replace(" ", "")
    if "," in raw and "." in raw:
        # whichever separator is last is the decimal one
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        # a lone comma with 1-2 trailing digits is decimal, otherwise thousands
        raw = raw.replace(",", "." if len(raw.split(",")[-1]) in (1, 2) else "")
    try:
        return float(raw)
    except ValueError:
        return None


def first_present(record: dict, candidates: Iterable[str]):
    """Look up the first key from `candidates` that exists and is non-empty.

    Case-insensitive, so a provider renaming 'Country' to 'country' is harmless.
    """
    lowered = {str(k).strip().lower(): v for k, v in record.items()}
    for key in candidates:
        v = lowered.get(str(key).strip().lower())
        if v not in (None, ""):
            return v
    return None


def clean_html(text: Optional[str]) -> str:
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", " ", str(text), flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return _WS.sub(" ", text).strip()
