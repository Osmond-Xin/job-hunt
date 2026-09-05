"""Evaluating a job has to tick its pipeline row off.

Only `job-hunt pipeline run` ever did that, and the real paths are `evaluate`
and `evaluate-batch`, so rows stayed pending forever: 3,365 of them had piled
up, and triage kept re-ranking, re-screening and occasionally re-paying for
jobs that had already been evaluated weeks earlier.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from job_hunt.models.evaluation import EvaluationScores
from job_hunt.models.job import JobMeta
from job_hunt.nodes.tracker import NEEDS_RERUN_NOTE, merge_or_update_tracker, write_tracker_addition
from job_hunt.repositories.tracker_repo import TrackerEntry, TrackerRepository
from job_hunt.services import pipeline_inbox
from job_hunt.services.llm.call import LLM_FAILURE_MARKER
from job_hunt.services.triage import parse_pipeline


def _score_failure_error(node_name: str) -> str:
    return f"{node_name} {LLM_FAILURE_MARKER}; using fallback content: TimeoutError: 529"


def _fallback_scores() -> EvaluationScores:
    return EvaluationScores(
        weighted_total=0.0,
        recommendation="skip",
        recommendation_rationale="Scoring unavailable because the LLM provider failed.",
    )

_URL = "https://www.adzuna.ca/details/5833584853"
_ROW = f"- [ ] {_URL} | CSC Generation | AI Solutions Engineer | Canada | source: adzuna\n"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "pipeline.md").write_text(
        "# Pipeline\n\n## Pending\n\n## Processed\n\n" + _ROW, encoding="utf-8"
    )
    return tmp_path


def test_a_evaluated_row_stops_coming_back(repo: Path) -> None:
    state = {
        "url": _URL,
        "jd_meta": JobMeta(company="CSC Generation", title="AI Solutions Engineer"),
        "scores": None,
        "run_id": "abc123",
    }
    asyncio.run(write_tracker_addition(state, None))

    text = (repo / "data" / "pipeline.md").read_text(encoding="utf-8")
    assert _URL not in {row.url for row in parse_pipeline(text)}
    assert pipeline_inbox.parse(repo / "data" / "pipeline.md")[0].tracker_id == 1


def test_a_target_that_is_not_a_pipeline_row_is_not_an_error(repo: Path) -> None:
    """Hand-typed URLs and local JD files are normal inputs, not failures."""
    state = {
        "url": "https://example.invalid/typed-by-hand",
        "jd_meta": JobMeta(company="Acme", title="Engineer"),
        "scores": None,
        "run_id": "abc123",
    }
    result = asyncio.run(write_tracker_addition(state, None))

    assert result["errors"] == []
    assert result["tracker_entry"].company == "Acme"


def _existing_entry(number: int, company: str, role: str) -> TrackerEntry:
    return TrackerEntry(
        number=number,
        date="2026-08-28",
        company=company,
        role=role,
        score="3.5/5",
        status="Evaluated",
        pdf="❌",
        report="run-original",
        notes="",
    )


def test_pipeline_write_uses_the_unattended_gate_not_apply_s_0_70(repo: Path) -> None:
    """`nodes/tracker.py` acts unattended (jd_meta is LLM-extracted from a
    scraped JD, nobody confirms it) — it must not reuse `apply.py`'s attended
    0.70 `mutate` gate. 'Mariner' (as jd_meta.company) matches 'Mariner
    Partners Inc.' at 0.714 under that bare threshold — enough for `mutate`,
    but below `is_reliable_match`'s 0.75 floor. The unattended write must
    treat this as no match and create a new row rather than reusing #1."""
    TrackerRepository(repo / "data" / "applications.md").append_entry(
        _existing_entry(1, "Mariner Partners Inc.", "AI Engineer - Healthcare")
    )
    state = {
        "url": "https://example.invalid/mariner",
        "jd_meta": JobMeta(company="Mariner", title="AI Engineer - Healthcare"),
        "scores": None,
        "run_id": "abc123",
    }

    result = asyncio.run(write_tracker_addition(state, None))

    assert result["errors"] == []
    entries = TrackerRepository(repo / "data" / "applications.md").parse()
    assert [(e.number, e.company) for e in entries] == [
        (1, "Mariner Partners Inc."),
        (2, "Mariner"),
    ]
    assert result["tracker_entry"].number == 2


def test_write_tracker_addition_does_not_merge_two_distinct_microsoft_postings(repo: Path) -> None:
    """Regression for the Microsoft "Senior" vs "Staff" Backend Engineer,
    Platform postings: they must land as two separate rows, not collapse
    into one under the unattended gate."""
    TrackerRepository(repo / "data" / "applications.md").append_entry(
        _existing_entry(1, "Microsoft", "Senior Backend Engineer, Platform")
    )
    state = {
        "url": "https://example.invalid/ms-staff",
        "jd_meta": JobMeta(company="Microsoft", title="Staff Backend Engineer, Platform"),
        "scores": None,
        "run_id": "abc123",
    }

    result = asyncio.run(write_tracker_addition(state, None))

    entries = TrackerRepository(repo / "data" / "applications.md").parse()
    assert [(e.number, e.role) for e in entries] == [
        (1, "Senior Backend Engineer, Platform"),
        (2, "Staff Backend Engineer, Platform"),
    ]
    assert result["tracker_entry"].number == 2


