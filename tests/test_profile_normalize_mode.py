"""Tests for _normalize_profile()'s mode-aware narrative selection (§N.3)."""

from __future__ import annotations

from job_hunt.nodes.context import _normalize_profile

_RAW = {
    "mode": "student",
    "candidate": {
        "full_name": "Yi Xin",
        "email": "x@example.com",
    },
    "target_roles": {
        "primary": ["AI Engineer"],
        "archetypes": [
            {"name": "AI Engineer Intern", "eligibility": "student"},
            {"name": "Data Analyst Co-op", "eligibility": "student"},
            {"name": "Senior AI Engineer", "eligibility": "full"},
        ],
    },
    "compensation": {
        "minimum": "CAD 80K",
        "student_minimum": "CAD 22/hr",
    },
    "narrative": {
        "headline": "20-year veteran",
        "exit_story": "Full-mode story.",
        "superpowers": ["Senior leadership"],
        "student": {
            "headline": "Co-op candidate",
            "exit_story": "Student-mode story.",
            "superpowers": ["Co-op execution"],
        },
    },
    "location": {"city": "Niagara Falls", "country": "Canada"},
}


def test_normalize_picks_student_narrative_when_mode_student() -> None:
    normalized = _normalize_profile(_RAW)
    assert normalized["mode"] == "student"
    assert normalized["exit_narrative"] == "Student-mode story."
    assert normalized["skills"] == ["Co-op execution"]
    assert normalized["preferred_archetypes"] == [
        "AI Engineer Intern",
        "Data Analyst Co-op",
    ]


def test_normalize_picks_full_narrative_when_mode_full() -> None:
    raw = dict(_RAW, mode="full")
    normalized = _normalize_profile(raw)
    assert normalized["mode"] == "full"
    assert normalized["exit_narrative"] == "Full-mode story."
    assert normalized["skills"] == ["Senior leadership"]
    assert normalized["preferred_archetypes"] == ["Senior AI Engineer"]


def test_normalize_falls_back_to_top_level_narrative_when_student_block_missing() -> None:
    raw = {
        **_RAW,
        "mode": "student",
        "narrative": {
            "exit_story": "Only top-level narrative.",
            "superpowers": ["Cross-cutting"],
        },
    }
    normalized = _normalize_profile(raw)
    assert normalized["exit_narrative"] == "Only top-level narrative."
    assert normalized["skills"] == ["Cross-cutting"]


def test_normalize_defaults_to_full_when_mode_absent() -> None:
    raw = {k: v for k, v in _RAW.items() if k != "mode"}
    normalized = _normalize_profile(raw)
    assert normalized["mode"] == "full"
    assert normalized["exit_narrative"] == "Full-mode story."


def test_normalize_picks_student_min_salary_in_student_mode() -> None:
    normalized = _normalize_profile(_RAW)
    # student_minimum "CAD 22/hr" → 22 (less than 1000, no thousand multiplier).
    assert normalized["min_salary"] == 22


def test_normalize_picks_full_min_salary_in_full_mode() -> None:
    raw = dict(_RAW, mode="full")
    normalized = _normalize_profile(raw)
    # minimum "CAD 80K" → digits 80 → 80*1000 = 80000.
    assert normalized["min_salary"] == 80000


def test_normalize_archetype_backstop_when_no_eligibility_tags() -> None:
    raw = {
        "mode": "student",
        "candidate": {"full_name": "X"},
        "target_roles": {
            "archetypes": [
                {"name": "Generic Role A"},
                {"name": "Generic Role B"},
            ],
        },
        "narrative": {},
    }
    normalized = _normalize_profile(raw)
    # No mode-tagged archetypes → fall back to unfiltered list.
    assert normalized["preferred_archetypes"] == ["Generic Role A", "Generic Role B"]


def test_normalize_handles_legacy_flat_schema_without_mode() -> None:
    # Flat schemas (no candidate dict) are returned as-is.
    raw = {"name": "X", "email": "x@example.com"}
    normalized = _normalize_profile(raw)
    assert normalized == raw
