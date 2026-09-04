"""redteam_review node — nothing leaves this pipeline unreviewed.

Runs after the artifacts exist and before the report is written, so the verdict
and the findings land in the report the operator actually reads. A BLOCK does not
delete the artifact: the operator decides whether the finding is real (the
reviewer reads extracted text and does produce false positives). It does make the
verdict impossible to miss.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from job_hunt.models.state import JobHuntState
from job_hunt.nodes.artifact_paths import run_output_dir
from job_hunt.services.redteam import run_review


_VERDICT_SEVERITY = {"BLOCK": 3, "UNREVIEWED": 2, "REVISE": 1, "SEND": 0}


def _merge_verdicts(verdicts: list[str]) -> str:
    """Worst-of across independent review batches. UNREVIEWED outranks REVISE:
    a batch the reviewer never saw is a bigger unknown than one with specific,
    addressable findings from another batch, and CLAUDE.md §1's "not a pass"
    rule means it must not be hidden behind a milder verdict from elsewhere."""
    return max(verdicts, key=lambda v: _VERDICT_SEVERITY.get(v, 0))


async def redteam_review(state: JobHuntState, config: RunnableConfig) -> dict:
    out_dir = run_output_dir(state)
    templated_artifacts = [
        Path(p)
        for p in (state.get("pdf_path"), state.get("cover_letter_path"))
        if p and Path(p).exists()
    ]

    # CLAUDE.md §1 names "application-form answers" alongside résumés and
    # cover letters as requiring red team before delivery. draft_application_
    # answers (nodes/personalize.py) only ever puts this text into
    # evaluation_blocks["draft_answers"] for report.md — it never touches
    # disk, so run_review, which reads artifacts off disk, never saw it. Give
    # it a file, under the run directory and filename the `apply-answers` CLI
    # command already uses for the same content (job_hunt/cli/apply.py), so
    # there is one convention rather than two.
    draft_answers = state.get("evaluation_blocks", {}).get("draft_answers")
    answer_artifacts: list[Path] = []
    if draft_answers:
        out_dir.mkdir(parents=True, exist_ok=True)
        answers_path = out_dir / "apply-answers.md"
        answers_path.write_text(draft_answers, encoding="utf-8")
        answer_artifacts.append(answers_path)

    # One run_review call per origin, not one call over the union of both
    # lists. run_review's `origin` names a single provenance for everything
    # it is handed: "pipeline" pulls in the ORIGINS blurb (services/redteam.py)
    # that tells the reviewer the target banner and the `|` contact-line
    # separator are house style of templates/cv.html.j2 and not defects.
    # Folding the freeform draft answers into that same call — which is what
    # happened here before, in the common case where a personalized
    # evaluation produces both a CV and draft answers — hands the reviewer
    # that exemption for text the template never touched. Splitting keeps
    # each origin scoped to the artifacts it was written for, at the cost of
    # a second mmx invocation (up to another 600s) on runs that produce both
    # kinds; a run with only one kind still makes a single call, same as
    # before.
    jd_meta = state.get("jd_meta")
    jd_text = state.get("jd_text", "")
    company = (jd_meta.company if jd_meta else "") or ""
    role = (jd_meta.title if jd_meta else "") or ""

    batches: list[tuple[str, list[Path]]] = []
    if templated_artifacts:
        batches.append(("pipeline", templated_artifacts))
    if answer_artifacts:
        batches.append(("hand-written", answer_artifacts))

    if not batches:
        return {"errors": []}

    results = [
        await asyncio.to_thread(
            run_review,
            artifacts=artifacts,
            jd_text=jd_text,
            company=company,
            role=role,
            origin=origin,
        )
        for origin, artifacts in batches
    ]

    review_path = out_dir / "redteam.md"
    reviews = [result.review for result in results if result.review]
    if reviews:
        review_path.write_text("\n\n---\n\n".join(reviews), encoding="utf-8")

    # A second batch (draft answers alongside a templated CV/cover letter) is
    # the only case where a per-batch label is needed to tell which origin a
    # warning is about; a single-batch run keeps the plain wording it always
    # had.
    multi = len(batches) > 1
    warnings: list[str] = []
    for (origin, _artifacts), result in zip(batches, results):
        label = f" ({origin})" if multi else ""
        if result.review:
            warnings.append(
                f"red team verdict{label}: {result.verdict} — findings in {review_path.name}"
            )
        else:
            warnings.append(f"artifacts are UNREVIEWED{label}: {'; '.join(result.errors)}")

    verdict = _merge_verdicts([result.verdict for result in results])
    return {
        "redteam_verdict": verdict,
        "artifact_warnings": warnings,
        "errors": (
            [err for result in results for err in result.errors]
            if verdict == "UNREVIEWED"
            else []
        ),
    }