def test_merge_or_update_tracker_does_not_overwrite_a_distinct_microsoft_posting(repo: Path) -> None:
    """The bug this guards: `merge_or_update_tracker` used to find the 'Staff'
    posting close enough to the existing 'Senior' row and overwrite that
    row's score/report with the new posting's — destroying the first
    application's record. It must now leave row #1 untouched and report no
    match."""
    TrackerRepository(repo / "data" / "applications.md").append_entry(
        _existing_entry(1, "Microsoft", "Senior Backend Engineer, Platform")
    )
    state = {
        "url": "https://example.invalid/ms-staff",
        "jd_meta": JobMeta(company="Microsoft", title="Staff Backend Engineer, Platform"),
        "scores": None,
        "report_path": "reports/ms-staff.md",
        "pdf_path": "output/ms-staff/cv.pdf",
    }

    result = asyncio.run(merge_or_update_tracker(state, None))

    assert result == {"errors": []}
    entries = TrackerRepository(repo / "data" / "applications.md").parse()
    assert len(entries) == 1
    assert entries[0].role == "Senior Backend Engineer, Platform"
    assert entries[0].score == "3.5/5"
    assert entries[0].report == "run-original"
    assert entries[0].pdf == "❌"


# ----- score_and_recommend failure: no row may assert a score that never happened -----


def test_a_scoring_failure_does_not_assert_a_score(repo: Path) -> None:
    """The bug this guards: a provider outage that returns 529 on every LLM
    call still let score_and_recommend's fallback ('0.0/5, skip') through to
    the tracker unmarked — row #867 read exactly like a completed, low-score
    evaluation. It was never assessed."""
    state = {
        "url": "https://example.invalid/outage",
        "jd_meta": JobMeta(company="Feathery", title="Senior Software Engineer - Applied AI"),
        "scores": _fallback_scores(),
        "recommendation": "skip",
        "run_id": "outage01",
        "errors": [_score_failure_error("score_and_recommend")],
    }

    result = asyncio.run(write_tracker_addition(state, None))

    entry = result["tracker_entry"]
    assert entry.score == "N/A"
    assert entry.notes == NEEDS_RERUN_NOTE
    assert entry.status == "Evaluated"
    assert entry.company == "Feathery"
    assert entry.role == "Senior Software Engineer - Applied AI"


def test_a_successful_run_writes_the_real_score_unchanged(repo: Path) -> None:
    state = {
        "url": "https://example.invalid/clean",
        "jd_meta": JobMeta(company="Acme", title="Engineer"),
        "scores": EvaluationScores(weighted_total=4.2, recommendation="apply"),
        "recommendation": "apply",
        "run_id": "clean001",
        "errors": [],
    }

    result = asyncio.run(write_tracker_addition(state, None))

    entry = result["tracker_entry"]
    assert entry.score == "4.2/5"
    assert entry.notes == "apply"


def test_a_partially_degraded_run_still_writes_the_real_score(repo: Path) -> None:
    """interview_prep falling back to its own fallback content is a different
    failure than score_and_recommend's — the number it produced is real and
    must not be discarded because some other node had a provider hiccup."""
    state = {
        "url": "https://example.invalid/partial",
        "jd_meta": JobMeta(company="Acme", title="Engineer"),
        "scores": EvaluationScores(weighted_total=3.7, recommendation="maybe"),
        "recommendation": "maybe",
        "run_id": "partial01",
        "errors": [_score_failure_error("interview_prep")],
    }

    result = asyncio.run(write_tracker_addition(state, None))

    entry = result["tracker_entry"]
    assert entry.score == "3.7/5"
    assert entry.notes == "maybe"


def test_merge_or_update_tracker_keeps_the_existing_score_when_scoring_failed(repo: Path) -> None:
    """A re-run's score_and_recommend failure must not clobber a real score a
    previous run already wrote for this row."""
    TrackerRepository(repo / "data" / "applications.md").append_entry(
        _existing_entry(1, "Acme", "Engineer")
    )
    state = {
        "url": "https://example.invalid/acme-retry",
        "jd_meta": JobMeta(company="Acme", title="Engineer"),
        "scores": _fallback_scores(),
        "recommendation": "skip",
        "errors": [_score_failure_error("score_and_recommend")],
    }

    result = asyncio.run(merge_or_update_tracker(state, None))

    assert result["tracker_entry"].score == "3.5/5"


def test_unrecoverable_identity_falls_back_to_the_url(repo: Path) -> None:
    """extract_jd already ran every identity-recovery trick it has
    (_strip_board_suffix, _identity_from_pipeline) before jd_meta got here —
    a blank/blank row is what tracker verify's (company, role) dedupe key
    treats as identical to every other blank/blank row. The URL keeps the
    row distinguishable from its siblings."""
    state = {
        "url": "https://example.invalid/mystery-posting",
        "jd_meta": JobMeta(company="", title=""),
        "scores": EvaluationScores(weighted_total=2.0, recommendation="skip"),
        "recommendation": "skip",
        "run_id": "mystery1",
        "errors": [],
    }

    result = asyncio.run(write_tracker_addition(state, None))

    entry = result["tracker_entry"]
    assert entry.company == "Unknown"
    assert entry.role == "https://example.invalid/mystery-posting"
