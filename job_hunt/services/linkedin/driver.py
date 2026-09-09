"""LinkedIn Easy Apply, behind the shared driver contract.

``easy_apply.py`` already ran the flow and returned a structured result -- it is
the model the rest of this refactor copied. What it did not do is keep filling
and submitting apart: with ``auto_submit=True`` it clicks Submit itself, inside
the same call, and reports ``OUTCOME_SUBMITTED``. That puts the auto-submit gate
inside the driver, once per ATS, which is what ``ats_contract`` exists to stop.

So ``fill`` runs the existing flow with ``auto_submit=False`` and ``submit``
owns the click. Nothing about the fill path changes -- the parameter was always
there, and this adapter delegates to the same helper the CLI used, run log and
all, rather than rebuilding the helper bundle beside it.
"""

from __future__ import annotations

from job_hunt.services.linkedin import detect
from job_hunt.services.linkedin.easy_apply import (
    OUTCOME_LOGIN_REQUIRED as _EA_LOGIN_REQUIRED,
    OUTCOME_MODAL_NOT_OPENED as _EA_MODAL_NOT_OPENED,
    OUTCOME_NOT_EASY_APPLY as _EA_NOT_EASY_APPLY,
)
from job_hunt.services.linkedin.page_helpers import (
    _linkedin_click_by_name,
    _maybe_linkedin_easy_apply,
)
from job_hunt.services.web.ats_contract import (
    OUTCOME_BLOCKED,
    OUTCOME_FILLED,
    OUTCOME_LOGIN_REQUIRED,
    ApplyContext,
    AtsResult,
    SubmitOutcome,
)


class LinkedInDriver:
    """LinkedIn Easy Apply."""

    name = "linkedin"

    def matches_url(self, url: str) -> bool:
        return detect.is_linkedin_job_url(url)

    async def matches_page(self, page) -> bool:
        """LinkedIn does not redirect a vanity domain into itself, so the URL
        the page settled on is the whole answer."""
        return self.matches_url(getattr(page, "url", "") or "")

    async def fill(self, page, ctx: ApplyContext) -> AtsResult:
        """Fill the Easy Apply modal and stop at Review.

        ``auto_submit=False`` is the contract, not a default being accepted:
        ``fill`` never clicks a final Submit.
        """
        raw = await _maybe_linkedin_easy_apply(
            page,
            pdf=ctx.pdf,
            company=ctx.company,
            role=ctx.role,
            report_context=ctx.report_context or None,
            auto_submit=False,
            artifact_dir=ctx.artifact_dir,
        )
        if raw is None or raw.outcome in (_EA_NOT_EASY_APPLY, _EA_MODAL_NOT_OPENED):
            # Not ours, or the modal never opened -- in both cases the generic
            # flow should get the page, so this is not a failure to report.
            return AtsResult(outcome=OUTCOME_BLOCKED)
        outcome = (
            OUTCOME_LOGIN_REQUIRED
            if raw.outcome == _EA_LOGIN_REQUIRED
            else OUTCOME_FILLED
        )
        return AtsResult(
            outcome=outcome,
            filled=list(raw.filled),
            skipped=list(raw.skipped),
            answers=list(raw.answers),
            required_empty=list(raw.required_empty),
            uploads=[item for item in raw.filled if "LinkedIn Resume:" in item],
        )

    async def submit(self, page, ctx: ApplyContext) -> SubmitOutcome:
        """Click Submit on the Review step and say what came of it.

        The session has already decided this may happen. The only judgement
        here is what the page did afterwards -- and in particular that a click
        which lands without a confirmation is ``unknown``, not failure.
        """
        try:
            clicked = await _linkedin_click_by_name(page, "Submit application")
        except Exception as exc:
            return SubmitOutcome(state="rejected", evidence=f"click raised: {exc}")
        if not clicked:
            return SubmitOutcome(
                state="rejected",
                evidence="Submit application button not found on the Review step",
            )
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception:
            return SubmitOutcome(
                state="unknown",
                evidence="clicked Submit; no confirmation load within 30s",
            )
        return SubmitOutcome(state="confirmed", evidence=getattr(page, "url", "") or "")
