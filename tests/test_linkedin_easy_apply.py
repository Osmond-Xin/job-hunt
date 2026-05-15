"""Tests for the LinkedIn Easy Apply dispatcher and field strategies.

These tests use ``AsyncMock`` doubles for every Playwright surface so the
dispatcher's per-step orchestration is fully exercised without a real browser.
The pure helpers in ``services.linkedin.fields`` are exercised directly.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from job_hunt.services.linkedin.detect import (
    is_linkedin_job_url,
    is_linkedin_login_url,
    normalise_step_heading,
)
from job_hunt.services.linkedin.easy_apply import (
    Helpers,
    OUTCOME_LOGIN_REQUIRED,
    OUTCOME_MODAL_NOT_OPENED,
    OUTCOME_NOT_EASY_APPLY,
    OUTCOME_REACHED_REVIEW,
    OUTCOME_STUCK,
    OUTCOME_SUBMITTED,
    run_easy_apply,
)
from job_hunt.services.linkedin.fields import (
    classify_label,
    country_code_best_match,
    yes_no_answer,
    years_of_experience_answer,
)


# --- URL detection ----------------------------------------------------------


def test_is_linkedin_job_url_accepts_view_path() -> None:
    assert is_linkedin_job_url(
        "https://www.linkedin.com/jobs/view/3902347291/?refId=abc"
    )


def test_is_linkedin_job_url_accepts_collections_path() -> None:
    assert is_linkedin_job_url(
        "https://www.linkedin.com/jobs/collections/recommended/?currentJobId=1"
    )


def test_is_linkedin_job_url_rejects_profile_and_feed() -> None:
    assert not is_linkedin_job_url("https://www.linkedin.com/in/osmond-xin/")
    assert not is_linkedin_job_url("https://www.linkedin.com/feed/")
    assert not is_linkedin_job_url("https://www.linkedin.com/")


def test_is_linkedin_job_url_rejects_non_linkedin_hosts() -> None:
    assert not is_linkedin_job_url("https://example.com/jobs/view/1")
    assert not is_linkedin_job_url("")


def test_is_linkedin_login_url_flags_login_and_checkpoint() -> None:
    assert is_linkedin_login_url("https://www.linkedin.com/login")
    assert is_linkedin_login_url("https://www.linkedin.com/checkpoint/lg/login")
    assert not is_linkedin_login_url("https://www.linkedin.com/jobs/view/1")


# --- Step heading normalisation --------------------------------------------


def test_normalise_step_heading_strips_progress_marker() -> None:
    assert normalise_step_heading("2 of 4 - Contact info") == "Contact info"
    assert normalise_step_heading("3 of 5: Additional Questions") == "Additional Questions"


def test_normalise_step_heading_resolves_variants() -> None:
    assert normalise_step_heading("Additional questions") == "Additional Questions"
    assert normalise_step_heading("Work eligibility") == "Work authorization"
    assert normalise_step_heading("Review your application") == ""  # unknown → ""


def test_normalise_step_heading_blank_input() -> None:
    assert normalise_step_heading("") == ""
    assert normalise_step_heading("  ") == ""


# --- Field strategy helpers -------------------------------------------------


def test_classify_label_handles_common_labels() -> None:
    assert classify_label("Email address") == "email"
    assert classify_label("Mobile phone number") == "phone"
    assert classify_label("Phone country code") == "phone_country"
    assert classify_label("LinkedIn profile") == "linkedin"
    assert classify_label("Portfolio URL") == "website"
    assert classify_label("City") == "location"
    assert classify_label("First Name") == "first_name"
    assert classify_label("") == ""


def test_yes_no_answer_returns_yes_for_authorization() -> None:
    assert yes_no_answer(
        "Are you legally authorized to work in Canada?"
    ) == "Yes"
    assert yes_no_answer("Do you agree to the terms?") == "Yes"


def test_yes_no_answer_returns_no_for_sponsorship_required() -> None:
    assert yes_no_answer(
        "Will you now or in the future require sponsorship for employment?"
    ) == "No"


def test_yes_no_answer_returns_blank_for_ambiguous() -> None:
    assert yes_no_answer("Tell us about yourself.") == ""
    assert yes_no_answer("") == ""


def test_years_of_experience_answer_default() -> None:
    assert years_of_experience_answer("How many years of experience do you have with Python?") == "2"
    assert years_of_experience_answer("How many years using SQL?") == "2"
    assert years_of_experience_answer("Years of experience") == "2"


def test_years_of_experience_answer_ignores_unrelated_questions() -> None:
    assert years_of_experience_answer("What is your current role?") == ""
    assert years_of_experience_answer("") == ""


def test_country_code_best_match_prefers_exact_then_prefix() -> None:
    options = ["United States (+1)", "Canada (+1)", "United Kingdom (+44)"]
    assert country_code_best_match("Canada", options) == "Canada (+1)"
    assert country_code_best_match("United States", options) == "United States (+1)"
    # Substring fallback
    assert country_code_best_match("Kingdom", options) == "United Kingdom (+44)"


def test_country_code_best_match_returns_blank_on_no_match() -> None:
    options = ["Canada (+1)", "Mexico (+52)"]
    assert country_code_best_match("Germany", options) == ""
    assert country_code_best_match("", options) == ""


# --- Driver: URL gating -----------------------------------------------------


class _Page:
    """Minimal stand-in for a Playwright page exposing only the ``url`` attr."""

    def __init__(self, url: str) -> None:
        self.url = url


def _make_helpers(**overrides) -> Helpers:
    """Build a Helpers bundle pre-wired with AsyncMock defaults."""
    defaults: dict = {
        "click_by_name": AsyncMock(return_value=False),
        "fill_by_label": AsyncMock(return_value=False),
        "select_dropdown": AsyncMock(return_value=False),
        "dropdown_options": AsyncMock(return_value=[]),
        "select_radio": AsyncMock(return_value=False),
        "attach_resume": AsyncMock(return_value=False),
        "read_modal_heading": AsyncMock(return_value=""),
        "read_required_empty": AsyncMock(return_value=[]),
        "read_modal_fields": AsyncMock(return_value=[]),
        "answer_lookup": lambda q, ctx: "",
    }
    defaults.update(overrides)
    return Helpers(**defaults)


def _run(coro):
    return asyncio.run(coro)


def test_run_easy_apply_returns_not_easy_apply_for_non_linkedin() -> None:
    helpers = _make_helpers()
    result = _run(
        run_easy_apply(
            _Page("https://example.com/careers/123"),
            values={},
            pdf=None,
            company=None,
            role=None,
            report_context=None,
            helpers=helpers,
        )
    )
    assert result.outcome == OUTCOME_NOT_EASY_APPLY
    helpers.click_by_name.assert_not_awaited()


def test_run_easy_apply_returns_login_required_when_on_login_page() -> None:
    helpers = _make_helpers()
    result = _run(
        run_easy_apply(
            _Page("https://www.linkedin.com/login"),
            values={},
            pdf=None,
            company=None,
            role=None,
            report_context=None,
            helpers=helpers,
        )
    )
    assert result.outcome == OUTCOME_LOGIN_REQUIRED
    assert any("not logged in" in s for s in result.skipped)


def test_run_easy_apply_returns_modal_not_opened_when_trigger_missing() -> None:
    helpers = _make_helpers(
        click_by_name=AsyncMock(return_value=False),
    )
    result = _run(
        run_easy_apply(
            _Page("https://www.linkedin.com/jobs/view/1"),
            values={},
            pdf=None,
            company=None,
            role=None,
            report_context=None,
            helpers=helpers,
        )
    )
    assert result.outcome == OUTCOME_MODAL_NOT_OPENED
    assert any("Easy Apply" in s for s in result.skipped)


# --- Driver: step orchestration --------------------------------------------


def _heading_sequencer(sequence: list[str]):
    """Return a callable that yields ``sequence`` items, then repeats the last.

    The dispatcher reads the modal heading at least twice per iteration (once
    at the top, once after advance to detect a stuck page). Repeating the
    final entry keeps the test deterministic without forcing every caller to
    pre-compute the exact read count.
    """
    seq = list(sequence)
    state = {"i": 0}

    async def _read(*args, **kwargs):
        i = state["i"]
        state["i"] = min(i + 1, len(seq) - 1)
        return seq[i] if seq else ""

    return _read


def _modal_open_patch():
    """Context-manager-style helper to patch ``is_easy_apply_modal_open``.

    Returns a tuple ``(install, restore)`` so tests can keep their try/finally
    blocks tight.
    """
    import job_hunt.services.linkedin.easy_apply as ea

    async def _open(page):
        return True

    orig = ea.is_easy_apply_modal_open

    def _install():
        ea.is_easy_apply_modal_open = _open  # type: ignore[assignment]

    def _restore():
        ea.is_easy_apply_modal_open = orig  # type: ignore[assignment]

    return _install, _restore


def test_run_easy_apply_reaches_review_and_stops_when_auto_submit_off() -> None:
    """Open modal → Contact info → Resume → Review (no submit)."""
    helpers = _make_helpers(
        click_by_name=AsyncMock(return_value=True),
        read_modal_heading=_heading_sequencer(
            ["Contact info", "Resume", "Resume", "Review"]
        ),
        read_modal_fields=AsyncMock(return_value=[]),
        attach_resume=AsyncMock(return_value=True),
    )

    install, restore = _modal_open_patch()
    install()
    try:
        result = _run(
            run_easy_apply(
                _Page("https://www.linkedin.com/jobs/view/1"),
                values={"email": "a@b.com", "phone": "555", "linkedin": "u"},
                pdf=Path("resume.pdf"),
                company="Acme",
                role="Engineer",
                report_context=None,
                helpers=helpers,
                auto_submit=False,
            )
        )
    finally:
        restore()

    assert result.outcome == OUTCOME_REACHED_REVIEW
    assert result.submitted is False
    assert result.steps_visited[:2] == ["Contact info", "Resume"]
    assert any("LinkedIn Resume:" in item for item in result.filled)


def test_run_easy_apply_fires_submit_when_auto_submit_and_review_clean() -> None:
    """Auto-submit gate fires when required_empty is [] on Review."""
    helpers = _make_helpers(
        click_by_name=AsyncMock(return_value=True),
        read_modal_heading=_heading_sequencer(["Review"]),
        read_required_empty=AsyncMock(return_value=[]),
    )

    install, restore = _modal_open_patch()
    install()
    try:
        result = _run(
            run_easy_apply(
                _Page("https://www.linkedin.com/jobs/view/1"),
                values={},
                pdf=None,
                company=None,
                role=None,
                report_context=None,
                helpers=helpers,
                auto_submit=True,
            )
        )
    finally:
        restore()

    assert result.outcome == OUTCOME_SUBMITTED
    assert result.submitted is True
    # Submit application click happened exactly once.
    submit_calls = [
        call for call in helpers.click_by_name.await_args_list
        if call.args[1:] and call.args[1] == "Submit application"
    ]
    assert len(submit_calls) == 1


def test_run_easy_apply_auto_submit_blocked_by_required_empty() -> None:
    """Auto-submit must NOT click when required_empty is non-empty on Review."""
    helpers = _make_helpers(
        click_by_name=AsyncMock(return_value=True),
        read_modal_heading=_heading_sequencer(["Review"]),
        read_required_empty=AsyncMock(return_value=["Years of experience"]),
    )

    install, restore = _modal_open_patch()
    install()
    try:
        result = _run(
            run_easy_apply(
                _Page("https://www.linkedin.com/jobs/view/1"),
                values={},
                pdf=None,
                company=None,
                role=None,
                report_context=None,
                helpers=helpers,
                auto_submit=True,
            )
        )
    finally:
        restore()

    assert result.outcome == OUTCOME_REACHED_REVIEW
    assert result.submitted is False
    assert result.required_empty == ["Years of experience"]
    # Crucially, Submit application was NOT clicked.
    submit_calls = [
        call for call in helpers.click_by_name.await_args_list
        if call.args[1:] and call.args[1] == "Submit application"
    ]
    assert submit_calls == []


def test_run_easy_apply_fills_yes_for_authorization_radio() -> None:
    """Field dispatcher should send Yes/No into the radio helper for known questions."""
    helpers = _make_helpers(
        click_by_name=AsyncMock(return_value=True),
        read_modal_heading=_heading_sequencer(
            ["Work authorization", "Review", "Review"]
        ),
        read_modal_fields=AsyncMock(return_value=[
            {
                "label": "Are you legally authorized to work in Canada?",
                "kind": "radio",
                "options": ["Yes", "No"],
            }
        ]),
        select_radio=AsyncMock(return_value=True),
        read_required_empty=AsyncMock(return_value=[]),
    )

    install, restore = _modal_open_patch()
    install()
    try:
        result = _run(
            run_easy_apply(
                _Page("https://www.linkedin.com/jobs/view/1"),
                values={},
                pdf=None,
                company=None,
                role=None,
                report_context=None,
                helpers=helpers,
                auto_submit=False,
            )
        )
    finally:
        restore()

    helpers.select_radio.assert_awaited()
    args = helpers.select_radio.await_args_list[0].args
    # (page, question, choice)
    assert "authorized" in args[1]
    assert args[2] == "Yes"
    assert result.outcome == OUTCOME_REACHED_REVIEW
    assert any("→ Yes" in item for item in result.filled)


def test_run_easy_apply_stuck_when_no_advance_button() -> None:
    """When click_by_name fails after the initial open, the driver bails."""
    call_count = {"n": 0}

    async def click(page, name):
        call_count["n"] += 1
        # First call opens the modal; subsequent calls (advance) fail.
        return call_count["n"] == 1

    helpers = _make_helpers(
        click_by_name=AsyncMock(side_effect=click),
        read_modal_heading=_heading_sequencer(["Contact info"]),
        read_modal_fields=AsyncMock(return_value=[]),
        read_required_empty=AsyncMock(return_value=["Email"]),
    )

    install, restore = _modal_open_patch()
    install()
    try:
        result = _run(
            run_easy_apply(
                _Page("https://www.linkedin.com/jobs/view/1"),
                values={},
                pdf=None,
                company=None,
                role=None,
                report_context=None,
                helpers=helpers,
                auto_submit=False,
            )
        )
    finally:
        restore()

    assert result.outcome == OUTCOME_STUCK
    assert result.required_empty == ["Email"]


def test_run_easy_apply_fills_phone_country_dropdown() -> None:
    """Country-code best-match should be wired to the dropdown helper."""
    helpers = _make_helpers(
        click_by_name=AsyncMock(return_value=True),
        read_modal_heading=_heading_sequencer(
            ["Contact info", "Review", "Review"]
        ),
        read_modal_fields=AsyncMock(return_value=[
            {
                "label": "Phone country code",
                "kind": "dropdown",
                "options": ["United States (+1)", "Canada (+1)"],
            }
        ]),
        select_dropdown=AsyncMock(return_value=True),
        read_required_empty=AsyncMock(return_value=[]),
    )

    install, restore = _modal_open_patch()
    install()
    try:
        result = _run(
            run_easy_apply(
                _Page("https://www.linkedin.com/jobs/view/1"),
                values={"country": "Canada"},
                pdf=None,
                company=None,
                role=None,
                report_context=None,
                helpers=helpers,
                auto_submit=False,
            )
        )
    finally:
        restore()

    helpers.select_dropdown.assert_awaited()
    args = helpers.select_dropdown.await_args_list[0].args
    assert args[1] == "Phone country code"
    assert args[2] == "Canada (+1)"
    assert result.outcome == OUTCOME_REACHED_REVIEW
