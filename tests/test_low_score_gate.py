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


def test_scores_the_scorer_now_recommends_pass() -> None:
    """3.0–4.0 is the "apply"/"maybe" band since 2026-08-16, not a blocked band.

    The gate is meant to track `prompts/shared.md`. While it sat at 4.0 and the
    prompt sat at 3.0, every newly-recommended role in between — Whitby at
    3.73, the whole reason the threshold moved — would have aborted at apply
    time and needed `--low-score-override` to get through.
    """
    _enforce_low_score_gate({"score": "3.73/5"}, override=False)
    _enforce_low_score_gate({"score": "3.5/5"}, override=False)
    _enforce_low_score_gate({"score": "3.0/5"}, override=False)


def test_score_below_threshold_aborts() -> None:
    with pytest.raises(typer.Exit) as info:
        _enforce_low_score_gate({"score": "2.9/5"}, override=False)
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
