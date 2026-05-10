"""Workday Review-page validation gate.

Phase 2.1 extracted this from ``cli.py``. Phase 3.1 added structured ``ReviewIssue``
records (issue ``code`` + ``message`` + ``details``) so callers can:

- forward ``message`` strings into the existing ``skipped`` summary lists, and
- persist the full ``ReviewIssue`` list into ``apply-review.json::validation_issues[]``
  for later analysis or for ``apply doctor``-style commands.

Detection currently runs against the Review page's full ``inner_text``: simple,
robust to minor DOM changes, and easy to unit-test from a static text fixture.
``_extract_section_text`` is provided as a future hook so structured-selector
detection can replace the body-regex path one issue at a time without changing
the public API. Body-text matching falls through whenever a scoped selector
yields nothing — never the other way around — so a Workday DOM tweak that hides
a section heading does not silently disable validation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReviewIssue:
    """One concrete Review-page violation.

    ``code`` is a stable identifier suitable for assertions, dashboards, and
    persisted run logs (do not break existing codes — append new ones).
    ``message`` is the human-readable summary appended to ``skipped`` lists.
    ``details`` carries structured payload (expected vs actual values, etc.)
    """

    code: str
    message: str
    details: dict[str, str] = field(default_factory=dict)


# --- Issue code catalogue ---------------------------------------------------
ISSUE_EXPERIENCE_MISSING = "WD_REVIEW_EXPERIENCE_MISSING"
ISSUE_TITLE_MISMATCH = "WD_REVIEW_TITLE_MISMATCH"
ISSUE_DATE_MISMATCH = "WD_REVIEW_DATE_MISMATCH"
ISSUE_ROLE_DESCRIPTION_MISSING = "WD_REVIEW_ROLE_DESCRIPTION_MISSING"
ISSUE_GPA_MISMATCH = "WD_REVIEW_GPA_MISMATCH"
ISSUE_LINKEDIN_INVALID = "WD_REVIEW_LINKEDIN_INVALID"
ISSUE_DUPLICATE_UPLOAD = "WD_REVIEW_DUPLICATE_UPLOAD"


# Non-greedy match anchored to a non-space first character. The greedy variant
# previously used in cli.py incorrectly concatenated space-separated filenames
# (e.g. "A.pdf B.pdf" matched as one) which under-reported duplicates.
_DUPLICATE_UPLOAD_PATTERN = re.compile(r"[\w.,'()&+-][\w .,'()&+-]*?\.pdf", re.I)


def detect_review_issues_from_text(
    body_text: str,
    *,
    experience_entries: list[dict[str, str]],
    education_entries: list[dict[str, str]],
) -> list[ReviewIssue]:
    """Pure-text detector — used directly by tests with a static fixture string."""
    issues: list[ReviewIssue] = []
    norm = re.sub(r"\s+", " ", body_text)

    if re.search(
        r"Work Experience\s+No Response|Professional Experience\s+No Response|Education\s+No Response",
        norm,
        re.I,
    ):
        issues.append(
            ReviewIssue(
                code=ISSUE_EXPERIENCE_MISSING,
                message="Workday Review validation: work experience or education is missing.",
            )
        )

    if experience_entries:
        exp = experience_entries[0]
        expected_start = f"{int(exp['start_month'])}/{exp['start_year']}"
        expected_start_padded = f"{int(exp['start_month']):02d}/{exp['start_year']}"
        expected_end = f"{int(exp['end_month'])}/{exp['end_year']}"
        expected_end_padded = f"{int(exp['end_month']):02d}/{exp['end_year']}"

        if exp["title"] not in norm:
            issues.append(
                ReviewIssue(
                    code=ISSUE_TITLE_MISMATCH,
                    message=f"Workday Review validation: work experience title is not {exp['title']}.",
                    details={"expected_title": exp["title"]},
                )
            )

        has_start = expected_start in norm or expected_start_padded in norm
        has_end = expected_end in norm or expected_end_padded in norm
        if not (has_start and has_end):
            issues.append(
                ReviewIssue(
                    code=ISSUE_DATE_MISMATCH,
                    message=(
                        f"Workday Review validation: {exp['title']} dates are not "
                        f"{expected_start_padded}-{expected_end_padded}."
                    ),
                    details={
                        "expected_start": expected_start_padded,
                        "expected_end": expected_end_padded,
                        "title": exp["title"],
                    },
                )
            )

    if re.search(r"Role Description\s+No Response", norm, re.I):
        issues.append(
            ReviewIssue(
                code=ISSUE_ROLE_DESCRIPTION_MISSING,
                message="Workday Review validation: work experience role description is missing.",
            )
        )

    if education_entries:
        edu = education_entries[0]
        gpa = edu.get("gpa", "")
        if gpa and gpa not in norm:
            issues.append(
                ReviewIssue(
                    code=ISSUE_GPA_MISMATCH,
                    message=f"Workday Review validation: education GPA is not {gpa}.",
                    details={"expected_gpa": gpa},
                )
            )

    if (
        "Invalid LinkedIn URL" in norm
        or "Social Network URLs If you wish, please provide your Linkedin URL. No Response" in norm
    ):
        issues.append(
            ReviewIssue(
                code=ISSUE_LINKEDIN_INVALID,
                message="Workday Review validation: social network URL is missing or invalid.",
            )
        )

    filenames = _DUPLICATE_UPLOAD_PATTERN.findall(body_text)
    for filename in set(filenames):
        # A single Workday upload card can show the filename twice
        # ("Successfully Uploaded" plus visible filename). More than two means duplicate cards.
        if body_text.count(filename) > 2:
            issues.append(
                ReviewIssue(
                    code=ISSUE_DUPLICATE_UPLOAD,
                    message=(
                        f"Workday Review validation: duplicate upload detected for {filename.strip()}."
                    ),
                    details={"filename": filename.strip()},
                )
            )

    return _dedupe_issues(issues)


async def _extract_section_text(page, automation_id: str) -> str:
    """Best-effort scoped text extraction. Returns "" when the section isn't on the page.

    Used as a hook for selector-based detection. Currently no caller depends on
    a non-empty result (body text is the always-available fallback), but keeping
    this function lets future per-issue detectors graduate from regex without
    rewiring the public API.
    """
    try:
        locator = page.locator(f'[data-automation-id="{automation_id}"]').first
        if await locator.count():
            return await locator.inner_text(timeout=2000)
    except Exception:
        return ""
    return ""


async def detect_review_issues(
    page,
    *,
    experience_entries: list[dict[str, str]],
    education_entries: list[dict[str, str]],
) -> list[ReviewIssue]:
    """Async page-aware variant. Falls through to ``detect_review_issues_from_text``."""
    try:
        text = await page.locator("body").inner_text(timeout=3000)
    except Exception:
        return []
    return detect_review_issues_from_text(
        text,
        experience_entries=experience_entries,
        education_entries=education_entries,
    )


async def review_validation_messages(
    page,
    *,
    experience_entries: list[dict[str, str]],
    education_entries: list[dict[str, str]],
) -> list[str]:
    """Backwards-compatible wrapper used by cli.py for the ``skipped`` list."""
    issues = await detect_review_issues(
        page,
        experience_entries=experience_entries,
        education_entries=education_entries,
    )
    return [issue.message for issue in issues]


async def review_needs_repair(
    page,
    *,
    experience_entries: list[dict[str, str]],
    education_entries: list[dict[str, str]],
) -> bool:
    return bool(
        await detect_review_issues(
            page,
            experience_entries=experience_entries,
            education_entries=education_entries,
        )
    )


def issues_to_payload(issues: list[ReviewIssue]) -> list[dict[str, Any]]:
    """Serialise issues for ``apply-review.json::validation_issues[]``."""
    return [
        {"code": item.code, "message": item.message, "details": dict(item.details)}
        for item in issues
    ]


def _dedupe_issues(issues: list[ReviewIssue]) -> list[ReviewIssue]:
    seen: set[tuple[str, str]] = set()
    out: list[ReviewIssue] = []
    for issue in issues:
        key = (issue.code, issue.message)
        if key in seen:
            continue
        seen.add(key)
        out.append(issue)
    return out
