"""Tests for P2-9 role drift detection."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from job_hunt.services.role_drift import (
    RoleDriftFinding,
    detect_role_drift,
    extract_page_role,
)


def test_detect_no_drift_when_role_matches() -> None:
    finding = detect_role_drift("Senior AI Engineer", "Senior AI Engineer")
    assert finding.warning is None
    assert finding.similarity == 100


def test_detect_no_drift_for_close_match() -> None:
    """Token order swaps should not trip the gate."""
    finding = detect_role_drift("AI Engineer Senior", "Senior AI Engineer")
    assert finding.warning is None
    assert finding.similarity >= 70


def test_detect_drift_when_role_clearly_different() -> None:
    finding = detect_role_drift("AI Engineer", "Marketing Manager")
    assert finding.warning is not None
    assert "AI Engineer" in finding.warning
    assert "Marketing Manager" in finding.warning
    assert "evaluate" in finding.warning


def test_detect_skipped_for_empty_inputs() -> None:
    assert detect_role_drift("", "Senior Engineer").warning is None
    assert detect_role_drift("Senior Engineer", "").warning is None
    assert detect_role_drift(None, None).warning is None


def test_threshold_is_configurable() -> None:
    # at default threshold 70, this would warn; raise threshold to skip
    high_finding = detect_role_drift("AI Engineer", "Software Engineer", threshold=99)
    assert high_finding.warning is not None
    low_finding = detect_role_drift("AI Engineer", "Software Engineer", threshold=10)
    assert low_finding.warning is None


def test_extract_page_role_prefers_og_title() -> None:
    page = MagicMock()
    og_locator = MagicMock()
    og_locator.count = AsyncMock(return_value=1)
    og_locator.get_attribute = AsyncMock(return_value="Senior AI Engineer (Remote)")
    page.locator = MagicMock(return_value=MagicMock(first=og_locator))

    role = asyncio.run(extract_page_role(page))
    assert role == "Senior AI Engineer (Remote)"


def test_extract_page_role_falls_back_to_h1() -> None:
    page = MagicMock()
    og_locator = MagicMock()
    og_locator.count = AsyncMock(return_value=0)
    h1_locator = MagicMock()
    h1_locator.count = AsyncMock(return_value=1)
    h1_locator.inner_text = AsyncMock(return_value="Senior AI Engineer")
    page.locator = MagicMock(
        side_effect=lambda sel: MagicMock(first=og_locator if "og:title" in sel else h1_locator)
    )
    page.title = AsyncMock(return_value="should not be used")

    role = asyncio.run(extract_page_role(page))
    assert role == "Senior AI Engineer"


def test_extract_page_role_falls_back_to_title() -> None:
    page = MagicMock()
    empty = MagicMock()
    empty.count = AsyncMock(return_value=0)
    page.locator = MagicMock(return_value=MagicMock(first=empty))
    page.title = AsyncMock(return_value="Anthropic — Careers")

    role = asyncio.run(extract_page_role(page))
    assert role == "Anthropic — Careers"


def test_extract_page_role_returns_none_on_total_failure() -> None:
    page = MagicMock()
    empty = MagicMock()
    empty.count = AsyncMock(return_value=0)
    page.locator = MagicMock(return_value=MagicMock(first=empty))
    page.title = AsyncMock(return_value="")

    role = asyncio.run(extract_page_role(page))
    assert role is None
