"""A submit that was clicked and never confirmed must not be clicked again.

The one behaviour this refactor adds rather than moves. It exists because
``SubmitOutcome`` has three states instead of two: an ATS can accept the click
while the confirmation never arrives, and the honest answer is neither "sent"
nor "not sent".

A return value cannot enforce that by itself -- the process exits, and the next
`job-hunt apply` starts knowing nothing -- so the attempt is written down before
the click and resolved after it. What is being prevented is a second real
application to the same employer.

Two things a 2026-09-09 review found wrong with the first version, both pinned
below: it was keyed on the artifact directory, whose name carries the date and a
truncated role (so a next-day retry looked in the wrong place, and two similar
roles shared a guard); and the write went through the run log, which swallows
every exception, so the record could silently not exist.
"""

from __future__ import annotations

import pytest

from job_hunt.services.apply.artifacts import _apply_artifact_dir, application_id
from job_hunt.services.web import apply_run_log


@pytest.fixture(autouse=True)
def attempt_log(tmp_path, monkeypatch):
    """Point the attempt log at a temp file; never touch the real data/."""
    path = tmp_path / "submit-attempts.jsonl"
    monkeypatch.setattr(apply_run_log, "ATTEMPT_LOG", path)
    return path


ACME = application_id("Acme", "Senior Software Engineer, Platform")


def _attempt(app: str = ACME, art="artifacts/apply/x"):
    apply_run_log.record_submit_attempt(app, art, "https://acme/apply")


def _resolve(state: str, app: str = ACME):
    apply_run_log.record_submit_resolution(app, state, "evidence")


def test_nothing_recorded_means_nothing_unresolved() -> None:
    assert apply_run_log.unresolved_submit_attempt(ACME) is None


def test_an_attempt_with_no_resolution_blocks() -> None:
    """The crash case: the process died between the click and the answer."""
    _attempt()
    assert apply_run_log.unresolved_submit_attempt(ACME) == "artifacts/apply/x"


def test_an_unknown_resolution_blocks() -> None:
    _attempt()
    _resolve("unknown")
    assert apply_run_log.unresolved_submit_attempt(ACME) == "artifacts/apply/x"


def test_a_confirmed_submission_does_not_block() -> None:
    _attempt()
    _resolve("confirmed")
    assert apply_run_log.unresolved_submit_attempt(ACME) is None


def test_a_rejected_submission_does_not_block() -> None:
    """Rejected is a definite "not sent" -- the button was never found. Retrying
    is safe, and blocking would strand the operator for a reason that does not
    apply."""
    _attempt()
    _resolve("rejected")
    assert apply_run_log.unresolved_submit_attempt(ACME) is None


def test_a_later_unknown_blocks_even_after_an_earlier_success() -> None:
    _attempt(); _resolve("confirmed")
    _attempt(); _resolve("unknown")
    assert apply_run_log.unresolved_submit_attempt(ACME) is not None


# --- what the review found ---------------------------------------------------


def test_the_guard_survives_the_next_day() -> None:
    """Keyed on the artifact directory, this failed: the directory name starts
    with today's date, so a retry tomorrow looked somewhere else and found
    nothing to stop it."""
    _attempt(art="artifacts/apply/2026-09-09-acme-senior-software-engineer")
    _resolve("unknown")
    # Tomorrow's run computes the same application id from the same inputs.
    assert application_id("Acme", "Senior Software Engineer, Platform") == ACME
    assert apply_run_log.unresolved_submit_attempt(ACME) is not None


def test_two_roles_that_share_four_words_do_not_share_a_guard() -> None:
    """The artifact directory takes the first four words of the role, so these
    two collided: one being unresolved blocked the other."""
    a = application_id("Acme", "Senior Software Engineer, Platform A")
    b = application_id("Acme", "Senior Software Engineer, Platform B")
    assert a != b
    _attempt(app=a); _resolve("unknown", app=a)
    assert apply_run_log.unresolved_submit_attempt(a) is not None
    assert apply_run_log.unresolved_submit_attempt(b) is None


def test_the_artifact_directory_really_does_collide() -> None:
    """Not a hypothetical -- this is why the identity had to stop being the
    directory name."""
    a = _apply_artifact_dir("Acme", "Senior Software Engineer, Platform A")
    b = _apply_artifact_dir("Acme", "Senior Software Engineer, Platform B")
    assert a == b


def test_recording_an_attempt_raises_when_it_cannot_be_written(monkeypatch, tmp_path) -> None:
    """The record is the only thing that would stop a repeat. If it cannot be
    written the caller has to know, so this one does not swallow like the run
    log does -- a safety record nobody managed to write is exactly the case it
    exists for."""
    monkeypatch.setattr(apply_run_log, "ATTEMPT_LOG", tmp_path / "nope" / "x.jsonl")
    monkeypatch.setattr(
        apply_run_log.Path, "mkdir",
        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only filesystem")),
    )
    with pytest.raises(OSError):
        apply_run_log.record_submit_attempt(ACME, "artifacts/apply/x", "https://acme")


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
