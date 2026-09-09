"""The Workday step decision, tested without a browser.

This is the spike docs/apply-seam-plan.md §3.9 made Phase 3b conditional on: can
a step decision be separated from the DOM read it needs, and still behave the
same? These cases are the evidence, and they are written against snapshots of
what a real Workday page yields -- a list of heading texts, and the body's inner
text -- not against a shape invented to be convenient.
"""

from __future__ import annotations

import pytest

from job_hunt.services.workday.step_decisions import (
    KNOWN_STEPS,
    step_from_body_text,
    step_from_headings,
)


@pytest.mark.parametrize("step", KNOWN_STEPS)
def test_every_known_step_is_recognised_from_its_heading(step: str) -> None:
    assert step_from_headings([step]) == step


def test_the_first_matching_heading_wins() -> None:
    """A Workday page renders the current step's heading above the next
    section's, so the first match is the step the applicant is on."""
    assert step_from_headings(["My Experience", "Review"]) == "My Experience"


def test_headings_are_matched_after_collapsing_whitespace() -> None:
    """Workday wraps long headings, and inner_text keeps the newline."""
    assert step_from_headings(["My  Information"]) == "My Information"
    assert step_from_headings(["Application\nQuestions"]) == "Application Questions"
    assert step_from_headings(["  Review  "]) == "Review"


def test_a_heading_that_merely_contains_a_step_name_is_not_that_step() -> None:
    """The exact match is the whole point: "Review your application" is the
    instruction at the top of My Experience on several tenants, and reading it
    as the Review step would tell the caller the form is finished."""
    assert step_from_headings(["Review your application"]) == ""
    assert step_from_headings(["Voluntary Disclosures (optional)"]) == ""
    assert step_from_headings(["Start Your Application"]) == ""


def test_no_headings_at_all() -> None:
    assert step_from_headings([]) == ""


def test_unrelated_headings() -> None:
    assert step_from_headings(["Search Jobs", "Sign In"]) == ""


@pytest.mark.parametrize("step", KNOWN_STEPS)
def test_every_known_step_is_recognised_from_a_body_line(step: str) -> None:
    assert step_from_body_text(f"Some preamble\n{step}\nFirst Name") == step


def test_a_step_name_at_the_very_start_or_end_of_the_body_counts() -> None:
    assert step_from_body_text("Review") == "Review"
    assert step_from_body_text("header\nReview") == "Review"
    assert step_from_body_text("Review\nfooter") == "Review"


def test_a_step_name_mid_sentence_does_not_count() -> None:
    """The body fallback exists for tenants that style step titles as plain
    text. Prose that happens to say the word is not a step title."""
    assert step_from_body_text("Please Review your answers before submitting.") == ""
    assert step_from_body_text("We will contact you about My Experience shortly.") == ""


def test_empty_body() -> None:
    assert step_from_body_text("") == ""


def test_the_body_pass_returns_the_first_known_step_present() -> None:
    """Order here follows KNOWN_STEPS, not document order -- the same as the
    behaviour this replaced. Pinned so a later change to make it document-order
    is a deliberate one, not a silent one."""
    body = "Review\nMy Information\n"
    assert step_from_body_text(body) == "My Information"


def test_a_real_my_information_page() -> None:
    """Snapshot from a Workday tenant's My Information step: the heading is
    exact, and the body carries the field labels around it."""
    headings = ["Acme Corporation", "My Information", "Country"]
    assert step_from_headings(headings) == "My Information"


def test_a_real_review_page_where_the_heading_is_decorated() -> None:
    """Heading pass misses, body pass catches it -- which is the whole reason
    the second read exists, and why it stays conditional on the first."""
    headings = ["Review your application before submitting"]
    body = "Acme Corporation\nReview\nMy Information\nFirst Name\nYi"
    assert step_from_headings(headings) == ""
    assert step_from_body_text(body) == "My Information"
