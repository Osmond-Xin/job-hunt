"""Asking for a cover letter must not rebuild the résumé.

`generate_cover_letter` renders from the master `cv` in state. It has never
read `cv_tailored`, so the CV branch is not an input to it — but the graph took
that branch anyway on every `--cover-letter` run.

That cost twice on 2026-09-07. `tailor_cv` is the slowest node in the graph
(usage ledger that day: a median 25s on a local CLI, over 100s on MiniMax-M3),
so a letter took twenty minutes to arrive. Worse, the regenerated résumé
overwrote the one in the run directory, and both times it came back degraded —
including once over an Inviso résumé that had already passed its red team with
the only SEND verdict of the batch. Recovering it meant moving files by hand.
"""

from __future__ import annotations

from job_hunt.graphs.evaluate_job import _route_pdf


class _Scores:
    generate_pdf = True


def test_cover_letter_only_skips_the_cv_branch() -> None:
    assert _route_pdf({"scores": _Scores(), "cover_letter_only": True}) == "skip_pdf"


def test_a_normal_run_still_tailors_the_cv() -> None:
    assert _route_pdf({"scores": _Scores()}) == "tailor_cv"
    assert _route_pdf({"scores": _Scores(), "cover_letter_only": False}) == "tailor_cv"


def test_the_score_gate_still_wins_on_its_own() -> None:
    """The scorer's `generate_pdf=False` is unrelated to this flag and still holds."""

    class _NoPdf:
        generate_pdf = False

    assert _route_pdf({"scores": _NoPdf()}) == "skip_pdf"
    assert _route_pdf({"scores": None}) == "skip_pdf"


def test_cover_letter_only_forces_the_letter_on() -> None:
    """`--cover-letter-only --no-cover-letter` would run the graph to produce nothing."""
    from typer.testing import CliRunner

    from job_hunt.cli import app

    captured: dict = {}

    import job_hunt.cli.evaluation as evaluation

    class _Graph:
        async def ainvoke(self, state, config=None):
            captured.update(state)
            return {"errors": [], "scores": None}

    runner = CliRunner()
    original = evaluation.build_evaluate_job_graph
    evaluation.build_evaluate_job_graph = lambda: _Graph()
    try:
        import os

        os.environ["JOB_HUNT_SKIP_CV_SYNC_CHECK"] = "1"
        runner.invoke(
            app,
            ["evaluate", "some job description text", "--cover-letter-only", "--no-cover-letter"],
        )
    finally:
        evaluation.build_evaluate_job_graph = original

    assert captured.get("cover_letter_only") is True
    assert captured.get("generate_cover_letter") is True


def test_letter_only_short_circuits_the_nodes_a_letter_never_reads(monkeypatch) -> None:
    """The five nodes skipped here are 348s of a 679s run, measured 2026-09-08."""
    import asyncio

    from job_hunt.nodes.evaluate import level_strategy, role_summary, score_and_recommend
    from job_hunt.nodes.personalize import draft_application_answers, interview_prep

    state = {"cover_letter_only": True}
    for node in (role_summary, level_strategy, score_and_recommend,
                 interview_prep, draft_application_answers):
        result = asyncio.run(node(dict(state), None))
        assert result == {"errors": []}, f"{node.__name__} did work it did not need to"


def test_a_letter_run_keeps_the_rows_existing_score_and_report() -> None:
    """It scores nothing, so its report describes no evaluation.

    Row #926 was an application already sent. Asking for an Area52 cover letter
    repointed it at a run that wrote no résumé and no score, and rewrote the 3.8
    the operator submitted against to a 4.1 he never saw.
    """
    from job_hunt.models.state import letter_only

    assert letter_only({"cover_letter_only": True}) is True
    assert letter_only({}) is False


def test_letter_only_skips_company_comp_research() -> None:
    """77s of a 332s run for an input the letter does not read.

    Half of that node is compensation, which never reaches a cover letter, and
    the company half duplicates `article_digest` — which the cover-letter prompt
    already reads directly. `personalization.md` renders the missing block as
    empty.
    """
    import asyncio

    from job_hunt.nodes.research import company_comp_research

    result = asyncio.run(company_comp_research({"cover_letter_only": True}, None))
    # The block must still be present. `personalization.md` interpolates it and
    # the Jinja environment is strict: returning nothing raised UndefinedError
    # inside personalization_plan and killed the run 78 seconds in.
    assert "comp_research" in result["evaluation_blocks"]
    assert result["errors"] == []
