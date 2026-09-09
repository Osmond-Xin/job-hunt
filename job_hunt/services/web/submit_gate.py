"""Whether an application may be sent.

One implementation, for every ATS. Before this there were two -- one inside the
LinkedIn flow and one written out again in ``_open_apply_page`` for Workday --
and two implementations of a rule this consequential is one too many: the thing
on the far side is a real application, under a real name, to a real employer.

The decision is pure and the reasons are named, so a test can assert both what
the gate decided and why, without a browser and without reading console output.
Acting on it -- the click, the run-log line -- is the session's.

Note the two kinds of refusal, which the plan's reviewers were right to say
should not share a name:

- **bypassed**: the operator never authorised auto-submit for this run. One of
  the three keys is off. Nothing about the form is wrong.
- **gated**: auto-submit was authorised, and the form itself is not ready.
"""

from __future__ import annotations

from dataclasses import dataclass

# Not authorised -- the operator did not ask for this, or this machine/mode is
# not set up for it. Emitted as `auto_submit.bypassed`.
REASON_NOT_REQUESTED = "not_requested"
REASON_PROFILE_DISABLED = "profile_disabled"
REASON_STUDENT_MODE = "student_mode"

# Authorised, but the application is not ready. Emitted as `auto_submit.gated`.
REASON_NO_DRIVER = "no_driver"
REASON_REVIEW_ISSUES = "review_validation_issues"
REASON_REQUIRED_EMPTY = "required_empty_fields"
REASON_UNRESOLVED_ATTEMPT = "unresolved_previous_attempt"

_BYPASS_REASONS = frozenset(
    {REASON_NOT_REQUESTED, REASON_PROFILE_DISABLED, REASON_STUDENT_MODE}
)


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str = ""
    detail: dict | None = None

    @property
    def event(self) -> str:
        """The run-log event name this decision should be recorded under."""
        if self.allowed:
            return "auto_submit.fired"
        return "auto_submit.bypassed" if self.reason in _BYPASS_REASONS else "auto_submit.gated"


def authorised(*, requested: bool, profile_enabled: bool, mode: str) -> GateDecision:
    """The three keys, evaluated before anything about the form is considered.

    Three because the thing on the other side is irreversible. ``--auto-submit``
    alone is a flag recalled from shell history; ``profile.yml`` alone is a file
    somebody edited months ago; ``mode`` alone is a setting kept for another
    purpose. Any one of them being sufficient would make an accident cheap.
    """
    if not requested:
        return GateDecision(False, REASON_NOT_REQUESTED)
    if not profile_enabled:
        return GateDecision(False, REASON_PROFILE_DISABLED)
    if mode != "full":
        return GateDecision(False, REASON_STUDENT_MODE, {"mode": mode})
    return GateDecision(True)


def may_submit(
    *,
    authorisation: GateDecision,
    driver_name: str | None,
    required_empty: list[str],
    blockers: list,
    unresolved_attempt: str | None = None,
) -> GateDecision:
    """The whole gate: authorisation first, then the state of the form.

    Order is deliberate. An unauthorised run should say so plainly rather than
    reporting whichever field happened to be empty -- the operator did not ask
    to submit, and the form's state is beside the point.
    """
    if not authorisation.allowed:
        return authorisation
    if unresolved_attempt:
        # A previous submit landed with no confirmation. Clicking again could
        # send the same application twice; only a human can say which.
        return GateDecision(
            False, REASON_UNRESOLVED_ATTEMPT, {"artifact_dir": unresolved_attempt}
        )
    if driver_name is None:
        return GateDecision(False, REASON_NO_DRIVER)
    if blockers:
        return GateDecision(
            False, REASON_REVIEW_ISSUES, {"codes": [b.code for b in blockers][:10]}
        )
    if required_empty:
        return GateDecision(
            False, REASON_REQUIRED_EMPTY, {"fields": list(required_empty)[:10]}
        )
    return GateDecision(True)
