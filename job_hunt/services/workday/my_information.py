"""Workday 'My Information' step (step 1 of 5) — pure helpers.

This module currently exposes one pure function used by the My Information
step in ``_fill_workday_current_step``: a check for whether a "required and
empty" field on the page actually blocks the user from continuing.

Workday's required-field detector is over-eager. After our fill pass clears
visible blanks, the only labels that should still block the Save and Continue
click are the Verify-New-Password pair (which we never touch because the
password comes from a one-time secret file — see ``services.workday.login``).

Keeping this here, rather than inline in ``cli.py``, lets future expansions
of the My Information step (address validation, phone-code re-selection
retry logic, etc.) land in one place without growing the cli.py footprint.
The cli.py wrapper (`_workday_required_blocks_my_information_continue`)
remains for back-compat with existing call sites.
"""

from __future__ import annotations

import re


_CONTINUE_BLOCKING_TOKENS = (
    "password",
    "verify new password",
)


def required_blocks_my_information_continue(label: str) -> bool:
    """Return True when ``label`` is a required-empty field that *actually*
    prevents the operator from clicking 'Save and Continue'.

    Other required-empty labels are typically false positives caused by
    Workday's detector firing on fields the operator never asked us to fill
    (e.g. optional reference contacts, alternate addresses).
    """
    norm = re.sub(r"\s+", " ", label.lower())
    return any(token in norm for token in _CONTINUE_BLOCKING_TOKENS)
