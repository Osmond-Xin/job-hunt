"""Tests for the shared run-artifact naming (output/ dir and report filename)."""

from __future__ import annotations

import datetime

from job_hunt.models.job import JobMeta
from job_hunt.nodes.artifact_paths import run_output_dir, run_stem


def _state(company: str = "ARKEN", title: str = "AI Engineer") -> dict:
    return {
        "run_id": "13c0e1730e154c27b1a74ea5a0fa16b9",
        "jd_meta": JobMeta(company=company, title=title),
    }


def test_stem_leads_with_date_then_company_then_role() -> None:
    today = datetime.date.today().isoformat()
    assert run_stem(_state()) == f"{today}-arken-ai-engineer-13c0e173"


def test_output_dir_sits_under_output_and_matches_the_stem() -> None:
    state = _state()
    out_dir = run_output_dir(state)
    assert out_dir.parent.name == "output"
    assert out_dir.name == run_stem(state)


def test_punctuation_and_case_collapse_to_single_hyphens() -> None:
    stem = run_stem(_state(company="ARKEN (thearken.com)", title="AI/ML Engineer"))
    assert "-arken-thearken-com-ai-ml-engineer-" in stem
    assert "--" not in stem


def test_missing_jd_meta_still_produces_a_usable_name() -> None:
    stem = run_stem({"run_id": "abcdef0123456789"})
    assert stem.endswith("-unknown-unknown-abcdef01")


def test_same_job_twice_in_one_day_does_not_collide() -> None:
    a = _state()
    b = dict(_state(), run_id="ffffffffffffffffffffffffffffffff")
    assert run_stem(a) != run_stem(b)
