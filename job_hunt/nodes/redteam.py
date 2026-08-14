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
    artifacts = [
        Path(p)
        for p in (state.get("pdf_path"), state.get("cover_letter_path"))
        if p and Path(p).exists()
    ]
    if not artifacts:
        return {"errors": []}

    jd_meta = state.get("jd_meta")
    result = await asyncio.to_thread(
        run_review,
        artifacts=artifacts,
        jd_text=state.get("jd_text", ""),
        company=(jd_meta.company if jd_meta else "") or "",
        role=(jd_meta.title if jd_meta else "") or "",
    )

    warnings: list[str] = []
    if result.review:
        review_path = run_output_dir(state) / "redteam.md"
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
