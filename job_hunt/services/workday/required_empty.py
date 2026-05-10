"""Pure-Python helpers for filtering Workday "required but empty" / "skip" lists.

Workday emits two false-positive patterns that the live ``_required_empty_fields``
DOM scraper cannot avoid by itself:

1. **Date helper text**: a correctly-filled date widget surfaces a label like
   ``From* current value is 1/2026 01 / 2026 To* current value is 3/2026 03 / 2026``
   in ``inner_text``. The label looks "empty" but is actually the tooltip /
   helper for a satisfied field.

2. **Country Phone Code chip**: the chip-style combobox keeps its underlying
   ``<input>.value`` empty even when a chip is visually selected. ``required``
   detection therefore reports it as empty whenever auto-fill went through the
   Workday dropdown helpers (which set the chip but not the input value).

Phase 2.3 extracted this logic out of ``cli.py`` and generalised the date helper
matcher (previously hardcoded to ``"2026"`` / ``"3/2026"``). Both filters are
pure-Python and unit-tested via ``tests/test_required_empty_filter.py``.
"""

from __future__ import annotations

import re

# Matches the Workday date helper signature regardless of the year/month value.
# Example matches:
#   "From* current value is 1/2026 01 / 2026 To* current value is 3/2026 03 / 2026"
#   "From current value is 12/2024 To current value is 6/2025"
_WORKDAY_DATE_HELPER_RE = re.compile(
    r"\bfrom\b.*\bcurrent\s+value\s+is\b.*\bto\b.*\bcurrent\s+value\s+is\b",
    re.IGNORECASE | re.DOTALL,
)
_WORKDAY_DATE_VALUE_RE = re.compile(r"\b\d{1,2}\s*/\s*\d{4}\b")


def is_workday_date_helper(label: str) -> bool:
    """``True`` for ``From* current value is M/YYYY ... To* current value is M/YYYY``-style helper text."""
    if not label:
        return False
    if not _WORKDAY_DATE_HELPER_RE.search(label):
        return False
    # require at least one M/YYYY value so we don't match plain "from / to" prose
    return bool(_WORKDAY_DATE_VALUE_RE.search(label))


def is_country_phone_code_label(label_norm: str) -> bool:
    return "country phone code" in label_norm


def country_phone_code_was_filled(filled_norm: list[str]) -> bool:
    return any("country phone code" in item for item in filled_norm)


def dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def filter_non_blocking_workday_skips(items: list[str]) -> list[str]:
    """Drop helper-text entries that the user/agent should not see in ``Needs Review``."""
    return dedupe_preserve_order([item for item in items if not is_workday_date_helper(item)])


def filter_required_empty_fields(required_empty: list[str], filled: list[str]) -> list[str]:
    """Drop required-empty entries that we actually filled or that are helper text.

    This runs after the DOM scraper to clean up the ``required_empty`` list
    before we surface it to the user. Three drop reasons:

    1. label is a Workday date helper (``is_workday_date_helper``)
    2. label is the Country Phone Code chip and a country-phone-code chip was filled
    3. generic substring overlap between the required-empty label and any filled entry
    """
    filled_norm = [item.lower() for item in filled]
    chip_filled = country_phone_code_was_filled(filled_norm)
    out: list[str] = []
    for label in required_empty:
        if is_workday_date_helper(label):
            continue
        label_norm = label.lower()
        if is_country_phone_code_label(label_norm) and chip_filled:
            continue
        if any(item in label_norm or label_norm in item for item in filled_norm):
            continue
        out.append(label)
    return out
