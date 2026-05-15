"""URL and DOM probes for LinkedIn job pages.

Pure functions only — the Playwright-aware variants live alongside in
:mod:`easy_apply` so this module stays trivial to unit-test.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse


_LINKEDIN_HOSTS = {"www.linkedin.com", "linkedin.com", "ca.linkedin.com"}

# Anchor on path so we don't false-positive on share/feed/profile links.
_JOB_PATH_RE = re.compile(
    r"^/(?:jobs/view/|jobs/collections/|jobs/search/|jobs/[^/]+/view/)", re.I
)

# Sign-in / checkpoint / 2FA URLs. If any of these is the page's host+path we
# treat the LinkedIn flow as "not logged in" and abort the apply.
_LOGIN_PATH_RE = re.compile(r"^/(?:login|checkpoint|uas/login)", re.I)


def is_linkedin_job_url(url: str) -> bool:
    """Return True when ``url`` points at a LinkedIn job posting.

    Accepts both ``linkedin.com/jobs/view/<id>`` and the collection / search
    variants that surface a job pane inline. Returns False for profile / feed
    / company pages so the apply router does not engage LinkedIn helpers there.
    """
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if host not in _LINKEDIN_HOSTS:
        return False
    return bool(_JOB_PATH_RE.match(parsed.path or ""))


def is_linkedin_login_url(url: str) -> bool:
    """Return True when ``url`` is a LinkedIn auth / checkpoint page."""
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if host not in _LINKEDIN_HOSTS:
        return False
    return bool(_LOGIN_PATH_RE.match(parsed.path or ""))


async def is_easy_apply_modal_open(page) -> bool:
    """Best-effort DOM probe for an open Easy Apply modal."""
    try:
        modal = page.locator(
            'div[role="dialog"][aria-labelledby*="easy-apply"], '
            'div[role="dialog"]:has-text("Apply to")'
        ).first
        return bool(await modal.count())
    except Exception:
        return False


async def has_easy_apply_button(page) -> bool:
    """Return True when the page exposes an Easy Apply trigger."""
    try:
        # aria-label is the most stable signal across LinkedIn UI revisions.
        btn = page.locator(
            'button[aria-label*="Easy Apply"], button:has-text("Easy Apply")'
        ).first
        return bool(await btn.count())
    except Exception:
        return False


# Step labels Linkedin renders in the modal header. Order matters for advance
# heuristics: the modal can skip steps depending on the role (e.g. resume-only).
KNOWN_STEPS = (
    "Contact info",
    "Resume",
    "Home address",
    "Additional Questions",
    "Work authorization",
    "Voluntary self identification",
    "Review",
)


def normalise_step_heading(text: str) -> str:
    """Map a raw modal heading to one of :data:`KNOWN_STEPS`, or "" if unknown.

    The match is case-insensitive and tolerates trailing punctuation /
    progress markers (LinkedIn sometimes prefixes the heading with the step
    number, e.g. ``"2 of 4 - Resume"``).
    """
    if not text:
        return ""
    cleaned = re.sub(r"\s+", " ", text).strip()
    cleaned = re.sub(r"^\s*\d+\s*of\s*\d+\s*[-:]?\s*", "", cleaned, flags=re.I)
    cleaned = cleaned.rstrip(":.- ").strip()
    lowered = cleaned.lower()
    for step in KNOWN_STEPS:
        if lowered == step.lower():
            return step
    # Treat plain "Additional questions" / "questions" / "Work eligibility"
    # variants as Additional Questions to keep the dispatcher simple.
    if "additional" in lowered and "question" in lowered:
        return "Additional Questions"
    if "work" in lowered and ("authoriz" in lowered or "eligib" in lowered):
        return "Work authorization"
    return ""
