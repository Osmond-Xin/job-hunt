"""Tests for the ethical low-score gate in `job-hunt apply`."""

from __future__ import annotations

import pytest
import typer

from job_hunt.cli import _enforce_low_score_gate


def test_no_context_silent() -> None:
    """No tracker match → gate stays silent."""
    _enforce_low_score_gate(None, override=False)
    _enforce_low_score_gate({}, override=False)


def test_score_above_threshold_passes() -> None:
    _enforce_low_score_gate({"score": "4.5/5"}, override=False)
    _enforce_low_score_gate({"score": "4.0/5"}, override=False)


def test_score_below_threshold_aborts() -> None:
    with pytest.raises(typer.Exit) as info:
        _enforce_low_score_gate({"score": "3.5/5"}, override=False)
    assert info.value.exit_code == 1


def test_override_allows_low_score() -> None:
    # Should NOT raise when override is set, even at low score
    _enforce_low_score_gate({"score": "2.0/5"}, override=True)


def test_unparseable_score_silent() -> None:
    """N/A or DUP scores can't be gated — fall back to allowing the apply."""
    _enforce_low_score_gate({"score": "N/A"}, override=False)
    _enforce_low_score_gate({"score": "DUP"}, override=False)
    _enforce_low_score_gate({"score": ""}, override=False)
    _enforce_low_score_gate({"score": None}, override=False)
