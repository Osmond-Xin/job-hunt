"""The claim that adding a third ATS costs a module and a registry entry.

docs/apply-seam-plan.md §2 makes that claim behavioural, and §6 criterion 8 says
the only real test of it is a third driver that exists nowhere but here. If this
file has to import from `cli/`, or reach into a session, or special-case
anything, the claim was false and the seam is in the wrong place.

Also here: the contract's own invariants, because they are what stop the
auto-submit gate drifting back inside a driver.
"""

from __future__ import annotations

import asyncio

import pytest

from job_hunt.services.web import ats_registry
from job_hunt.services.web.ats_contract import (
    OUTCOME_FILLED,
    ApplyContext,
    AtsDriver,
    AtsResult,
    Blocker,
    SubmitOutcome,
)


class _Page:
    def __init__(self, url: str) -> None:
        self.url = url


class GreenhouseDriver:
    """A third ATS, invented for this test and registered nowhere else."""

    name = "greenhouse"

    def matches_url(self, url: str) -> bool:
        return "boards.greenhouse.io" in url

    async def matches_page(self, page) -> bool:
        return self.matches_url(getattr(page, "url", "") or "")

    async def fill(self, page, ctx: ApplyContext) -> AtsResult:
        return AtsResult(outcome=OUTCOME_FILLED, filled=["Full name"])

    async def submit(self, page, ctx: ApplyContext) -> SubmitOutcome:
        return SubmitOutcome(state="confirmed", evidence="thanks page")


@pytest.fixture
def registered_third_driver(monkeypatch):
    driver = GreenhouseDriver()
    monkeypatch.setattr(
        ats_registry, "DRIVERS", [*ats_registry.DRIVERS, driver], raising=True
    )
    return driver


def test_a_third_driver_satisfies_the_protocol_without_inheriting_anything() -> None:
    """Structural, not nominal: a new ATS does not import a base class."""
    assert isinstance(GreenhouseDriver(), AtsDriver)


def test_the_shipped_drivers_satisfy_it_too() -> None:
    for driver in ats_registry.DRIVERS:
        assert isinstance(driver, AtsDriver), driver


def test_a_third_driver_is_dispatched_by_one_registry_entry(registered_third_driver) -> None:
    page = _Page("https://boards.greenhouse.io/acme/jobs/1")
    found = asyncio.run(ats_registry.driver_for(page))
    assert found is registered_third_driver


def test_the_existing_drivers_still_win_their_own_pages(registered_third_driver) -> None:
    workday = asyncio.run(ats_registry.driver_for(_Page("https://acme.wd5.myworkdayjobs.com/j/1")))
    linkedin = asyncio.run(ats_registry.driver_for(_Page("https://www.linkedin.com/jobs/view/1")))
    assert workday.name == "workday"
    assert linkedin.name == "linkedin"


def test_an_unknown_page_matches_nothing() -> None:
    """No driver is the answer for a plain careers page, and the caller has to
    handle that rather than being handed a driver that will fumble the form."""
    assert asyncio.run(ats_registry.driver_for(_Page("https://acme.com/careers"))) is None


def test_the_page_is_asked_before_the_url() -> None:
    """A vanity domain that redirects into a tenant is that tenant. Dispatch on
    the URL the operator pasted would miss every one of them."""
    requested = "https://careers.acme.com/job/1"
    landed = _Page("https://acme.wd5.myworkdayjobs.com/job/1")
    assert asyncio.run(ats_registry.driver_for(landed, requested)).name == "workday"


def test_with_no_page_yet_the_url_hint_is_used() -> None:
    assert asyncio.run(
        ats_registry.driver_for(None, "https://acme.wd5.myworkdayjobs.com/job/1")
    ).name == "workday"


def test_fill_cannot_report_a_submission() -> None:
    """There is no OUTCOME_SUBMITTED. A fill result that could say "submitted"
    is how the gate ends up inside a driver, one copy per ATS."""
    import job_hunt.services.web.ats_contract as contract

    assert not any(
        name.startswith("OUTCOME_") and "SUBMIT" in name.upper()
        for name in dir(contract)
    )


def test_an_unknown_submission_is_neither_success_nor_failure() -> None:
    """The three-valued state is the point: a click that lands without a
    confirmation must not be recorded as sent, and must not be retried against
    a real employer."""
    unknown = SubmitOutcome(state="unknown", evidence="no confirmation")
    assert unknown.needs_reconciliation is True
    assert SubmitOutcome(state="confirmed").needs_reconciliation is False
    assert SubmitOutcome(state="rejected").needs_reconciliation is False


def test_ready_to_submit_is_about_the_form_not_the_authorisation() -> None:
    assert AtsResult(outcome=OUTCOME_FILLED).ready_to_submit is True
    assert AtsResult(outcome=OUTCOME_FILLED, required_empty=["Phone"]).ready_to_submit is False
    assert (
        AtsResult(
            outcome=OUTCOME_FILLED,
            blockers=[Blocker(code="date_mismatch", message="dates wrong")],
        ).ready_to_submit
        is False
    )


def test_result_collections_are_per_instance() -> None:
    a, b = AtsResult(outcome=OUTCOME_FILLED), AtsResult(outcome=OUTCOME_FILLED)
    a.filled.append("First name")
    assert b.filled == []
