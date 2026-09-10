"""What counts as having filled a Workday work-experience card.

Four overlapping writers run against the card -- accessible label, scoped
field, positional order, and the date filler -- and any one of them succeeding
used to be confirmed by a single question: is the job title in *some* input?

On 2026-09-09 a University of Waterloo tenant answered yes for the wrong
reason. Its Review page read:

    Work Experience 1
    Job Title   Data Analyst Intern
    Company     Data Analyst Intern

The employer's name was on no field of the application, and the run reported
"Workday structured work experience" filled. A check that a value was typed
somewhere cannot tell that apart from a check that the right value reached the
right field, and the application is the thing that goes to an employer.
"""

from __future__ import annotations

import asyncio

import pytest

from job_hunt.services.workday import steps


ENTRY = {
    "title": "Data Analyst Intern",
    "company": "FindGrant",
    "location": "Remote, Toronto, ON, Canada",
    "start_year": "2026",
    "start_month": "1",
    "end_year": "2026",
    "end_month": "3",
    "description": "Built a trading pipeline.",
}


@pytest.fixture
def card(monkeypatch):
    """Stand every writer up as succeeded, and let the test say what the card
    ended up holding."""

    def build(input_values: list[str], *, fields_ok=True, dates_ok=True):
        async def ok(*args, **kwargs):
            return fields_ok

        async def dates(*args, **kwargs):
            return dates_ok

        async def has_value(page, value):
            return any(value in held for held in input_values)

        for name in (
            "_force_fill_by_accessible_label",
            "_fill_workday_scoped_field",
            "_fill_workday_experience_card_by_order",
        ):
            monkeypatch.setattr(steps, name, ok)
        monkeypatch.setattr(steps, "_fill_workday_experience_dates_by_title", dates)
        monkeypatch.setattr(steps, "_workday_any_input_has_value", has_value)
        return asyncio.run(steps._fill_workday_structured_experience(object(), ENTRY))

    return build


def test_the_title_landing_in_the_company_box_is_not_a_filled_card(card):
    """The Waterloo card, exactly: both boxes hold the title, the employer name
    is nowhere. The old check passed this because the title was in an input."""
    assert card(["Data Analyst Intern", "Data Analyst Intern"]) is False


def test_a_card_holding_both_values_is_filled(card):
    assert card(["Data Analyst Intern", "FindGrant", "Remote, Toronto, ON, Canada"]) is True


def test_a_missing_employer_name_is_not_a_filled_card(card):
    """Whatever the reason the Company box stayed empty, the card is not done."""
    assert card(["Data Analyst Intern"]) is False


def test_a_missing_title_is_not_a_filled_card(card):
    assert card(["FindGrant"]) is False


def test_a_writer_that_reported_failure_still_short_circuits(card):
    """The read-back is an addition, not a replacement: a card whose writers
    all failed never reaches it."""
    assert card(["Data Analyst Intern", "FindGrant"], fields_ok=False) is False
    assert card(["Data Analyst Intern", "FindGrant"], dates_ok=False) is False
