"""Did the application actually go through?

Both drivers used to answer this by awaiting ``wait_for_load_state`` and
returning ``confirmed`` if it resolved. It nearly always resolves: on an already
loaded page it returns immediately, and an ATS that submits over XHR never
navigates at all. So a click that did nothing came back as a confirmed
submission, the session recorded the row as Applied, and nobody found out until
the rejection that never came.

A 2026-09-09 review caught it. The rule now is that ``confirmed`` requires
positive evidence -- the page said so -- and anything else is ``unknown``, which
blocks recording and blocks a retry until a person looks.

The asymmetry is on purpose. Wrongly ``unknown`` costs the operator a minute
checking a tab. Wrongly ``confirmed`` costs a real application to a real
employer, either lost or sent twice.
"""

from __future__ import annotations

import re

from job_hunt.services.web.ats_contract import SubmitOutcome


# Phrases an ATS shows once an application is in. Deliberately short and
# generic: matching too eagerly is the failure mode this module exists to
# prevent, so each of these has to be a claim the page is making about *this*
# submission, not a word that could appear on a form.
_CONFIRMATION_PATTERNS = [
    r"application (?:was )?(?:successfully )?submitted",
    r"submitted your application",
    r"your application (?:has been|was) received",
    r"thank you for (?:your interest|applying)",
    r"we(?:'ve| have) received your application",
    r"application complete",
]
_CONFIRMATION_RE = re.compile("|".join(_CONFIRMATION_PATTERNS), re.I)

# URL fragments ATSs redirect to after a successful submission.
_CONFIRMATION_URL_RE = re.compile(
    r"/(?:thank[-_]?you|confirmation|submitted|success)(?:[/?#]|$)", re.I
)


async def confirm_submission(page, *, settle_ms: int = 3000) -> SubmitOutcome:
    """Look for evidence the submission landed, and say what was found.

    ``settle_ms`` restores the pause the original Workday path had before it
    read the page: an ATS that swaps in a confirmation panel needs a moment,
    and reading too early is how a real success gets reported as unknown.
    """
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=30000)
    except Exception:
        # Not itself a verdict: the page may have submitted over XHR without
        # navigating. Fall through and look at what is on screen.
        pass
    try:
        await page.wait_for_timeout(settle_ms)
    except Exception:
        pass

    url = getattr(page, "url", "") or ""
    if _CONFIRMATION_URL_RE.search(url):
        return SubmitOutcome(state="confirmed", evidence=f"confirmation URL: {url}")

    try:
        text = await page.locator("body").inner_text(timeout=5000)
    except Exception:
        return SubmitOutcome(
            state="unknown",
            evidence=f"clicked Submit; page text unreadable afterwards (url={url})",
        )

    match = _CONFIRMATION_RE.search(text or "")
    if match:
        return SubmitOutcome(
            state="confirmed", evidence=f"page says: {match.group(0)!r} (url={url})"
        )
    return SubmitOutcome(
        state="unknown",
        evidence=(
            "clicked Submit; no confirmation wording or URL on the page afterwards "
            f"(url={url}). Check the browser before re-running."
        ),
    )
