"""Tests for the Workday Review-page validation gate.

Uses static body-text fixtures so we never depend on a live Playwright browser
or a captured DOM snapshot for the regex path. Future scoped-selector
detectors will get their own DOM-locator-level tests via AsyncMock.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from job_hunt.services.workday.review_gate import (
    ISSUE_DATE_MISMATCH,
    ISSUE_DUPLICATE_UPLOAD,
    ISSUE_EXPERIENCE_MISSING,
    ISSUE_GPA_MISMATCH,
    ISSUE_LINKEDIN_INVALID,
    ISSUE_ROLE_DESCRIPTION_MISSING,
    ISSUE_TITLE_MISMATCH,
    ReviewIssue,
    detect_review_issues,
    detect_review_issues_from_text,
    issues_to_payload,
    review_needs_repair,
    review_validation_messages,
)


_EXPERIENCE = [
    {
        "title": "Data Analyst Intern",
        "company": "Acme",
        "location": "City, Region, Country",
        "start_year": "2026",
        "start_month": "1",
        "end_year": "2026",
        "end_month": "3",
        "description": "Engineered a pipeline.",
    }
]
_EDUCATION = [
    {
        "school": "Example University",
        "degree": "Master's Degree",
        "field": "Data Analytics",
        "gpa": "3.84",
    }
]


def _detect(text: str) -> list[ReviewIssue]:
    return detect_review_issues_from_text(
        text, experience_entries=_EXPERIENCE, education_entries=_EDUCATION
    )


def test_clean_review_emits_no_issues() -> None:
    body = (
        "Work Experience Data Analyst Intern Acme 01/2026 to 03/2026 "
        "Role Description Engineered a pipeline. "
        "Education Example University Master's Degree Data Analytics 3.84 "
        "Resume.pdf Successfully Uploaded "
        "Social Network URLs https://www.linkedin.com/in/test"
    )
    assert _detect(body) == []


def test_missing_experience_block_is_flagged() -> None:
    body = "Work Experience No Response Education No Response"
    codes = [issue.code for issue in _detect(body)]
    assert ISSUE_EXPERIENCE_MISSING in codes


def test_title_mismatch_is_flagged() -> None:
    body = "Work Experience Some Other Title Acme 01/2026 03/2026 Education 3.84"
    codes = [issue.code for issue in _detect(body)]
    assert ISSUE_TITLE_MISMATCH in codes


def test_date_mismatch_is_flagged() -> None:
    body = "Work Experience Data Analyst Intern Acme 12/2001 to 12/2026 Education 3.84"
    codes = [issue.code for issue in _detect(body)]
    assert ISSUE_DATE_MISMATCH in codes


def test_padded_dates_pass_validation() -> None:
    body = "Work Experience Data Analyst Intern Acme 01/2026 03/2026 Role Description x. Education 3.84"
    codes = [issue.code for issue in _detect(body)]
    assert ISSUE_DATE_MISMATCH not in codes


def test_unpadded_dates_pass_validation() -> None:
    body = "Work Experience Data Analyst Intern Acme 1/2026 3/2026 Role Description x. Education 3.84"
    codes = [issue.code for issue in _detect(body)]
    assert ISSUE_DATE_MISMATCH not in codes


def test_role_description_missing_is_flagged() -> None:
    body = "Data Analyst Intern 01/2026 03/2026 Role Description No Response Education 3.84"
    codes = [issue.code for issue in _detect(body)]
    assert ISSUE_ROLE_DESCRIPTION_MISSING in codes


def test_gpa_mismatch_is_flagged_when_expected_value_missing() -> None:
    body = "Data Analyst Intern 01/2026 03/2026 Role Description x Education shows 3.50"
    codes = [issue.code for issue in _detect(body)]
    assert ISSUE_GPA_MISMATCH in codes


def test_invalid_linkedin_is_flagged() -> None:
    body = "Data Analyst Intern 01/2026 03/2026 Role Description x Education 3.84 Invalid LinkedIn URL"
    codes = [issue.code for issue in _detect(body)]
    assert ISSUE_LINKEDIN_INVALID in codes


def test_duplicate_upload_card_is_flagged() -> None:
    body = (
        "Data Analyst Intern 01/2026 03/2026 Role Description x Education 3.84 "
        "Candidate_Resume.pdf Successfully Uploaded "
        "Candidate_Resume.pdf "
        "Candidate_Resume.pdf"  # 3rd occurrence => duplicate per code's count > 2 rule
    )
    issues = _detect(body)
    duplicate_codes = [i for i in issues if i.code == ISSUE_DUPLICATE_UPLOAD]
    assert duplicate_codes, [i.code for i in issues]
    assert duplicate_codes[0].details["filename"] == "Candidate_Resume.pdf"


def test_single_card_double_filename_is_not_flagged_as_duplicate() -> None:
    """A single Workday upload card legitimately shows the filename twice."""
    body = (
        "Data Analyst Intern 01/2026 03/2026 Role Description x Education 3.84 "
        "Candidate_Resume.pdf Successfully Uploaded Candidate_Resume.pdf"
    )
    issues = _detect(body)
    assert all(i.code != ISSUE_DUPLICATE_UPLOAD for i in issues)


def test_issues_payload_serialises_for_json() -> None:
    # Provide a body that satisfies title / dates / gpa so only the missing-experience
    # block is flagged, then assert that one issue serialises correctly.
    body = (
        "Work Experience No Response Data Analyst Intern 01/2026 03/2026 "
        "Role Description x. Education 3.84"
    )
    payload = issues_to_payload(_detect(body))
    codes = [item["code"] for item in payload]
    assert codes == [ISSUE_EXPERIENCE_MISSING]
    assert payload[0]["message"] == (
        "Workday Review validation: work experience or education is missing."
    )
    assert payload[0]["details"] == {}


def test_dedupe_drops_duplicate_codes() -> None:
    # set() over the regex matches yields one filename; the dedup helper guarantees
    # we never emit two ISSUE_DUPLICATE_UPLOAD entries for the same (code, message).
    body = (
        "Data Analyst Intern 01/2026 03/2026 Role Description x Education 3.84 "
        "Candidate_Resume.pdf Candidate_Resume.pdf Candidate_Resume.pdf Candidate_Resume.pdf"
    )
    issues = _detect(body)
    upload_codes = [i.code for i in issues if i.code == ISSUE_DUPLICATE_UPLOAD]
    assert len(upload_codes) == 1, upload_codes


def test_async_detect_falls_back_to_empty_when_body_unreadable() -> None:
    page = MagicMock()
    locator = MagicMock()
    locator.inner_text = AsyncMock(side_effect=RuntimeError("locator timeout"))
    page.locator = MagicMock(return_value=locator)

    issues = asyncio.run(
        detect_review_issues(
            page, experience_entries=_EXPERIENCE, education_entries=_EDUCATION
        )
    )
    assert issues == []


def test_async_review_validation_messages_returns_strings() -> None:
    page = MagicMock()
    locator = MagicMock()
    locator.inner_text = AsyncMock(
        return_value=(
            "Work Experience No Response Data Analyst Intern 01/2026 03/2026 "
            "Role Description x. Education 3.84"
        )
    )
    page.locator = MagicMock(return_value=locator)

    messages = asyncio.run(
        review_validation_messages(
            page, experience_entries=_EXPERIENCE, education_entries=_EDUCATION
        )
    )
    assert messages == [
        "Workday Review validation: work experience or education is missing.",
    ]


def test_async_review_needs_repair_is_truthy_when_issues_exist() -> None:
    page = MagicMock()
    locator = MagicMock()
    locator.inner_text = AsyncMock(return_value="Work Experience No Response")
    page.locator = MagicMock(return_value=locator)

    assert asyncio.run(
        review_needs_repair(
            page, experience_entries=_EXPERIENCE, education_entries=_EDUCATION
        )
    )


def test_async_review_needs_repair_is_false_for_clean_page() -> None:
    page = MagicMock()
    locator = MagicMock()
    locator.inner_text = AsyncMock(
        return_value=(
            "Data Analyst Intern 01/2026 03/2026 Role Description x. "
            "Education 3.84 Candidate_Resume.pdf Successfully Uploaded"
        )
    )
    page.locator = MagicMock(return_value=locator)

    assert not asyncio.run(
        review_needs_repair(
            page, experience_entries=_EXPERIENCE, education_entries=_EDUCATION
        )
    )
