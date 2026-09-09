"""The session fills through a driver, and never around one.

Written because two independent reviews found the same defect that 1,118 passing
tests could not see: ``AtsDriver.fill`` had been written, tested in isolation,
and then never called.

The first version of this file scanned the AST for `driver.fill`, and a third
review pointed out that it passes on ``if False: await driver.fill(...)``. It
proved the call was written, which was never the thing in doubt. So the session
runs here, against a fake page and a fake driver, and the assertions are about
what actually happened.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

from job_hunt.services.web import apply_session, ats_registry
from job_hunt.services.web.ats_contract import (
    OUTCOME_FILLED,
    OUTCOME_INCOMPLETE,
    ApplyContext,
    AtsResult,
    Blocker,
    SubmitOutcome,
)
from job_hunt.services.web.reporter import RecordingReporter
from job_hunt.services.web.submit_gate import GateDecision


class FakePage:
    """Just enough page for the session to get through its sequence."""

    def __init__(self, url: str = "https://acme.wd5.myworkdayjobs.com/job/1") -> None:
        self.url = url
        self.clicks: list[str] = []

    async def goto(self, *a, **k): ...
    async def title(self): return "Acme — Engineer"
    async def wait_for_timeout(self, *a, **k): ...
    async def wait_for_load_state(self, *a, **k): ...
    async def screenshot(self, path=None, **k):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"jpg")
    def locator(self, *a, **k): return _EmptyLocator()


class _EmptyLocator:
    async def all_inner_texts(self): return []
    async def inner_text(self, **k): return ""
    async def count(self): return 0


class SpyDriver:
    """Records the order the session called it in."""

    name = "spy"

    def __init__(self, *, outcome=OUTCOME_FILLED, required_empty=None, blockers=None):
        self.calls: list[str] = []
        self._outcome = outcome
        self._required_empty = required_empty or []
        self._blockers = blockers or []

    def matches_url(self, url: str) -> bool: return True
    async def matches_page(self, page) -> bool: return True

    async def fill(self, page, ctx: ApplyContext) -> AtsResult:
        self.calls.append("fill")
        return AtsResult(
            outcome=self._outcome,
            filled=["First Name"],
            required_empty=list(self._required_empty),
            blockers=list(self._blockers),
        )

    async def submit(self, page, ctx: ApplyContext) -> SubmitOutcome:
        self.calls.append("submit")
        return SubmitOutcome(state="confirmed", evidence="thanks page")


def _run(driver, tmp_path, *, auto_submit: bool = True, authorisation=None):
    """Drive the real session against a fake browser, and return the spy."""
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(ats_registry, "DRIVERS", [driver])
        monkey.setattr(apply_session, "_BROWSER_PROFILE", tmp_path / "profile")
        monkey.setattr(apply_run_log_stub := apply_session.apply_run_log,
                       "ATTEMPT_LOG", tmp_path / "attempts.jsonl")
        (tmp_path / "art").mkdir(parents=True, exist_ok=True)
        page = FakePage()
        reporter = RecordingReporter()
        asyncio.run(
            apply_session._run_apply_flow(
                page,
                art_dir=tmp_path / "art",
                url=page.url,
                company="Acme",
                role="Engineer",
                pdf=None,
                cover_letter_pdf=None,
                auto_fill=True,
                # One decision, one name: the session takes only the
                # authorisation now. `auto_submit=False` here means the
                # operator did not ask, which is a refused authorisation.
                authorisation=authorisation
                or GateDecision(
                    allowed=auto_submit,
                    reason="" if auto_submit else "not_requested",
                ),
                report_context={},
                reporter=reporter,
            )
        )
        return driver, reporter
    finally:
        monkey.undo()


def test_the_session_fills_through_the_driver(tmp_path) -> None:
    """The defect this file exists for."""
    driver, _ = _run(SpyDriver(), tmp_path, auto_submit=False)
    assert "fill" in driver.calls


def test_filling_happens_before_submitting(tmp_path) -> None:
    driver, _ = _run(SpyDriver(), tmp_path, auto_submit=True)
    assert driver.calls == ["fill", "submit"]


def test_no_submit_without_auto_submit(tmp_path) -> None:
    driver, _ = _run(SpyDriver(), tmp_path, auto_submit=False)
    assert "submit" not in driver.calls


def test_no_submit_when_the_operator_did_not_authorise_it(tmp_path) -> None:
    driver, _ = _run(
        SpyDriver(), tmp_path, auto_submit=True,
        authorisation=GateDecision(allowed=False, reason="not_requested"),
    )
    assert "submit" not in driver.calls


def test_no_submit_when_a_required_field_is_empty(tmp_path) -> None:
    driver, _ = _run(
        SpyDriver(required_empty=["Phone Number"]), tmp_path, auto_submit=True
    )
    assert "submit" not in driver.calls


def test_no_submit_when_a_blocker_stands(tmp_path) -> None:
    driver, _ = _run(
        SpyDriver(blockers=[Blocker(code="date_mismatch", message="dates wrong")]),
        tmp_path, auto_submit=True,
    )
    assert "submit" not in driver.calls


def test_no_submit_when_the_form_never_reached_review(tmp_path) -> None:
    """The one a review caught: a stalled flow reports no required fields,
    because it never saw the page that lists them. Emptiness is not readiness."""
    driver, _ = _run(SpyDriver(outcome=OUTCOME_INCOMPLETE), tmp_path, auto_submit=True)
    assert "submit" not in driver.calls


# --- structural rules the runtime test cannot express ------------------------

SOURCE = Path(inspect.getfile(apply_session)).read_text(encoding="utf-8")


def test_the_session_does_not_reach_around_the_drivers() -> None:
    forbidden = [
        "_maybe_linkedin_easy_apply",
        "_workday_advance_all_steps",
        "_try_workday_final_submit",
        "_collect_workday_review_issues",
        "_workday_resume_was_uploaded",
    ]
    leaked = sorted(name for name in forbidden if f"{name}(" in SOURCE)
    assert leaked == [], f"session calls ATS-specific code directly: {leaked}"


def test_the_session_says_nothing_in_colour() -> None:
    for tag in ("[red]", "[yellow]", "[green]", "[dim]", "[bold]"):
        assert tag not in SOURCE, f"{tag} markup leaked into the session"


def test_the_session_names_no_ats() -> None:
    assert "myworkdayjobs" not in SOURCE
    assert "linkedin.com" not in SOURCE


def test_the_submitted_question_defaults_to_no() -> None:
    """A caller that forgets the callback gets "not submitted", which leaves the
    tracker row untouched. The other default would record an application nobody
    sent."""
    default = inspect.signature(
        apply_session._open_apply_page
    ).parameters["confirm_submitted"].default
    assert default("Have you manually submitted this application?") is False


def test_authorisation_defaults_to_refusing() -> None:
    """A caller that forgets to pass the three keys gets no auto-submit, not an
    unauthorised one."""
    default = inspect.signature(
        apply_session._open_apply_page
    ).parameters["authorisation"].default
    assert default.allowed is False
