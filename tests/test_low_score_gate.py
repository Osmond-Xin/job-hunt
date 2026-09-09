"""Tests for the ethical low-score gate in `job-hunt apply`.

The gate is two pieces since the Phase 1 split (docs/apply-seam-plan.md): a pure
decision in `services/apply/reporting.low_score_verdict`, and the wording plus
the exit in `cli/apply._report_low_score_verdict`. Both are tested here, because
what matters to the operator is the pair -- a verdict nobody acts on is as bad
as no verdict.
"""

from __future__ import annotations

import pytest
import typer

from job_hunt.cli import low_score_verdict
from job_hunt.cli.apply import _report_low_score_verdict


def _allows(context, *, override: bool = False) -> bool:
    """Did the gate let this through, end to end -- decision and action?"""
    verdict = low_score_verdict(context, override=override)
    try:
        _report_low_score_verdict(verdict)
    except typer.Exit as exit_:
        assert exit_.exit_code == 1
        assert verdict.allowed is False
        return False
    return verdict.allowed


def test_no_context_silent() -> None:
    """No tracker match → gate stays silent."""
    assert _allows(None) is True
    assert _allows({}) is True


def test_score_above_threshold_passes() -> None:
    assert _allows({"score": "4.5/5"}) is True
    assert _allows({"score": "4.0/5"}) is True


def test_scores_the_scorer_now_recommends_pass() -> None:
    """3.0–4.0 is the "apply"/"maybe" band since 2026-08-16, not a blocked band.

    The gate is meant to track `prompts/shared.md`. While it sat at 4.0 and the
    prompt sat at 3.0, every newly-recommended role in between — Whitby at
    3.73, the whole reason the threshold moved — would have aborted at apply
    time and needed `--low-score-override` to get through.
    """
    assert _allows({"score": "3.73/5"}) is True
    assert _allows({"score": "3.5/5"}) is True
    assert _allows({"score": "3.0/5"}) is True


def test_score_below_threshold_aborts() -> None:
    assert _allows({"score": "2.9/5"}) is False


def test_override_allows_low_score() -> None:
    assert _allows({"score": "2.0/5"}, override=True) is True


def test_unparseable_score_silent() -> None:
    """N/A or DUP scores can't be gated — fall back to allowing the apply."""
    assert _allows({"score": "N/A"}) is True
    assert _allows({"score": "DUP"}) is True
    assert _allows({"score": ""}) is True
    assert _allows({"score": None}) is True


def test_the_verdict_carries_the_score_so_the_message_can_name_it(capsys) -> None:
    """A refusal that does not say which score it refused is not actionable."""
    verdict = low_score_verdict({"score": "2.4/5"}, override=False)
    assert verdict.score == 2.4
    with pytest.raises(typer.Exit):
        _report_low_score_verdict(verdict)
    out = capsys.readouterr().out
    assert "2.4" in out
    assert "low-score-override" in out


def test_an_override_is_announced_not_silent(capsys) -> None:
    """Overriding is a decision worth a line in the log: it means the operator
    applied to a role the scorer said not to."""
    verdict = low_score_verdict({"score": "2.0/5"}, override=True)
    _report_low_score_verdict(verdict)
    out = capsys.readouterr().out
    assert "2.0" in out
    assert "low-score-override" in out
