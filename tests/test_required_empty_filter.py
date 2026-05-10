"""Phase 2.3 — lock the false-positive filter semantics in pure-Python form.

The DOM scraper ``_required_empty_fields`` lives in ``cli.py`` and is hard to
test, but everything it produces flows through the helpers in
``services/workday/required_empty.py`` before reaching the user. These tests
keep that downstream filter honest.
"""

from __future__ import annotations

from job_hunt.services.workday.required_empty import (
    country_phone_code_was_filled,
    dedupe_preserve_order,
    filter_non_blocking_workday_skips,
    filter_required_empty_fields,
    is_country_phone_code_label,
    is_workday_date_helper,
)


# --- is_workday_date_helper -------------------------------------------------

def test_workday_date_helper_matches_quadreal_2026_label() -> None:
    label = (
        "From* current value is 1/2026 01 / 2026 To* current value is 3/2026 03 / 2026"
    )
    assert is_workday_date_helper(label)


def test_workday_date_helper_matches_arbitrary_year() -> None:
    label = "From current value is 12/2024 To current value is 6/2025"
    assert is_workday_date_helper(label)


def test_workday_date_helper_matches_padded_dates() -> None:
    label = "From* current value is 09/2023 To* current value is 11/2024"
    assert is_workday_date_helper(label)


def test_workday_date_helper_rejects_plain_text() -> None:
    assert not is_workday_date_helper("From start date To end date")
    assert not is_workday_date_helper("Start date *")
    assert not is_workday_date_helper("")


def test_workday_date_helper_rejects_helper_without_dates() -> None:
    label = "From* current value is N/A To* current value is N/A"
    assert not is_workday_date_helper(label)


# --- filter_non_blocking_workday_skips --------------------------------------

def test_filter_skips_drops_only_date_helper() -> None:
    skips = [
        "From* current value is 1/2026 To* current value is 3/2026",
        "Workday eligibility A/B: dropdown not found",
        "Resume not uploaded",
    ]
    assert filter_non_blocking_workday_skips(skips) == [
        "Workday eligibility A/B: dropdown not found",
        "Resume not uploaded",
    ]


def test_filter_skips_dedupes_preserved_order() -> None:
    skips = ["A", "B", "A", "C", "B"]
    assert filter_non_blocking_workday_skips(skips) == ["A", "B", "C"]


# --- filter_required_empty_fields -------------------------------------------

def test_required_filter_drops_country_phone_code_when_chip_filled() -> None:
    required = ["Country Phone Code*", "First Name*"]
    filled = ["Workday Country Phone Code"]
    assert filter_required_empty_fields(required, filled) == ["First Name*"]


def test_required_filter_keeps_country_phone_code_when_chip_not_filled() -> None:
    # Filled list is unrelated to phone code; both required fields survive.
    required = ["Country Phone Code*", "Real Estate License*"]
    filled = ["Workday Phone Device Type"]
    assert filter_required_empty_fields(required, filled) == [
        "Country Phone Code*",
        "Real Estate License*",
    ]


def test_required_filter_drops_date_helper_text() -> None:
    required = [
        "From* current value is 1/2026 To* current value is 3/2026",
        "Real Estate License*",
    ]
    assert filter_required_empty_fields(required, []) == ["Real Estate License*"]


def test_required_filter_drops_substring_overlap_with_filled() -> None:
    required = ["Phone Number*"]
    filled = ["Phone Number"]
    assert filter_required_empty_fields(required, filled) == []


def test_required_filter_keeps_unrelated_required() -> None:
    required = ["GPA*", "Graduation Date*"]
    filled = ["Phone Number", "First Name"]
    assert filter_required_empty_fields(required, filled) == [
        "GPA*",
        "Graduation Date*",
    ]


# --- helper predicates ------------------------------------------------------

def test_is_country_phone_code_label_is_case_insensitive_via_caller() -> None:
    # filter_required_empty_fields lowercases before testing; the predicate
    # itself accepts already-normalised input.
    assert is_country_phone_code_label("country phone code*")
    assert not is_country_phone_code_label("phone number*")


def test_country_phone_code_was_filled_substring_match() -> None:
    assert country_phone_code_was_filled(["workday country phone code"])
    assert not country_phone_code_was_filled(["workday phone device type"])


def test_dedupe_preserve_order_keeps_first_occurrence() -> None:
    assert dedupe_preserve_order(["a", "b", "a", "c", "b", "a"]) == ["a", "b", "c"]
