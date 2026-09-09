"""The session fills through a driver, and never around one.

Written because two independent reviews found the same defect that 1,118 passing
tests could not see: `AtsDriver.fill` had been written, tested in isolation, and
then never called. The session still hand-rolled a LinkedIn branch and a Workday
branch inline, reaching a driver only to submit. A third ATS would have been
submitted without ever being filled.

Testing that a protocol exists is not the same as testing that anything uses it.
These are the second kind.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from job_hunt.services.web import apply_session


SOURCE = Path(inspect.getfile(apply_session)).read_text(encoding="utf-8")


def _calls_in(func_name: str) -> set[str]:
    """Every attribute call made inside one function, as `obj.attr`."""
    tree = ast.parse(SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return {
                f"{c.func.value.id}.{c.func.attr}"
                for c in ast.walk(node)
                if isinstance(c, ast.Call)
                and isinstance(c.func, ast.Attribute)
                and isinstance(c.func.value, ast.Name)
            }
    raise AssertionError(f"{func_name} not found in apply_session")


def test_the_session_fills_through_a_driver() -> None:
    """The defect this file exists for: `fill` written and never called."""
    assert "driver.fill" in _calls_in("_open_apply_page")


def test_the_session_submits_through_the_same_driver() -> None:
    assert "driver.submit" in _calls_in("_open_apply_page")


def test_the_session_does_not_reach_around_the_drivers() -> None:
    """No ATS-specific flow may be called from the session directly. Each of
    these is something a driver owns; the session calling one means an ATS the
    registry does not know about is being driven anyway."""
    forbidden = {
        "_maybe_linkedin_easy_apply",
        "_workday_advance_all_steps",
        "_try_workday_final_submit",
        "_collect_workday_review_issues",
        "_workday_resume_was_uploaded",
    }
    leaked = sorted(name for name in forbidden if f"{name}(" in SOURCE)
    assert leaked == [], f"session calls ATS-specific code directly: {leaked}"


def test_the_session_holds_no_second_submit_gate() -> None:
    """Every refusal goes through submit_gate, so there is one place where the
    rule lives and one place a test has to pin."""
    tree = ast.parse(SOURCE)
    emitted = {
        arg.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "emit"
        for arg in node.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    }
    # `auto_submit.gated` is submit_gate's to emit. The session may name it once,
    # for a submit that was attempted and came back rejected -- which is not a
    # gate decision, because the gate had already said yes.
    assert "auto_submit.bypassed" not in emitted


def test_the_session_says_nothing_in_colour() -> None:
    """Rich markup in services/ is the mixing the Reporter exists to undo. The
    session says what happened; cli/ decides what that looks like."""
    for tag in ("[red]", "[yellow]", "[green]", "[dim]", "[bold]"):
        assert tag not in SOURCE, f"{tag} markup leaked into the session"


def test_the_session_asks_the_registry_rather_than_naming_an_ats() -> None:
    """`"myworkdayjobs.com" in page.url` used to appear thirteen times. The
    session should not know the name of a single ATS."""
    assert "myworkdayjobs" not in SOURCE
    assert "linkedin.com" not in SOURCE


def test_authorisation_defaults_to_refusing() -> None:
    """A caller that forgets to pass the three keys must get no auto-submit,
    not an unauthorised one. The default is the safe direction."""
    signature = inspect.signature(apply_session._open_apply_page)
    default = signature.parameters["authorisation"].default
    assert default.allowed is False
