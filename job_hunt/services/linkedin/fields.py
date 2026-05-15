"""Pure field strategy helpers for LinkedIn Easy Apply.

Each helper is a small, deterministic function that maps a raw label or
question into an answer the dispatcher can attempt. None of these touch
Playwright — the live dispatcher in :mod:`easy_apply` calls these and then
applies the result to the page.
"""

from __future__ import annotations

import re


# Canonical field types the dispatcher recognises. Anything else falls
# through to the generic question-answer pipeline (and otherwise to
# `skipped` so the user can hand-fill).
FIELD_EMAIL = "email"
FIELD_PHONE = "phone"
FIELD_PHONE_COUNTRY = "phone_country"
FIELD_LINKEDIN = "linkedin"
FIELD_WEBSITE = "website"
FIELD_LOCATION = "location"
FIELD_FULL_NAME = "full_name"
FIELD_FIRST_NAME = "first_name"
FIELD_LAST_NAME = "last_name"


_LABEL_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\s*email( address)?\s*$", re.I), FIELD_EMAIL),
    (re.compile(r"phone\s*country\s*code", re.I), FIELD_PHONE_COUNTRY),
    (re.compile(r"country\s*code", re.I), FIELD_PHONE_COUNTRY),
    (re.compile(r"^\s*mobile\s*phone( number)?\s*$", re.I), FIELD_PHONE),
    (re.compile(r"^\s*phone( number)?\s*$", re.I), FIELD_PHONE),
    (re.compile(r"linkedin\s*(profile|url)?", re.I), FIELD_LINKEDIN),
    (re.compile(r"website|portfolio|personal\s*url", re.I), FIELD_WEBSITE),
    (re.compile(r"city|location", re.I), FIELD_LOCATION),
    (re.compile(r"^\s*first\s*name\s*$", re.I), FIELD_FIRST_NAME),
    (re.compile(r"^\s*last\s*name\s*$", re.I), FIELD_LAST_NAME),
    (re.compile(r"^\s*(full\s*)?name\s*$", re.I), FIELD_FULL_NAME),
)


def classify_label(label: str) -> str:
    """Map a raw field label to one of the ``FIELD_*`` constants, or ""."""
    if not label:
        return ""
    for pattern, kind in _LABEL_RULES:
        if pattern.search(label):
            return kind
    return ""


_YES_PATTERNS = (
    re.compile(r"authoriz(ed|ation) to work", re.I),
    re.compile(r"legally (allowed|eligible) to work", re.I),
    re.compile(r"right to work", re.I),
    re.compile(r"willing to (relocate|commute)", re.I),
    re.compile(r"able to (commute|relocate)", re.I),
    re.compile(r"agree to (the )?(terms|privacy)", re.I),
    re.compile(r"consent to", re.I),
)

_NO_PATTERNS = (
    re.compile(r"require? (visa|sponsorship)", re.I),
    re.compile(r"need (visa|sponsorship)", re.I),
    re.compile(r"sponsor(ship)? required", re.I),
    re.compile(r"convicted of a (felony|crime)", re.I),
    re.compile(r"have you ever been (terminated|fired|dismissed)", re.I),
)


def yes_no_answer(question: str) -> str:
    """Return ``"Yes"`` / ``"No"`` / ``""`` for a Yes-No question.

    Only fires on the conservative rule-set above. Anything ambiguous returns
    "" so the dispatcher falls back to ``skipped`` and the user resolves it.
    The patterns are deliberately specific to high-frequency LinkedIn Easy
    Apply prompts; do not over-extend without an incident to point at.
    """
    if not question:
        return ""
    for pattern in _YES_PATTERNS:
        if pattern.search(question):
            return "Yes"
    for pattern in _NO_PATTERNS:
        if pattern.search(question):
            return "No"
    return ""


_YEARS_RE = re.compile(
    r"how many years.*(experience|using|with)\b|years? of experience", re.I
)


def years_of_experience_answer(question: str, default: str = "2") -> str:
    """Return a conservative numeric answer for years-of-experience prompts.

    LinkedIn Easy Apply ships a *lot* of "How many years of experience do you
    have with X?" prompts. We can't know the user's exact answer for every X,
    so we return a single conservative default ("2"). The dispatcher only
    invokes this when the question matches the years-of-experience shape.
    """
    if not question:
        return ""
    if not _YEARS_RE.search(question):
        return ""
    digits = re.sub(r"\D", "", default) or "2"
    return digits


def country_code_best_match(target: str, options: list[str]) -> str:
    """Return the dropdown option that best matches ``target`` (e.g. "Canada").

    LinkedIn's phone-country dropdown lists entries like ``"Canada (+1)"`` /
    ``"United States (+1)"``. Match strategy:

    1. exact (case-insensitive) match on the country name.
    2. prefix match — ``target`` appears at the start of an option.
    3. substring match — ``target`` appears anywhere in an option.

    Returns "" when nothing matches so the caller can record a skip.
    """
    if not target or not options:
        return ""
    needle = target.strip().lower()
    if not needle:
        return ""
    cleaned = [opt for opt in options if isinstance(opt, str) and opt.strip()]
    for opt in cleaned:
        if opt.strip().lower() == needle:
            return opt
    for opt in cleaned:
        if opt.strip().lower().startswith(needle):
            return opt
    for opt in cleaned:
        if needle in opt.lower():
            return opt
    return ""
