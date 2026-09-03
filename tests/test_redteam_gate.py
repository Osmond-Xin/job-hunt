"""The red-team gate: verdict parsing, fail-closed behaviour, and graph wiring.

The asymmetry under test is the same one `_quality.py` enforces: a reviewer that
could not be reached is UNREVIEWED, never a pass.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from job_hunt.models.job import JobMeta
from job_hunt.services import redteam as svc


def test_verdict_parsing_accepts_the_shapes_a_model_actually_emits():
    assert svc.parse_verdict("...\nVERDICT: SEND — looks fine") == "SEND"
    assert svc.parse_verdict("**VERDICT: BLOCK** — wrong email") == "BLOCK"
    assert svc.parse_verdict("## VERDICT: REVISE — thin targeting") == "REVISE"


def test_missing_verdict_is_unreviewed_not_a_pass():
    assert svc.parse_verdict("I ran out of tokens mid-sentence") == "UNREVIEWED"
    assert svc.parse_verdict("") == "UNREVIEWED"


def test_last_verdict_wins_when_the_model_restates_it():
    review = "VERDICT: BLOCK — draft\nmore thinking\nVERDICT: SEND — final"
    assert svc.parse_verdict(review) == "SEND"


def test_no_artifacts_is_a_no_op():
    result = svc.run_review(artifacts=[], jd_text="anything")
    assert result.verdict == "UNREVIEWED"
    assert not result.blocking


def test_missing_mmx_is_unreviewed_and_reported(monkeypatch, tmp_path):
    artifact = tmp_path / "cv.md"
    artifact.write_text("# Yi Xin", encoding="utf-8")
    monkeypatch.setattr(svc.shutil, "which", lambda name: None)
    result = svc.run_review(artifacts=[artifact], jd_text="jd")
    assert result.verdict == "UNREVIEWED"
    assert any("mmx" in err for err in result.errors)


def test_prompt_carries_ground_truth_jd_and_artifact(tmp_path):
    artifact = tmp_path / "cv.md"
    artifact.write_text("RESUME BODY", encoding="utf-8")
    facts = tmp_path / "facts.md"
    facts.write_text("GROUND TRUTH BODY", encoding="utf-8")
    prompt = svc.build_prompt(
        artifacts=[artifact],
        jd_text="JD BODY",
        company="Acme",
        role="FDE",
        facts_path=facts,
    )
    assert "GROUND TRUTH BODY" in prompt
    assert "JD BODY" in prompt
    assert "RESUME BODY" in prompt
    # The JD is model-visible untrusted text and must stay fenced.
    assert "<<<JOB_DESCRIPTION_BEGIN>>>" in prompt
    assert "untrusted input" in prompt


def test_missing_ground_truth_file_tells_the_reviewer_to_block(tmp_path):
    artifact = tmp_path / "cv.md"
    artifact.write_text("body", encoding="utf-8")
    prompt = svc.build_prompt(
        artifacts=[artifact],
        jd_text="",
        company="",
        role="",
        facts_path=tmp_path / "absent.md",
    )
    assert "ground-truth file missing" in prompt


def test_node_runs_between_artifacts_and_report():
    from job_hunt.graphs.evaluate_job import build_evaluate_job_graph

    graph = build_evaluate_job_graph().get_graph()
    edges = {(e.source, e.target) for e in graph.edges}
    assert ("generate_cover_letter", "redteam_review") in edges
    assert ("redteam_review", "write_report") in edges


def test_node_is_a_no_op_without_artifacts():
    from job_hunt.nodes.redteam import redteam_review

    out = asyncio.run(redteam_review({"pdf_path": None, "cover_letter_path": None}, None))
    assert out == {"errors": []}
    assert "redteam_verdict" not in out


# ----- draft_application_answers text reaching the review (CLAUDE.md §1) -----


def test_draft_answers_are_written_to_disk_and_reach_the_review(monkeypatch, tmp_path):
    """draft_application_answers (nodes/personalize.py) only ever puts its text
    into evaluation_blocks for report.md — it never touches disk, so run_review
    (which reads artifacts off disk) never saw it. With no PDF and no cover
    letter, this used to be the no-op path above; it must not be any more."""
    from job_hunt.nodes import artifact_paths as artifact_paths_module
    from job_hunt.nodes import redteam as redteam_module

    monkeypatch.setattr(artifact_paths_module, "_OUTPUT_DIR", tmp_path)

    calls = []

    def fake_run_review(*, artifacts, jd_text, company, role, origin="hand-written"):
        calls.append({"artifacts": artifacts, "origin": origin})
        return svc.RedTeamResult("SEND", "VERDICT: SEND — fine")

    monkeypatch.setattr(redteam_module, "run_review", fake_run_review)

    state = {
        "run_id": "abc123",
        "jd_meta": JobMeta(company="Acme", title="Engineer"),
        "jd_text": "JD body",
        "pdf_path": None,
        "cover_letter_path": None,
        "evaluation_blocks": {"draft_answers": "Q: Why us?\nA: Because."},
    }
    out = asyncio.run(redteam_module.redteam_review(state, None))

    answers_path = artifact_paths_module.run_output_dir(state) / "apply-answers.md"
    assert answers_path.exists()
    assert answers_path.read_text(encoding="utf-8") == "Q: Why us?\nA: Because."
    assert len(calls) == 1
    assert calls[0]["artifacts"] == [answers_path]
    # No templated CV/cover-letter in this batch, so the pipeline template's
    # exemptions (target banner, contact-line separator) must not apply.
    assert calls[0]["origin"] == "hand-written"
    assert out["redteam_verdict"] == "SEND"


def test_draft_answers_join_the_pipeline_review_when_a_pdf_exists(monkeypatch, tmp_path):
    """When a templated CV is also in this run, both go through one review call
    under the pipeline origin — one verdict, one redteam.md, same as before."""
    from job_hunt.nodes import artifact_paths as artifact_paths_module
    from job_hunt.nodes import redteam as redteam_module

    monkeypatch.setattr(artifact_paths_module, "_OUTPUT_DIR", tmp_path)

    pdf_path = tmp_path / "resume.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF fake")

    calls = []

    def fake_run_review(*, artifacts, jd_text, company, role, origin="hand-written"):
        calls.append({"artifacts": artifacts, "origin": origin})
        return svc.RedTeamResult("SEND", "VERDICT: SEND — fine")

    monkeypatch.setattr(redteam_module, "run_review", fake_run_review)

    state = {
        "run_id": "abc123",
        "jd_meta": JobMeta(company="Acme", title="Engineer"),
        "jd_text": "JD body",
        "pdf_path": str(pdf_path),
        "cover_letter_path": None,
        "evaluation_blocks": {"draft_answers": "answer text"},
    }
    asyncio.run(redteam_module.redteam_review(state, None))

    answers_path = artifact_paths_module.run_output_dir(state) / "apply-answers.md"
    assert set(calls[0]["artifacts"]) == {pdf_path, answers_path}
    assert calls[0]["origin"] == "pipeline"


def test_draft_answers_alone_still_go_unreviewed_when_mmx_is_unreachable(monkeypatch, tmp_path):
    """The other side of the same fix: an answers-only run must still fail
    closed, exactly like the existing pdf/cover-letter path."""
    from job_hunt.nodes import artifact_paths as artifact_paths_module
    from job_hunt.nodes import redteam as redteam_module

    monkeypatch.setattr(artifact_paths_module, "_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(svc.shutil, "which", lambda name: None)

    state = {
        "run_id": "abc123",
        "jd_meta": JobMeta(company="Acme", title="Engineer"),
        "jd_text": "JD body",
        "pdf_path": None,
        "cover_letter_path": None,
        "evaluation_blocks": {"draft_answers": "Q: Why us?\nA: Because."},
    }
    out = asyncio.run(redteam_module.redteam_review(state, None))

    assert out["redteam_verdict"] == "UNREVIEWED"
    assert not (artifact_paths_module.run_output_dir(state) / "redteam.md").exists()


# ----- write_report: must not point at a redteam.md that redteam_review never wrote -----


def _report_state(verdict: str) -> dict:
    return {
        "run_id": "abc123",
        "jd_meta": JobMeta(company="Acme", title="Engineer"),
        "evaluation_blocks": {},
        "redteam_verdict": verdict,
        "errors": [],
    }


def test_unreviewed_report_does_not_point_at_a_file_that_was_never_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLAUDE.md §1: UNREVIEWED is not a pass. redteam_review only writes
    redteam.md when a review actually came back — on the mmx-unreachable /
    timeout / non-zero-exit paths, no file exists, so the report must not send
    the operator to look for one."""
    from job_hunt.nodes.report import write_report

    monkeypatch.chdir(tmp_path)  # write_report writes a relative reports/ dir
    result = asyncio.run(write_report(_report_state("UNREVIEWED"), None))
    report = result["report_md"]

    assert "RED TEAM: UNREVIEWED" in report
    assert "not reviewed" in report.lower()
    assert "See `redteam.md`" not in report


