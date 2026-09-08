from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from job_hunt.models.evaluation import EvaluationScores
from job_hunt.models.job import ArchetypeResult, CandidateProfile, JobMeta
from job_hunt.models.tracker import TrackerEntry


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
    # Write the cover letter and nothing else: skip tailor_cv and the CV PDF.
    # The cover-letter prompt reads the master `cv`, never `cv_tailored`, so
    # the letter has no dependency on that branch — and re-running it to get a
    # letter costs the slowest node in the graph and overwrites a résumé that
    # may already have passed its review.
    cover_letter_only: bool

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


def letter_only(state: "JobHuntState") -> bool:
    """Is this run producing a cover letter and nothing else?

    `prompts/evaluate/cover_letter.md` reads exactly two evaluation blocks —
    `cv_match` and `personalization` — plus the archetype, the master CV and the
    JD. `personalization` in turn reads `cv_match` and `comp_research`. Every
    other node in the chain is written for a full evaluation and is dead weight
    when only a letter is wanted.

    Measured on one Area52 letter, 2026-09-08: role_summary 14.9s,
    level_strategy 15.0s, interview_prep 41.8s, score_and_recommend 177.9s and
    draft_application_answers 98.2s — 348 seconds of a 679-second run, none of
    it reaching the letter.

    score_and_recommend has to go for a second reason. It rewrites the tracker
    row's score and report pointer, so asking for a letter re-scored an
    application already sent: Area52 moved from the 3.8 the operator submitted
    against to a 4.1 produced by a run that wrote no résumé at all.
    """
    return bool(state.get("cover_letter_only"))
