"""What every ATS driver looks like from the outside.

Imports nothing else in this package on purpose: the concrete drivers import
this, the registry imports the drivers, and a single module holding both the
protocol and the driver list would be a cycle (docs/apply-seam-plan.md §3.1).

The split that matters here is ``fill`` from ``submit``. A driver that could
submit inside ``fill`` would own the auto-submit gate, once per ATS, and the
whole point is that the gate has one implementation in the session. So the
session decides *whether* to submit -- it holds the policy -- and the driver
decides *how*, because only it knows which button.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class ApplyContext:
    """Everything a driver needs about the application it is filling.

    A frozen bundle rather than a growing keyword list: every driver signature
    would otherwise gain an argument each time the apply flow learns a new fact.
    """

    company: str | None = None
    role: str | None = None
    pdf: Path | None = None
    cover_letter_pdf: Path | None = None
    artifact_dir: Path | None = None
    report_context: dict = field(default_factory=dict)
    profile_values: dict = field(default_factory=dict)


OUTCOME_FILLED = "filled"                  # reached Review; nothing left to fill
OUTCOME_INCOMPLETE = "incomplete"          # filled what it could; not at Review
OUTCOME_LOGIN_REQUIRED = "login_required"  # the session is not signed in
OUTCOME_BLOCKED = "blocked"                # the driver could not proceed

# There is deliberately no OUTCOME_SUBMITTED. Submitting is `submit()`, and its
# result is a SubmitOutcome -- a fill result that could say "submitted" is how
# the gate ends up inside the driver.

# Only OUTCOME_FILLED means "the form is finished and the Review step is on
# screen". Everything else must keep the gate shut, and the gate checks the
# outcome rather than inferring readiness from `required_empty` being empty:
# a flow that stalled before Review has nothing to report as required, so an
# empty list there means "we never got far enough to look", not "nothing is
# missing". A 2026-09-09 review found exactly that reading a LinkedIn
# `stuck` result as ready to submit.
READY_OUTCOMES = frozenset({OUTCOME_FILLED})


class Blocker(BaseModel):
    """One reason an application is not ready to send.

    ATS-neutral by design. Workday's review gate has its own richer
    ``ReviewIssue``; the driver maps it into this at its edge rather than
    putting a Workday type in the shared contract, which would make the LinkedIn
    driver import a type it never populates.
    """

    code: str
    message: str
    details: dict[str, str] = Field(default_factory=dict)


class AtsResult(BaseModel):
    """What one pass over an application form accomplished."""

    outcome: str
    filled: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    answers: list[dict[str, str]] = Field(default_factory=list)
    required_empty: list[str] = Field(default_factory=list)
    uploads: list[str] = Field(default_factory=list)
    blockers: list[Blocker] = Field(default_factory=list)

    @property
    def ready_to_submit(self) -> bool:
        """The form is finished, at Review, with nothing outstanding.

        The outcome check is not redundant with the two emptiness checks. A
        flow that stalled before Review reports no required fields because it
        never reached the page that lists them -- so emptiness alone reads a
        stall as readiness.

        Not the same as "may be sent": that is the session's call, and it
        weighs the operator's three authorisation keys on top of this.
        """
        return (
            self.outcome in READY_OUTCOMES
            and not self.required_empty
            and not self.blockers
        )


class SubmitOutcome(BaseModel):
    """What happened when the final Submit was clicked.

    ``state`` is three-valued rather than a bool because the third value is
    real: an ATS can accept the click while the confirmation navigation times
    out. Reporting that as failure invites a second application to the same
    employer; reporting it as success records one that may not exist. It is
    ``unknown``, and it is the operator's to resolve.
    """

    state: Literal["confirmed", "rejected", "unknown"]
    evidence: str = ""

    @property
    def needs_reconciliation(self) -> bool:
        return self.state == "unknown"


@runtime_checkable
class AtsDriver(Protocol):
    """One ATS, from the session's point of view.

    ``matches_url`` is a hint available before navigation; ``matches_page`` is
    the answer that counts, because an employer's careers domain may redirect
    into a tenant of an ATS the URL never mentioned.
    """

    name: str

    def matches_url(self, url: str) -> bool: ...

    async def matches_page(self, page) -> bool: ...

    async def fill(self, page, ctx) -> AtsResult: ...

    async def submit(self, page, ctx) -> SubmitOutcome: ...