def test_reviewed_block_report_points_at_the_file_that_was_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other side of the same fix: when redteam_review did write the file,
    the report must still send the operator to it."""
    from job_hunt.nodes.artifact_paths import run_output_dir
    from job_hunt.nodes.report import write_report

    monkeypatch.chdir(tmp_path)
    state = _report_state("BLOCK")
    out_dir = run_output_dir(state)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "redteam.md").write_text("VERDICT: BLOCK — wrong email", encoding="utf-8")

    result = asyncio.run(write_report(state, None))
    report = result["report_md"]

    assert "RED TEAM: BLOCK" in report
    assert "See `redteam.md`" in report


def test_missing_verdict_with_a_pdf_on_disk_reads_as_unreviewed_not_silence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A generated PDF with no redteam_verdict at all (redteam_review skipped
    or crashed before setting one) must not render identically to a clean
    SEND — that is the same silent gap as an absent verdict, just from a
    different cause than the two in CLAUDE.md §1."""
    from job_hunt.nodes.report import write_report

    monkeypatch.chdir(tmp_path)
    state = {
        "run_id": "abc123",
        "jd_meta": JobMeta(company="Acme", title="Engineer"),
        "evaluation_blocks": {},
        "pdf_path": "output/whatever/resume.pdf",
        "errors": [],
    }
    result = asyncio.run(write_report(state, None))
    report = result["report_md"]

    assert "RED TEAM: UNREVIEWED" in report
    assert "not reviewed" in report.lower()


def test_missing_verdict_with_no_artifact_stays_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other side: a run that produced nothing outward-facing has nothing
    to review, so the missing-verdict guard must not manufacture a banner."""
    from job_hunt.nodes.report import write_report

    monkeypatch.chdir(tmp_path)
    state = {
        "run_id": "abc123",
        "jd_meta": JobMeta(company="Acme", title="Engineer"),
        "evaluation_blocks": {},
        "errors": [],
    }
    result = asyncio.run(write_report(state, None))

    assert "RED TEAM" not in result["report_md"]
