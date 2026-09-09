"""A submit that was clicked and never confirmed must not be clicked again.

The one behaviour this refactor adds rather than moves. It exists because
``SubmitOutcome`` has three states instead of two: an ATS can accept the click
while the confirmation navigation times out, and the honest answer is neither
"sent" nor "not sent".

A return value cannot enforce that by itself -- the process exits, and the next
`job-hunt apply` starts knowing nothing -- so the attempt is written to the run
log before the click and resolved after it. What is being prevented is a second
real application to the same employer.
"""

from __future__ import annotations

from job_hunt.services.web import apply_run_log


def test_a_clean_directory_has_nothing_unresolved(tmp_path) -> None:
    assert apply_run_log.unresolved_submit_attempt(tmp_path) is None


def test_an_attempt_with_no_resolution_blocks(tmp_path) -> None:
    """The crash case: the process died between the click and the answer."""
    apply_run_log.emit(tmp_path, "submit.attempted", url="https://acme/apply")
    assert apply_run_log.unresolved_submit_attempt(tmp_path) == str(tmp_path)


def test_an_unknown_resolution_blocks(tmp_path) -> None:
    """Clicked, and the confirmation never arrived. This is the case the whole
    three-valued outcome exists for."""
    apply_run_log.emit(tmp_path, "submit.attempted", url="https://acme/apply")
    apply_run_log.emit(tmp_path, "submit.resolved", state="unknown",
                       evidence="no confirmation load within 30s")
    assert apply_run_log.unresolved_submit_attempt(tmp_path) == str(tmp_path)


def test_a_confirmed_submission_does_not_block(tmp_path) -> None:
    apply_run_log.emit(tmp_path, "submit.attempted", url="https://acme/apply")
    apply_run_log.emit(tmp_path, "submit.resolved", state="confirmed",
                       evidence="https://acme/thanks")
    assert apply_run_log.unresolved_submit_attempt(tmp_path) is None


def test_a_rejected_submission_does_not_block(tmp_path) -> None:
    """Rejected is a definite "not sent" -- the button was never found, or the
    click raised. Retrying that is safe, and blocking it would strand the
    operator for a reason that does not apply."""
    apply_run_log.emit(tmp_path, "submit.attempted", url="https://acme/apply")
    apply_run_log.emit(tmp_path, "submit.resolved", state="rejected",
                       evidence="Submit button not located")
    assert apply_run_log.unresolved_submit_attempt(tmp_path) is None


def test_the_last_attempt_is_the_one_that_counts(tmp_path) -> None:
    """An earlier unknown that a later run resolved must not block forever."""
    apply_run_log.emit(tmp_path, "submit.attempted")
    apply_run_log.emit(tmp_path, "submit.resolved", state="unknown", evidence="a")
    apply_run_log.emit(tmp_path, "submit.attempted")
    apply_run_log.emit(tmp_path, "submit.resolved", state="confirmed", evidence="b")
    assert apply_run_log.unresolved_submit_attempt(tmp_path) is None


def test_a_later_unknown_blocks_even_after_an_earlier_success(tmp_path) -> None:
    apply_run_log.emit(tmp_path, "submit.attempted")
    apply_run_log.emit(tmp_path, "submit.resolved", state="confirmed", evidence="a")
    apply_run_log.emit(tmp_path, "submit.attempted")
    apply_run_log.emit(tmp_path, "submit.resolved", state="unknown", evidence="b")
    assert apply_run_log.unresolved_submit_attempt(tmp_path) == str(tmp_path)


def test_other_events_in_the_log_are_ignored(tmp_path) -> None:
    apply_run_log.emit(tmp_path, "session.started", url="https://acme/apply")
    apply_run_log.emit(tmp_path, "auto_submit.gated", reason="required_empty_fields")
    apply_run_log.emit(tmp_path, "session.ended")
    assert apply_run_log.unresolved_submit_attempt(tmp_path) is None


def test_the_gate_refuses_when_an_attempt_is_unresolved() -> None:
    """The lookup and the gate, joined: this is what actually stops the click."""
    from job_hunt.services.web.submit_gate import (
        REASON_UNRESOLVED_ATTEMPT,
        GateDecision,
        may_submit,
    )

    decision = may_submit(
        authorisation=GateDecision(allowed=True),
        driver_name="workday",
        required_empty=[],
        blockers=[],
        unresolved_attempt="artifacts/apply/2026-09-09-acme",
    )
    assert decision.allowed is False
    assert decision.reason == REASON_UNRESOLVED_ATTEMPT
    assert decision.event == "auto_submit.gated"
