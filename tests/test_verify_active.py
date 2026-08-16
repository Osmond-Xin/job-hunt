"""Tests for verify_active — the gate that stops a closed or unextracted posting
from being scored.

Both regressions here were found on 2026-08-15 against live boards: a closed
SuccessFactors posting and an expired LinkedIn job. Each served enough text to
clear the length check while carrying no job description at all, and the old
"substantial text means active" fallback let both through to a full evaluation.
"""

from __future__ import annotations

import asyncio

from job_hunt.nodes.extract import verify_active


def _run(jd_text: str) -> dict:
    return asyncio.run(verify_active({"jd_text": jd_text}, {}))


def test_real_posting_with_structure_is_active() -> None:
    result = _run(
        "About the role\n" + "We are hiring an engineer. " * 20
        + "\nResponsibilities: ship things.\nQualifications: experience."
    )
    assert result["jd_active"] is True
    assert result["errors"] == []


def test_closed_successfactors_wording_is_inactive() -> None:
    """The Nova Scotia government wording matched none of the original signals."""
    result = _run(
        "Careers Home About Us How To Apply Explore Opportunities " * 8
        + "This posting is now closed and no further applications can be accepted"
    )
    assert result["jd_active"] is False


def test_expired_linkedin_redirect_is_inactive() -> None:
    result = _run("Senior data engineer jobs " * 30 + "trk=expired_jd_redirect")
    assert result["jd_active"] is False


def test_site_chrome_without_job_structure_is_inactive() -> None:
    """Long page, no JD anywhere: the failure mode that produced a scored report
    for a posting that no longer existed."""
    result = _run(
        "Careers Home About Us What We Offer Explore Opportunities "
        "Join Our Talent Community Resume Writing Tips Newcomers " * 12
    )
    assert result["jd_active"] is False
    assert "No job-description structure" in result["errors"][0]


def test_too_short_is_inactive() -> None:
    result = _run("Data Analyst")
    assert result["jd_active"] is False
    assert "too short" in result["errors"][0]
