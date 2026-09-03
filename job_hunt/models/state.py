from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from job_hunt.models.evaluation import EvaluationScores
from job_hunt.models.job import ArchetypeResult, CandidateProfile, JobMeta
from job_hunt.repositories.tracker_repo import TrackerEntry


def _merge_dicts(a: dict, b: dict) -> dict:
    return {**a, **b}


class JobHuntState(TypedDict, total=False):
    # --- run identity ---
    run_id: str
    thread_id: str

    # --- input ---
    input: str
    source_type: Literal["url", "jd_text", "local_file"]
    url: str | None

    # --- context loaded at startup ---
    jd_text: str
    jd_meta: JobMeta
    profile: CandidateProfile
    cv: str
    article_digest: str | None
    proof_points: str | None

    # --- top-level mode switch (docs/design-notes.md §N) ---
    # Set by the eligibility_gate node from profile.yml::mode.
    mode: Literal["student", "full"]
    # JD classification produced by the gate; pure-heuristic. Default
    # "unknown" passes through to scoring; mode mismatch routes to SKIP.
    jd_eligibility: Literal["student", "full", "unknown"]
    # Set by verify_active() to indicate whether the JD text represents an
    # active posting. Routes to mark_unavailable if False.
    jd_active: bool

    # --- analysis outputs (parallel fan-in via reducer) ---
    archetype: ArchetypeResult
    evaluation_blocks: Annotated[dict[str, str], _merge_dicts]

    # --- scoring ---
    scores: EvaluationScores
    recommendation: Literal["apply", "maybe", "skip"]

    # --- artifacts ---
    # JD-tailored rewrite of cv (produced by tailor_cv on the generate_pdf path);
    # empty/absent means render the master cv as-is.
    cv_tailored: str
    report_md: str
    report_path: str | None
    pdf_path: str | None
    cover_letter_path: str | None
    # BLOCK | REVISE | SEND | UNREVIEWED — set by redteam_review once the
    # artifacts exist. UNREVIEWED means the reviewer could not be reached,
    # which is not the same as passing.
    redteam_verdict: str

    # --- evaluation toggles (set from CLI / profile) ---
    generate_cover_letter: bool

    # --- tracker ---
    tracker_entry: TrackerEntry | None

    # --- HITL ---
    human_decision: dict[str, Any]

    # --- errors (parallel fan-in via list concat) ---
    errors: Annotated[list[str], operator.add]
    # Artifacts that were withheld or could not be verified by the quality
    # audit. Separate from `errors` because these must reach the operator as a
    # decision ("do not send this until you read it"), not as run noise.
    artifact_warnings: Annotated[list[str], operator.add]
