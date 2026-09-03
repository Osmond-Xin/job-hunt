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


async def redteam_review(state: JobHuntState, config: RunnableConfig) -> dict:
    out_dir = run_output_dir(state)
    artifacts = [
        Path(p)
        for p in (state.get("pdf_path"), state.get("cover_letter_path"))
        if p and Path(p).exists()
    ]
    # Whether a templated CV/cover-letter (both out of templates/cv.html.j2)
    # is actually in this batch — drives the `origin` passed to run_review
    # below, since its "pipeline" exemptions (target banner, contact-line
    # separator) are specific to that template and must not silently cover
    # the freeform draft answers appended next.
    templated = bool(artifacts)

    # CLAUDE.md §1 names "application-form answers" alongside résumés and
    # cover letters as requiring red team before delivery. draft_application_
    # answers (nodes/personalize.py) only ever puts this text into
    # evaluation_blocks["draft_answers"] for report.md — it never touches
    # disk, so run_review, which reads artifacts off disk, never saw it. Give
    # it a file, under the run directory and filename the `apply-answers` CLI
    # command already uses for the same content (job_hunt/cli/apply.py), so
    # there is one convention rather than two.
    draft_answers = state.get("evaluation_blocks", {}).get("draft_answers")
    if draft_answers:
        out_dir.mkdir(parents=True, exist_ok=True)
        answers_path = out_dir / "apply-answers.md"
        answers_path.write_text(draft_answers, encoding="utf-8")
        artifacts.append(answers_path)

    if not artifacts:
        return {"errors": []}

    jd_meta = state.get("jd_meta")
    result = await asyncio.to_thread(
        run_review,
        artifacts=artifacts,
        jd_text=state.get("jd_text", ""),
        company=(jd_meta.company if jd_meta else "") or "",
        role=(jd_meta.title if jd_meta else "") or "",
        origin="pipeline" if templated else "hand-written",
    )

    warnings: list[str] = []
    if result.review:
        review_path = out_dir / "redteam.md"
        review_path.write_text(result.review, encoding="utf-8")
        warnings.append(
            f"red team verdict: {result.verdict} — findings in {review_path.name}"
        )
    else:
        warnings.append(f"artifacts are UNREVIEWED: {'; '.join(result.errors)}")

    return {
        "redteam_verdict": result.verdict,
        "artifact_warnings": warnings,
        "errors": result.errors if result.verdict == "UNREVIEWED" else [],
    }
