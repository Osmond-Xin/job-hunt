"""Role drift detection — compares expected role to the page's stated role.

When a job posting URL gets re-posted weeks later under a slightly different
title (or a recruiter reuses a URL for a follow-up role), the candidate may
apply to the *wrong* posting. This catches that by extracting the page's advertised role
(``og:title`` → ``<h1>`` → ``<title>`` in priority order) and fuzzy-matching
it against the expected ``--role`` argument.

The scoring uses rapidfuzz ``token_sort_ratio`` (0–100). Below 70 ⇒ warning.
"""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz


_DRIFT_THRESHOLD = 70


@dataclass
class RoleDriftFinding:
    """Result of role drift comparison.

    ``warning`` is None when the role looks consistent. Otherwise it's a
    one-line yellow-warning string ready to print.
    """

    expected_role: str
    page_role: str
    similarity: float  # 0–100
    warning: str | None


def detect_role_drift(
    expected_role: str | None,
    page_role: str | None,
    *,
    threshold: int = _DRIFT_THRESHOLD,
) -> RoleDriftFinding:
    """Return a finding object. Caller decides whether to print/abort.

    Both args are tolerant of None / empty strings — those skip the check.
    """
    expected = (expected_role or "").strip()
    page = (page_role or "").strip()
    if not expected or not page:
        return RoleDriftFinding(
            expected_role=expected, page_role=page, similarity=0.0, warning=None
        )
    score = fuzz.token_sort_ratio(expected, page)
    if score >= threshold:
        return RoleDriftFinding(
            expected_role=expected, page_role=page, similarity=score, warning=None
        )
    warning = (
        f"Possible role drift: expected {expected!r}, page advertises "
        f"{page!r} (similarity {score:.0f}/100 < {threshold}). "
        f"Re-run `job-hunt evaluate '<url>'` if the posting changed."
    )
    return RoleDriftFinding(
        expected_role=expected, page_role=page, similarity=score, warning=warning
    )


async def extract_page_role(page) -> str | None:
    """Extract the page's stated role from ``og:title`` → ``<h1>`` → ``<title>``.

    Returns the first non-empty signal, or None when nothing usable surfaces.
    Each step is wrapped in try/except so a slow DOM doesn't crash the apply flow.
    """
    # 1. og:title (most reliable when set; recruiters set it deliberately)
    try:
        og = page.locator('meta[property="og:title"]').first
        if await og.count() > 0:
            content = await og.get_attribute("content", timeout=2000)
            if content and content.strip():
                return content.strip()
    except Exception:
        pass
    # 2. First visible <h1>
    try:
        h1 = page.locator("h1").first
        if await h1.count() > 0:
            text = await h1.inner_text(timeout=2000)
            if text and text.strip():
                return text.strip()
    except Exception:
        pass
    # 3. <title> as last resort
    try:
        title = await page.title()
        if title and title.strip():
            return title.strip()
    except Exception:
        pass
    return None
