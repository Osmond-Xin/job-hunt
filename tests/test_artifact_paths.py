"""Tests for the shared run-artifact naming (output/ dir and report filename)."""

from __future__ import annotations

import datetime
from types import SimpleNamespace

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
    assert stem.endswith("-unknown-employer-unknown-role-abcdef01")


def test_an_employer_the_page_did_not_name_says_so_in_the_path() -> None:
    """The employer segment is what the operator navigates by.

    Aggregator pages name no company, and an empty slug collapsed the segment
    to nothing: three 2026-08-17 runs landed in
    "output/2026-08-17--ai-solutions-engineer-adzuna-c-…", which names the job
    board and not the employer.
    """
    stem = run_stem({"run_id": "abcdef0123456789", "jd_meta": JobMeta(title="AI Solutions Engineer")})

    assert "--" not in stem
    assert "-unknown-employer-ai-solutions-engineer-" in stem


def test_same_job_twice_in_one_day_does_not_collide() -> None:
    a = _state()
    b = dict(_state(), run_id="ffffffffffffffffffffffffffffffff")
    assert run_stem(a) != run_stem(b)


def test_artifacts_are_named_for_the_employer_not_just_cv_pdf():
    """A file-upload dialog shows the filename, not the directory.

    Every pipeline run wrote a bare `cv.pdf`, so a folder of applications was 69
    identical entries and the operator could not tell which employer he was
    attaching (reported 2026-09-01).
    """
    from job_hunt.nodes.artifact_paths import artifact_filename

    state = {
        "profile": SimpleNamespace(full_name="Yi Xin"),
        "jd_meta": SimpleNamespace(company="CGS Immersive", title="Senior Full-Stack AI Product Engineer"),
    }
    assert artifact_filename(state, kind="Resume") == "Yi_Xin_Resume_CGS_Immersive.pdf"
    assert artifact_filename(state, kind="Cover_Letter") == "Yi_Xin_Cover_Letter_CGS_Immersive.pdf"
    assert artifact_filename(state, kind="Resume", suffix="") == "Yi_Xin_Resume_CGS_Immersive"


def test_a_missing_employer_says_so_rather_than_producing_a_bare_name():
    from job_hunt.nodes.artifact_paths import artifact_filename

    state = {"profile": SimpleNamespace(full_name="Yi Xin"), "jd_meta": SimpleNamespace(company="", title="")}
    assert artifact_filename(state, kind="Resume") == "Yi_Xin_Resume_Unknown_Employer.pdf"
    # No profile and no metadata at all still yields a usable, non-empty name.
    assert artifact_filename({}, kind="Resume") == "Candidate_Resume_Unknown_Employer.pdf"


def test_punctuation_in_an_employer_name_cannot_break_the_filename():
    from job_hunt.nodes.artifact_paths import artifact_filename

    state = {
        "profile": SimpleNamespace(full_name="Yi Xin"),
        "jd_meta": SimpleNamespace(company="Connor, Clark & Lunn Financial Group / Inc.", title="x"),
    }
    name = artifact_filename(state, kind="Resume")
    assert name == "Yi_Xin_Resume_Connor_Clark_Lunn_Financial_Group_Inc.pdf"
    for bad in "/\\:*?\"<>|,&.":
        assert bad not in name.replace(".pdf", "")
