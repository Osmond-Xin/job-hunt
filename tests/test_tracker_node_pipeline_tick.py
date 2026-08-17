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

from job_hunt.models.job import JobMeta
from job_hunt.nodes.tracker import write_tracker_addition
from job_hunt.services import pipeline_inbox
from job_hunt.services.triage import parse_pipeline

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
