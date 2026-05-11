"""Tests for the extracted Workday Voluntary Disclosures filler."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from job_hunt.services.workday.voluntary_disclosures import (
    consent_enabled,
    fill_voluntary_disclosures,
)


def test_consent_enabled_reads_values_dict() -> None:
    assert consent_enabled({"workday_consent_terms_and_conditions": True}) is True
    assert consent_enabled({"workday_consent_terms_and_conditions": False}) is False


def test_consent_enabled_reads_sentinel_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "storage" / "private").mkdir(parents=True)
    (tmp_path / "storage" / "private" / "workday-consent-terms").write_text("ok")

    assert consent_enabled({}) is True


def test_consent_disabled_when_neither_signal_present(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert consent_enabled({}) is False


def test_fill_skips_with_clear_message_when_consent_missing(tmp_path, monkeypatch) -> None:
    """No consent recorded → no UI work attempted, skipped message guides the operator."""
    monkeypatch.chdir(tmp_path)
    page = MagicMock()

    filled, skipped = asyncio.run(fill_voluntary_disclosures(page, {}))

    assert filled == []
    assert len(skipped) == 1
    assert "consent" in skipped[0].lower()
    page.get_by_label.assert_not_called()


def test_fill_uses_label_path_first(tmp_path, monkeypatch) -> None:
    """When the accessibility-label path works, no JS fallback is needed."""
    monkeypatch.chdir(tmp_path)
    checkbox = MagicMock()
    checkbox.count = AsyncMock(return_value=1)
    checkbox.first.check = AsyncMock()
    page = MagicMock()
    page.get_by_label.return_value = checkbox
    page.evaluate = AsyncMock()  # would explode if called — must not be

    filled, skipped = asyncio.run(
        fill_voluntary_disclosures(
            page, {"workday_consent_terms_and_conditions": True}
        )
    )

    assert filled == ["Workday terms and conditions consent checkbox"]
    assert skipped == []
    page.evaluate.assert_not_called()


def test_fill_falls_back_to_js_when_label_path_misses(tmp_path, monkeypatch) -> None:
    """Label path returns 0 elements → JS path runs and clicks the checkbox."""
    monkeypatch.chdir(tmp_path)
    checkbox = MagicMock()
    checkbox.count = AsyncMock(return_value=0)
    page = MagicMock()
    page.get_by_label.return_value = checkbox
    page.evaluate = AsyncMock(return_value=True)

    filled, skipped = asyncio.run(
        fill_voluntary_disclosures(
            page, {"workday_consent_terms_and_conditions": True}
        )
    )

    assert filled == ["Workday terms and conditions consent checkbox"]
    assert skipped == []
    page.evaluate.assert_awaited_once()


def test_fill_reports_skip_when_both_paths_miss(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    checkbox = MagicMock()
    checkbox.count = AsyncMock(return_value=0)
    page = MagicMock()
    page.get_by_label.return_value = checkbox
    page.evaluate = AsyncMock(return_value=False)

    filled, skipped = asyncio.run(
        fill_voluntary_disclosures(
            page, {"workday_consent_terms_and_conditions": True}
        )
    )

    assert filled == []
    assert len(skipped) == 1
    assert "manual review" in skipped[0].lower()


def test_cli_wrapper_delegates_to_module(tmp_path, monkeypatch) -> None:
    """The thin cli.py shim must continue to work after the extraction."""
    monkeypatch.chdir(tmp_path)
    from job_hunt.cli import _fill_workday_voluntary_disclosures

    checkbox = MagicMock()
    checkbox.count = AsyncMock(return_value=1)
    checkbox.first.check = AsyncMock()
    page = MagicMock()
    page.get_by_label.return_value = checkbox

    filled, skipped = asyncio.run(
        _fill_workday_voluntary_disclosures(
            page, {"workday_consent_terms_and_conditions": True}
        )
    )

    assert filled == ["Workday terms and conditions consent checkbox"]
    assert skipped == []
