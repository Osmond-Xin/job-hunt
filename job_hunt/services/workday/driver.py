"""Workday, behind the shared driver contract.

The thing LinkedIn had and Workday did not. Before this, ``_open_apply_page``
hand-rolled the Workday flow inline over about 120 lines and its auto-submit
gate over 60 more, while consuming the LinkedIn driver's result in ten. This is
the other side of that asymmetry.

The flow itself is unchanged -- ``steps.py`` holds it, moved verbatim in Phase
3a. This maps it onto ``AtsResult``/``SubmitOutcome`` and, in doing so, moves
the gate out: ``fill`` stops at Review and reports what is missing, and the
session decides whether ``submit`` runs.
"""

from __future__ import annotations

from job_hunt.services.profile_loader import _apply_profile_values
from job_hunt.services.web.ats_contract import (
    OUTCOME_FILLED,
    ApplyContext,
    AtsResult,
    Blocker,
    SubmitOutcome,
)
from job_hunt.services.workday.detect import is_workday_page, is_workday_url
from job_hunt.services.workday.required_empty import (
    filter_non_blocking_workday_skips,
    filter_required_empty_fields,
)
from job_hunt.services.workday.steps import (
    _collect_workday_review_issues,
    _try_workday_final_submit,
    _workday_advance_all_steps,
    _workday_resume_was_uploaded,
)


class WorkdayDriver:
    """A Workday tenant's application flow."""

    name = "workday"

    def matches_url(self, url: str) -> bool:
        return is_workday_url(url)

    async def matches_page(self, page) -> bool:
        """The answer that counts. An employer careers domain that redirects
        into a tenant is a Workday page even though the pasted URL was not."""
        return is_workday_page(page)

    async def fill(self, page, ctx: ApplyContext) -> AtsResult:
        """Walk My Information through Voluntary Disclosures, stopping at Review.

        Never clicks the final Submit -- that is ``submit``, and whether it runs
        is the session's decision.
        """
        filled, skipped, answers = await _workday_advance_all_steps(
            page,
            ctx.profile_values or _apply_profile_values(),
            pdf=ctx.pdf,
            company=ctx.company,
            role=ctx.role,
            report_context=ctx.report_context or None,
            artifact_dir=ctx.artifact_dir,
        )
        skipped = filter_non_blocking_workday_skips(skipped)

        uploads: list[str] = []
        if ctx.pdf and await _workday_resume_was_uploaded(page, ctx.pdf):
            # Workday uploads the résumé inside My Experience, not through the
            # generic file input, so the only honest way to claim it attached is
            # to see the filename on the page.
            uploads.append(f"Workday Resume: {ctx.pdf.name}")

        required_empty = filter_required_empty_fields(
            await _required_empty(page), filled
        )
        issues = await _collect_workday_review_issues(page)
        return AtsResult(
            outcome=OUTCOME_FILLED,
            filled=list(filled),
            skipped=list(skipped),
            answers=list(answers),
            required_empty=list(required_empty),
            uploads=uploads,
            blockers=[
                # ReviewIssue is a Workday type and stays one: it is mapped here,
                # at the driver's edge, rather than put in the shared contract
                # where the LinkedIn driver would import a shape it never fills.
                Blocker(code=issue.code, message=issue.message, details=dict(issue.details))
                for issue in issues
            ],
        )

    async def submit(self, page, ctx: ApplyContext) -> SubmitOutcome:
        """Click Submit on the Review step and say what came of it."""
        try:
            clicked = await _try_workday_final_submit(page)
        except Exception as exc:
            return SubmitOutcome(state="rejected", evidence=f"click raised: {exc}")
        if not clicked:
            return SubmitOutcome(
                state="rejected", evidence="Submit button not located on the Review page"
            )
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception:
            # Clicked, and no confirmation arrived. Not a failure, and not to be
            # retried against a real employer: unknown, for a human to resolve.
            return SubmitOutcome(
                state="unknown", evidence="clicked Submit; no confirmation load within 30s"
            )
        return SubmitOutcome(state="confirmed", evidence=getattr(page, "url", "") or "")


async def _required_empty(page) -> list[str]:
    from job_hunt.services.web.form_fill import _required_empty_fields

    return await _required_empty_fields(page)
