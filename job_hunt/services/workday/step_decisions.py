"""Which Workday step a page is on, decided without a page.

An **internal seam** (docs/apply-seam-plan.md §3.7, Phase 3b): these functions
are private to the Workday driver's implementation and exist so its own tests can
reach the decisions without a browser. They are not part of the driver's
interface -- ``apply_session`` talks to ``fill``/``submit`` and must never import
this module. Reaching past the interface is how a seam stops being one.

The split preserves one thing that is easy to lose: the original read the page
body *only* when no heading matched. Reading both up front would change what
happens when the body read times out on a page whose heading already answered
the question. So the decision is two functions, and the async shell decides
whether the second read is needed at all.
"""

from __future__ import annotations

import re


# The step names Workday renders as headings. Order is not significant for
# matching -- both passes below check membership, not sequence -- but this is
# the order the applicant walks them in, which is why it is written this way.
KNOWN_STEPS = [
    "My Information",
    "My Experience",
    "Application Questions",
    "Voluntary Disclosures",
    "Review",
    "Create Account",
]


def step_from_headings(headings: list[str]) -> str:
    """The step named by an exact heading match, or "" if none names one.

    Exact after whitespace collapsing, deliberately: Workday renders section
    labels inside headings too ("Review your application"), and a substring test
    would call that the Review step while the applicant is somewhere else.
    """
    for heading in headings:
        normalized = re.sub(r"\s+", " ", heading).strip()
        for step in KNOWN_STEPS:
            if normalized == step:
                return step
    return ""


def step_from_body_text(text: str) -> str:
    """The step named by a whole line of the page body, or "" if none is.

    The fallback for tenants that style their step titles as something other
    than a heading element. Anchored to line boundaries so prose mentioning
    "Review" mid-sentence does not count.
    """
    for step in KNOWN_STEPS:
        if re.search(rf"(?:^|\n){re.escape(step)}(?:\n|$)", text):
            return step
    return ""
