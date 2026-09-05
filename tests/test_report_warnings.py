"""Withheld/unverified artifacts must survive past the console."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from job_hunt.models.evaluation import EvaluationScores
from job_hunt.models.job import JobMeta
from job_hunt.nodes.report import write_report
from job_hunt.nodes.tracker import NEEDS_RERUN_NOTE
from job_hunt.services.llm.call import LLM_FAILURE_MARKER


def test_artifact_warnings_are_written_into_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Console output scrolls away during a 50-job batch; the report does not."""
    # write_report writes to a relative `reports/`, so without this the test
    # drops a fake "Acme — Engineer" report into the operator's real one.
    monkeypatch.chdir(tmp_path)
    state = {
        "run_id": "abc123",
        "jd_meta": JobMeta(company="Acme", title="Engineer"),
        "evaluation_blocks": {},
        "artifact_warnings": ["cover letter withheld (audit failed): invented a metric"],
        "errors": [],
    }
    result = asyncio.run(write_report(state, None))
    report = result["report_md"]
    assert "Artifacts needing review" in report
    assert "cover letter withheld" in report
    assert "Do not send these without reading them first." in report


def test_clean_run_adds_no_warning_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    state = {
        "run_id": "abc123",
        "jd_meta": JobMeta(company="Acme", title="Engineer"),
        "evaluation_blocks": {},
        "errors": [],
    }
    result = asyncio.run(write_report(state, None))
    assert "Artifacts needing review" not in result["report_md"]


def test_a_scoring_failure_does_not_report_a_fake_score(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """job_hunt/nodes/tracker.py's write_tracker_addition writes 'N/A' and a
    NEEDS_RERUN_NOTE for the same run — this report must not contradict that
    by showing the fallback score_and_recommend object's '0.0/5, skip' as if
    scoring had actually happened."""
    monkeypatch.chdir(tmp_path)
    state = {
        "run_id": "abc123",
        "jd_meta": JobMeta(company="Feathery", title="Senior Software Engineer"),
        "evaluation_blocks": {},
        "scores": EvaluationScores(
            weighted_total=0.0,
            recommendation="skip",
            recommendation_rationale="Scoring unavailable because the LLM provider failed.",
        ),
        "recommendation": "skip",
        "errors": [
            f"score_and_recommend {LLM_FAILURE_MARKER}; using fallback content: "
            "TimeoutError: 529"
        ],
    }
    result = asyncio.run(write_report(state, None))
    report = result["report_md"]

    assert "**Score**: N/A" in report
    assert "**Recommendation**: NEEDS RE-RUN" in report
    assert "0.0/5" not in report
    assert "SKIP" not in report
    assert NEEDS_RERUN_NOTE in report


def test_a_partially_degraded_run_still_reports_the_real_score(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """interview_prep falling back is a different node's failure — the score
    itself is real and the header must keep reporting it."""
    monkeypatch.chdir(tmp_path)
    state = {
        "run_id": "abc123",
        "jd_meta": JobMeta(company="Acme", title="Engineer"),
        "evaluation_blocks": {},
        "scores": EvaluationScores(weighted_total=3.7, recommendation="maybe"),
        "recommendation": "maybe",
        "errors": [
            f"interview_prep {LLM_FAILURE_MARKER}; using fallback content: "
            "TimeoutError: 529"
        ],
    }
    result = asyncio.run(write_report(state, None))
    report = result["report_md"]

    assert "**Score**: 3.7/5.0" in report
    assert "**Recommendation**: MAYBE" in report
    assert "Weighted total**: 3.70/5.0" in report
