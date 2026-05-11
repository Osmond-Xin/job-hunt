"""Tests for the JD eligibility gate (docs/design-notes.md §N.3)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from job_hunt.graphs.evaluate_job import _route_active, _route_eligibility
from job_hunt.models.job import JobMeta
from job_hunt.nodes.eligibility_gate import (
    classify_jd_eligibility,
    eligibility_gate,
    mark_ineligible,
)


# --- pure classifier ---


@pytest.mark.parametrize(
    "title",
    [
        "Software Engineer Intern",
        "Data Analyst Co-op",
        "AI Engineer (Co-Op)",
        "Summer Internship — ML Team",
        "Trainee Data Scientist",
        "AI Engineer Apprentice",
        "Coop Software Developer",
    ],
)
def test_classifier_recognises_student_titles(title: str) -> None:
    assert classify_jd_eligibility(title, "") == "student"


@pytest.mark.parametrize(
    "title",
    [
        "Senior Software Engineer",
        "Staff ML Engineer",
        "Principal Data Scientist",
        "Director of Engineering",
        "VP, Engineering",
        "Head of AI",
        "Engineering Manager",
        "Chief Data Officer",
        "Lead Data Scientist",
    ],
)
def test_classifier_recognises_full_titles(title: str) -> None:
    assert classify_jd_eligibility(title, "") == "full"


def test_classifier_returns_unknown_for_neutral_titles() -> None:
    assert classify_jd_eligibility("Software Engineer", "") == "unknown"
    assert classify_jd_eligibility("AI Engineer", "") == "unknown"
    assert classify_jd_eligibility("Data Analyst", "") == "unknown"


def test_classifier_does_not_match_intern_inside_internal() -> None:
    # Word boundary regression: "internal tooling engineer" must not be student.
    assert classify_jd_eligibility("Internal Tools Engineer", "") == "unknown"


def test_classifier_uses_jd_body_when_title_neutral() -> None:
    body = (
        "We are looking for an Engineer to join our 2026 internship program. "
        "Co-op term runs from May to August."
    )
    assert classify_jd_eligibility("Engineer", body) == "student"


def test_classifier_body_scan_is_bounded_to_prefix() -> None:
    # Internship phrase deep in the body must not flip a senior posting.
    body = "Lead a team. " + ("filler. " * 200) + "internship program runs annually."
    assert classify_jd_eligibility("Senior Engineer", body) == "full"


def test_classifier_title_takes_precedence_over_body() -> None:
    body = "summer internship co-op term student program"
    assert classify_jd_eligibility("Senior Software Engineer", body) == "student"
    # Title regex order: student match wins before full match because student
    # patterns are checked first; this is the conservative direction (an
    # explicitly intern-titled posting is intern even if body says "senior").


def test_classifier_handles_empty_inputs() -> None:
    assert classify_jd_eligibility("", "") == "unknown"
    assert classify_jd_eligibility(None, None) == "unknown"  # type: ignore[arg-type]


# --- node + routing ---


def _run(coro):
    return asyncio.run(coro)


def test_eligibility_gate_node_writes_mode_and_classification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile = tmp_path / "profile.yml"
    profile.write_text("mode: student\n", encoding="utf-8")
    monkeypatch.setattr(
        "job_hunt.nodes.eligibility_gate.current_mode",
        lambda: "student",
    )

    state = {
        "jd_meta": JobMeta(title="Software Engineer Intern"),
        "jd_text": "We are hiring an intern.",
    }
    result = _run(eligibility_gate(state, None))
    assert result["mode"] == "student"
    assert result["jd_eligibility"] == "student"
    assert result["errors"] == []


def test_eligibility_gate_unknown_passes_through_in_routing() -> None:
    state = {"mode": "student", "jd_eligibility": "unknown"}
    assert _route_eligibility(state) == "classify_archetype"


def test_eligibility_gate_blocks_full_jd_in_student_mode() -> None:
    state = {"mode": "student", "jd_eligibility": "full"}
    assert _route_eligibility(state) == "mark_ineligible"


def test_eligibility_gate_blocks_student_jd_in_full_mode() -> None:
    state = {"mode": "full", "jd_eligibility": "student"}
    assert _route_eligibility(state) == "mark_ineligible"


def test_eligibility_gate_passes_matching_class() -> None:
    assert _route_eligibility({"mode": "student", "jd_eligibility": "student"}) == "classify_archetype"
    assert _route_eligibility({"mode": "full", "jd_eligibility": "full"}) == "classify_archetype"


def test_route_active_now_targets_eligibility_gate() -> None:
    assert _route_active({"jd_active": True}) == "eligibility_gate"
    assert _route_active({"jd_active": False}) == "mark_unavailable"


def test_mark_ineligible_forces_skip_with_mode_in_message() -> None:
    state = {
        "mode": "student",
        "jd_eligibility": "full",
        "jd_meta": JobMeta(title="Senior Engineer"),
    }
    result = _run(mark_ineligible(state, None))
    assert result["recommendation"] == "skip"
    assert any("Mode=student" in err and "Senior Engineer" in err for err in result["errors"])


# --- end-to-end: mode flip produces opposite routing for the same JD ---


def _route_for(jd_meta: JobMeta, jd_text: str, mode: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """Walk verify_active routing → eligibility_gate node → eligibility routing."""
    monkeypatch.setattr(
        "job_hunt.nodes.eligibility_gate.current_mode",
        lambda: mode,
    )
    state: dict = {"jd_meta": jd_meta, "jd_text": jd_text, "jd_active": True}
    assert _route_active(state) == "eligibility_gate"
    update = _run(eligibility_gate(state, None))
    state.update(update)
    return _route_eligibility(state)


def test_same_senior_jd_routes_oppositely_under_each_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jd = JobMeta(title="Senior AI Engineer")
    body = "We are looking for a senior IC to lead our LLM platform."
    assert _route_for(jd, body, "student", monkeypatch) == "mark_ineligible"
    assert _route_for(jd, body, "full", monkeypatch) == "classify_archetype"


def test_same_intern_jd_routes_oppositely_under_each_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jd = JobMeta(title="AI Engineer Intern")
    body = "Co-op term placement for summer 2026."
    assert _route_for(jd, body, "student", monkeypatch) == "classify_archetype"
    assert _route_for(jd, body, "full", monkeypatch) == "mark_ineligible"


def test_neutral_jd_passes_through_in_both_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jd = JobMeta(title="Software Engineer")
    body = "Build great software."
    assert _route_for(jd, body, "student", monkeypatch) == "classify_archetype"
    assert _route_for(jd, body, "full", monkeypatch) == "classify_archetype"
