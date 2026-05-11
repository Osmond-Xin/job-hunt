"""eligibility_gate node — binary mode-vs-JD compatibility check.

Lives between ``verify_active`` and ``classify_archetype`` in the evaluate
graph. Classifies the JD as ``"student"`` (intern / co-op posting),
``"full"`` (full-time posting), or ``"unknown"`` (insufficient signal).

Routing rules (handled by ``_route_eligibility`` in
``graphs/evaluate_job.py``):

- classification == "unknown" → pass through to ``classify_archetype``;
  let the scorer handle ambiguity rather than silently dropping work.
- classification == active mode → pass through.
- classification != active mode → route to ``mark_ineligible`` and end
  the graph; recommendation is forced to ``skip``.

This is intentionally a heuristic, not an LLM call — the gate runs
before the first LLM-driven node (``classify_archetype``) and adding a
model call here would slow every single evaluate. Heuristics target the
title first, then a small JD-prefix scan; both are very high-precision
for the cases that actually need to be blocked (e.g. "Senior X" while
in student mode).
"""

from __future__ import annotations

import re
from typing import Literal

from langchain_core.runnables import RunnableConfig

from job_hunt.models.state import JobHuntState
from job_hunt.services.profile_loader import current_mode

JdEligibility = Literal["student", "full", "unknown"]

# Title tokens that mark a posting as student-only. Word boundaries matter
# here — "internal" must not match "intern". Each entry is a regex with
# explicit word boundaries.
_STUDENT_TITLE_PATTERNS = [
    r"\binterns?\b",
    r"\binternships?\b",
    r"\bco[-\s]?ops?\b",
    r"\bco[-\s]?operative\b",
    r"\btrainees?\b",
    r"\bapprentices?\b",
    r"\bstudents?\b",
    r"\bcoop\b",
]

# Title tokens that mark a posting as full-time-only. Tuned to keep false
# positives low: "Lead Engineer" is FT, but "Lead a team of interns" is rare
# and the title scan stops at the title field, not the JD body.
_FULL_TITLE_PATTERNS = [
    r"\bsenior\b",
    r"\bstaff\b",
    r"\bprincipal\b",
    r"\bdirectors?\b",
    r"\bvp\b",
    r"\bvice\s+president\b",
    r"\bhead\s+of\b",
    r"\bmanagers?\b",
    r"\bchief\b",
    r"\bleads?\b",  # "Lead Data Scientist" / "Engineering Lead"
]

# Phrases inside the JD body (first ~600 chars) that confirm a co-op /
# internship framing even when the title is generic ("Engineer").
_STUDENT_BODY_PATTERNS = [
    r"\binternship\s+program\b",
    r"\bco[-\s]?op\s+(?:term|student|placement|position)\b",
    r"\bsummer\s+(?:intern|internship|co[-\s]?op)\b",
    r"\b(?:fall|winter|spring)\s+(?:intern|internship|co[-\s]?op)\b",
    r"\bcurrently\s+enrolled\b",
    r"\bmust\s+be\s+(?:a|an)\s+(?:current|enrolled)\s+student\b",
]

_BODY_SCAN_CHARS = 600


def classify_jd_eligibility(title: str, jd_text: str) -> JdEligibility:
    """Pure classifier — title first, body second, default ``"unknown"``."""
    title_norm = (title or "").strip().lower()
    body_prefix = (jd_text or "")[:_BODY_SCAN_CHARS].lower()

    if any(re.search(pat, title_norm) for pat in _STUDENT_TITLE_PATTERNS):
        return "student"
    if any(re.search(pat, body_prefix) for pat in _STUDENT_BODY_PATTERNS):
        return "student"
    if any(re.search(pat, title_norm) for pat in _FULL_TITLE_PATTERNS):
        return "full"
    return "unknown"


async def eligibility_gate(state: JobHuntState, config: RunnableConfig) -> dict:
    """Classify the JD against the active mode and stash both on state."""
    mode = current_mode()
    jd_meta = state.get("jd_meta")
    title = (jd_meta.title if jd_meta is not None else "") or ""
    classification = classify_jd_eligibility(title, state.get("jd_text", ""))
    return {
        "mode": mode,
        "jd_eligibility": classification,
        "errors": [],
    }


async def mark_ineligible(state: JobHuntState, config: RunnableConfig) -> dict:
    """Short-circuit when the JD's eligibility class does not match the mode."""
    mode = state.get("mode", "full")
    classification = state.get("jd_eligibility", "unknown")
    jd_meta = state.get("jd_meta")
    title = jd_meta.title if jd_meta is not None else "this posting"
    return {
        "recommendation": "skip",
        "errors": [
            f"Mode={mode} blocks {classification}-eligibility role: {title}. "
            "Flip mode in profile/profile.yml to evaluate."
        ],
    }
