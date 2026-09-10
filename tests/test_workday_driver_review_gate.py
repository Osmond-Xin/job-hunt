"""What the Workday driver is allowed to say about a page it has not reached.

``fill`` returns Review-gate findings as ``blockers``, and the submit gate reads
that list. The gate is therefore only as honest as the step the findings came
from: the Review gate compares page text against the profile, so anywhere other
than Review every comparison fails for one uninteresting reason -- the summary
is not on screen. Those are not findings about the application.

The live run this pins: 2026-09-09, a University of Waterloo tenant. The same
three findings ("work experience title is not Data Analyst Intern", its dates,
the education GPA) were written into ``apply-review.json`` from the sign-in
modal, then again from Application Questions, then again from Voluntary
Disclosures. The Review page, once actually reached, showed the title and the
dates present and correct.

These cases assert the effect -- whether the gate is consulted and what comes
back -- rather than that a guard exists, because a guard nobody reaches is the
defect this repository has already shipped once.
"""

from __future__ import annotations

import asyncio

import pytest

from job_hunt.services.workday import driver as driver_module
from job_hunt.services.web.ats_contract import (
    OUTCOME_FILLED,
    OUTCOME_INCOMPLETE,
    ApplyContext,
)
from job_hunt.services.workday.review_gate import ReviewIssue


THREE_FINDINGS = [
    ReviewIssue(
        code="WD_REVIEW_TITLE_MISMATCH",
        message="Workday Review validation: work experience title is not Data Analyst Intern.",
        details={"expected_title": "Data Analyst Intern"},
    ),
    ReviewIssue(
        code="WD_REVIEW_DATE_MISMATCH",
        message="Workday Review validation: Data Analyst Intern dates are not 01/2026-03/2026.",
    ),
    ReviewIssue(
        code="WD_REVIEW_GPA_MISMATCH",
        message="Workday Review validation: education GPA is not 4.13/4.3.",
    ),
]


@pytest.fixture
def driver_on(monkeypatch):
    """Build a driver whose page is wherever you say, and count gate calls."""

    def build(step: str, *, issues=THREE_FINDINGS):
        calls: list[str] = []

        async def fake_generic_fill(page, **kwargs):
            return ["Email"], [], []

        async def fake_advance(page, values, **kwargs):
            return [], [], []

        async def fake_current_step(page):
            return step

        async def fake_required_empty(page):
            return []

        async def fake_collect(page):
            calls.append("collect")
            return list(issues)

        monkeypatch.setattr(driver_module, "generic_fill", fake_generic_fill)
        monkeypatch.setattr(driver_module, "_workday_advance_all_steps", fake_advance)
        monkeypatch.setattr(driver_module, "_workday_current_step", fake_current_step)
        monkeypatch.setattr(driver_module, "_required_empty", fake_required_empty)
        monkeypatch.setattr(driver_module, "_collect_workday_review_issues", fake_collect)
        return driver_module.WorkdayDriver(), calls

    return build


@pytest.mark.parametrize(
    "step",
    ["Create Account", "My Information", "My Experience", "Application Questions",
     "Voluntary Disclosures", ""],
)
def test_no_review_findings_from_a_page_that_is_not_review(driver_on, step):
    """Every step the walk passes through on the way, plus the unknown step a
    misread page now yields. None of them may produce a Review finding."""
    driver, calls = driver_on(step)

    result = asyncio.run(driver.fill(object(), ApplyContext(company="Acme", role="Analyst")))

    assert result.blockers == []
    assert calls == [], "the Review gate was consulted from a page that is not Review"
    assert result.outcome == OUTCOME_INCOMPLETE


def test_review_findings_still_reach_the_gate_from_the_review_page(driver_on):
    """The narrowing must not cost the gate its findings where they are real."""
    driver, calls = driver_on("Review")

    result = asyncio.run(driver.fill(object(), ApplyContext(company="Acme", role="Analyst")))

    assert calls == ["collect"]
    assert [b.code for b in result.blockers] == [
        "WD_REVIEW_TITLE_MISMATCH",
        "WD_REVIEW_DATE_MISMATCH",
        "WD_REVIEW_GPA_MISMATCH",
    ]
    assert result.blockers[0].details == {"expected_title": "Data Analyst Intern"}
    assert result.outcome == OUTCOME_FILLED


def test_a_clean_review_page_reports_filled_with_nothing_outstanding(driver_on):
    """The shape the submit gate is allowed to act on, and the only one."""
    driver, calls = driver_on("Review", issues=[])

    result = asyncio.run(driver.fill(object(), ApplyContext(company="Acme", role="Analyst")))

    assert calls == ["collect"]
    assert result.blockers == []
    assert result.required_empty == []
    assert result.outcome == OUTCOME_FILLED
