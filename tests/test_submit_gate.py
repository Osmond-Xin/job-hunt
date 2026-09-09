"""The one gate that decides whether a real application gets sent.

docs/apply-seam-plan.md §3.7 requires these before the phase that rewrites the
gate, and requires them to pin *effects* and not only event names -- a
regression can emit exactly the right `auto_submit.gated` line and click anyway.
So every case here asserts `allowed`, and the event name is checked on top.

Before this there were two gates: one inside the LinkedIn flow, one written out
again for Workday in `_open_apply_page`. The table below is what both of them
have to agree on now, because there is only one of them.
"""

from __future__ import annotations

import pytest

from job_hunt.services.web.ats_contract import OUTCOME_INCOMPLETE, Blocker
from job_hunt.services.web.submit_gate import (
    REASON_NO_DRIVER,
    REASON_NOT_AT_REVIEW,
    REASON_NOT_REQUESTED,
    REASON_PROFILE_DISABLED,
    REASON_REQUIRED_EMPTY,
    REASON_REVIEW_ISSUES,
    REASON_STUDENT_MODE,
    REASON_UNRESOLVED_ATTEMPT,
    authorised,
    may_submit,
)


def _clear(auth=None):
    return dict(
        authorisation=auth or authorised(requested=True, profile_enabled=True, mode="full"),
        driver_name="workday",
        required_empty=[],
        blockers=[],
    )


# --- the three keys ---------------------------------------------------------


def test_all_three_keys_authorise() -> None:
    assert authorised(requested=True, profile_enabled=True, mode="full").allowed is True


@pytest.mark.parametrize(
    "requested,profile_enabled,mode,reason",
    [
        (False, True, "full", REASON_NOT_REQUESTED),
        (False, False, "full", REASON_NOT_REQUESTED),
        (False, True, "student", REASON_NOT_REQUESTED),
        (False, False, "student", REASON_NOT_REQUESTED),
        (True, False, "full", REASON_PROFILE_DISABLED),
        (True, False, "student", REASON_PROFILE_DISABLED),
        (True, True, "student", REASON_STUDENT_MODE),
    ],
)
def test_every_incomplete_combination_refuses(requested, profile_enabled, mode, reason) -> None:
    """Exhaustive over the seven ways to be short of all three. A gate rewritten
    as a chain of ifs can pass a representative sample and fail one specific
    combination, so none is left to inference."""
    decision = authorised(requested=requested, profile_enabled=profile_enabled, mode=mode)
    assert decision.allowed is False
    assert decision.reason == reason


def test_an_unauthorised_run_is_bypassed_not_gated() -> None:
    """Two different refusals that should not share a name: nothing is wrong
    with the form, the operator simply did not ask."""
    assert authorised(requested=False, profile_enabled=True, mode="full").event == "auto_submit.bypassed"
    assert authorised(requested=True, profile_enabled=False, mode="full").event == "auto_submit.bypassed"
    assert authorised(requested=True, profile_enabled=True, mode="student").event == "auto_submit.bypassed"


# --- the form's own state ---------------------------------------------------


def test_a_clean_authorised_application_may_be_sent() -> None:
    decision = may_submit(**_clear())
    assert decision.allowed is True
    assert decision.event == "auto_submit.fired"


def test_a_required_field_still_empty_blocks() -> None:
    decision = may_submit(**{**_clear(), "required_empty": ["Phone Number"]})
    assert decision.allowed is False
    assert decision.reason == REASON_REQUIRED_EMPTY
    assert decision.detail["fields"] == ["Phone Number"]
    assert decision.event == "auto_submit.gated"


def test_a_review_blocker_blocks() -> None:
    decision = may_submit(
        **{**_clear(), "blockers": [Blocker(code="date_mismatch", message="dates wrong")]}
    )
    assert decision.allowed is False
    assert decision.reason == REASON_REVIEW_ISSUES
    assert decision.detail["codes"] == ["date_mismatch"]


def test_no_driver_owns_the_page_blocks() -> None:
    """The old gate refused any non-Workday host by name. The rule it was really
    expressing is that nothing may be submitted through a form no driver
    understands -- which is now also true for a third ATS nobody has written."""
    decision = may_submit(**{**_clear(), "driver_name": None})
    assert decision.allowed is False
    assert decision.reason == REASON_NO_DRIVER


def test_an_unresolved_previous_attempt_blocks() -> None:
    """A submit that landed with no confirmation. Clicking again risks sending
    the same application twice, and only a human can say whether it went."""
    decision = may_submit(**{**_clear(), "unresolved_attempt": "artifacts/apply/acme"})
    assert decision.allowed is False
    assert decision.reason == REASON_UNRESOLVED_ATTEMPT
    assert decision.detail["artifact_dir"] == "artifacts/apply/acme"


# --- ordering ---------------------------------------------------------------


def test_authorisation_is_checked_before_the_form() -> None:
    """An unauthorised run should say the operator did not ask, not name
    whichever field happened to be empty -- the form's state is beside the
    point, and reporting it invites someone to "fix" the field and retry."""
    decision = may_submit(
        authorisation=authorised(requested=False, profile_enabled=True, mode="full"),
        driver_name="workday",
        required_empty=["Phone Number"],
        blockers=[Blocker(code="date_mismatch", message="x")],
    )
    assert decision.allowed is False
    assert decision.reason == REASON_NOT_REQUESTED


def test_an_unresolved_attempt_outranks_a_clean_form() -> None:
    decision = may_submit(**{**_clear(), "unresolved_attempt": "artifacts/apply/acme"})
    assert decision.reason == REASON_UNRESOLVED_ATTEMPT


def test_a_form_that_never_reached_review_blocks() -> None:
    """The reason emptiness is not readiness: a walk that stalled reports no
    required fields because it never saw the page that lists them. Checked at
    the gate as well as end to end, so removing the check fails here too."""
    decision = may_submit(**{**_clear(), "outcome": OUTCOME_INCOMPLETE})
    assert decision.allowed is False
    assert decision.reason == REASON_NOT_AT_REVIEW
    assert decision.detail["outcome"] == OUTCOME_INCOMPLETE


@pytest.mark.parametrize(
    "kwargs",
    [
        {"required_empty": ["Phone"]},
        {"blockers": [Blocker(code="c", message="m")]},
        {"driver_name": None},
        {"unresolved_attempt": "artifacts/apply/acme"},
        {"outcome": OUTCOME_INCOMPLETE},
    ],
)
def test_no_blocking_condition_ever_allows(kwargs) -> None:
    """The effect assertion the plan asked for: whatever the reason, `allowed`
    is False. An event name alone would not catch a gate that logged and
    clicked."""
    assert may_submit(**{**_clear(), **kwargs}).allowed is False
