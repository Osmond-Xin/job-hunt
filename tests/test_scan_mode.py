"""Tests for scan.py mode-aware filtering (docs/design-notes.md §N.3)."""

from __future__ import annotations

from job_hunt.services.scan import (
    _augment_query_for_mode,
    _company_matches_mode,
    _select_title_filter,
)


def test_select_title_filter_prefers_mode_specific_group() -> None:
    raw = {
        "student": {"positive": ["Intern"], "negative": ["Senior"]},
        "full": {"positive": ["Senior"], "negative": ["Intern"]},
        "positive": ["legacy"],
        "negative": ["legacy-neg"],
    }
    pos, neg = _select_title_filter(raw, "student")
    assert pos == ["intern"]
    assert neg == ["senior"]
    pos, neg = _select_title_filter(raw, "full")
    assert pos == ["senior"]
    assert neg == ["intern"]


def test_select_title_filter_falls_back_to_legacy_top_level() -> None:
    raw = {"positive": ["AI Engineer"], "negative": ["Sales"]}
    pos, neg = _select_title_filter(raw, "student")
    assert pos == ["ai engineer"]
    assert neg == ["sales"]
    pos, neg = _select_title_filter(raw, "full")
    assert pos == ["ai engineer"]
    assert neg == ["sales"]


def test_select_title_filter_empty_yields_empty_lists() -> None:
    pos, neg = _select_title_filter({}, "student")
    assert pos == []
    assert neg == []


def test_select_title_filter_partial_mode_block_uses_present_keys() -> None:
    raw = {"student": {"positive": ["Intern"]}, "positive": ["legacy"]}
    pos, neg = _select_title_filter(raw, "student")
    assert pos == ["intern"]
    assert neg == []


def test_company_without_tags_matches_both_modes() -> None:
    item = {"name": "Generic ATS Co"}
    assert _company_matches_mode(item, "student") is True
    assert _company_matches_mode(item, "full") is True


def test_company_tagged_intern_matches_student_only() -> None:
    item = {"name": "TalentEgg-like", "eligibility_tags": ["intern", "coop"]}
    assert _company_matches_mode(item, "student") is True
    assert _company_matches_mode(item, "full") is False


def test_company_tagged_full_matches_full_only() -> None:
    item = {"name": "Senior-only Co", "eligibility_tags": ["full"]}
    assert _company_matches_mode(item, "student") is False
    assert _company_matches_mode(item, "full") is True


def test_company_tagged_for_both_matches_both() -> None:
    item = {"name": "Both", "eligibility_tags": ["intern", "full_time"]}
    assert _company_matches_mode(item, "student") is True
    assert _company_matches_mode(item, "full") is True


def test_company_tags_normalise_whitespace_and_case() -> None:
    item = {"name": "Mixed", "eligibility_tags": ["  STUDENT ", " "]}
    assert _company_matches_mode(item, "student") is True
    assert _company_matches_mode(item, "full") is False


def test_company_with_only_blank_tags_matches_both() -> None:
    item = {"name": "Blank", "eligibility_tags": ["", "   "]}
    assert _company_matches_mode(item, "student") is True
    assert _company_matches_mode(item, "full") is True


# --- _augment_query_for_mode ---


def test_augment_query_passes_through_in_full_mode() -> None:
    q = 'site:shopify.com/careers "Data Analyst"'
    assert _augment_query_for_mode(q, "full") == q


def test_augment_query_appends_constraint_in_student_mode() -> None:
    q = 'site:shopify.com/careers "Data Analyst"'
    out = _augment_query_for_mode(q, "student")
    assert out.startswith(q)
    assert '"Intern"' in out
    assert '"Co-op"' in out
    assert '"New Grad"' in out
    # Constraint is wrapped in parens so search engines treat it as one AND group.
    assert out.endswith(")")
    assert " OR " in out


def test_augment_query_returns_blank_when_query_blank() -> None:
    assert _augment_query_for_mode("", "student") == ""
    assert _augment_query_for_mode("   ", "student") == ""


def test_augment_query_only_acts_on_student() -> None:
    q = "test"
    assert _augment_query_for_mode(q, "full") == q
    assert _augment_query_for_mode(q, "unknown") == q
    assert "Intern" in _augment_query_for_mode(q, "student")
