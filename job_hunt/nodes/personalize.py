"""personalization_plan, interview_prep, draft_application_answers, and update_story_bank nodes."""

from __future__ import annotations

import datetime
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from job_hunt.models.state import JobHuntState
from job_hunt.nodes._llm import call_node_llm_or_fallback
from job_hunt.nodes._prompts import render

_STORY_BANK_PATH = Path("interview-prep/story-bank.md")
_DRAFT_ANSWERS_SCORE_THRESHOLD = 4.5


async def personalization_plan(state: JobHuntState, config: RunnableConfig) -> dict:
    prompt = render(
        "evaluate/personalization.md",
        cv=state.get("cv", ""),
        jd_meta=state.get("jd_meta"),
        jd_text=state.get("jd_text", ""),
        archetype=state.get("archetype"),
        evaluation_blocks=state.get("evaluation_blocks", {}),
    )
    result, errors = await call_node_llm_or_fallback(
        state,
        node_name="personalization_plan",
        prompt=prompt,
        prompt_version="evaluate/personalization.md:v2",
        fallback_content=(
            "Personalization plan unavailable because the LLM provider timed out or failed. "
            "Use the CV Match, Role Summary, and Level Strategy sections to tailor the resume manually."
        ),
        temperature=0.2,
        max_tokens=1200,
    )
    return {"evaluation_blocks": {"personalization": result.content}, "errors": errors}


async def interview_prep(state: JobHuntState, config: RunnableConfig) -> dict:
    jd_meta = state.get("jd_meta")
    prompt = render(
        "evaluate/interview_prep.md",
        cv=state.get("cv", ""),
        jd_meta=jd_meta,
        archetype=state.get("archetype"),
        evaluation_blocks=state.get("evaluation_blocks", {}),
    )
    result, errors = await call_node_llm_or_fallback(
        state,
        node_name="interview_prep",
        prompt=prompt,
        prompt_version="evaluate/interview_prep.md:v3",
        fallback_content=(
            "Interview preparation unavailable because the LLM provider timed out or failed. "
            "Prepare from the role requirements, CV evidence, hard gaps, and company notes in the report."
        ),
        temperature=0.2,
        max_tokens=1400,
    )
    return {"evaluation_blocks": {"interview_prep": result.content}, "errors": errors}


async def draft_application_answers(state: JobHuntState, config: RunnableConfig) -> dict:
    """Generate Section G draft answers only when score >= 4.5."""
    scores = state.get("scores")
    if not scores or scores.weighted_total < _DRAFT_ANSWERS_SCORE_THRESHOLD:
        return {"errors": []}

    prompt = render(
        "evaluate/draft_answers.md",
        cv=state.get("cv", ""),
        jd_meta=state.get("jd_meta"),
        jd_text=state.get("jd_text", ""),
        archetype=state.get("archetype"),
        evaluation_blocks=state.get("evaluation_blocks", {}),
    )
    result, errors = await call_node_llm_or_fallback(
        state,
        node_name="draft_application_answers",
        prompt=prompt,
        prompt_version="evaluate/draft_answers.md:v1",
        fallback_content=(
            "Draft answers unavailable because the LLM provider timed out or failed. "
            "Use the Personalization Plan and CV Match sections to write answers manually."
        ),
        temperature=0.3,
        max_tokens=1200,
    )
    return {"evaluation_blocks": {"draft_answers": result.content}, "errors": errors}


async def update_story_bank(state: JobHuntState, config: RunnableConfig) -> dict:
    """Append STAR+R stories from this evaluation to the persistent story bank."""
    interview_content = state.get("evaluation_blocks", {}).get("interview_prep", "")
    if not interview_content:
        return {"errors": []}

    jd_meta = state.get("jd_meta")
    company = jd_meta.company if jd_meta else "Unknown"
    role = jd_meta.title if jd_meta else "Unknown"
    date = datetime.date.today().isoformat()
    entry_header = f"## {date} | {company} — {role}"

    _STORY_BANK_PATH.parent.mkdir(parents=True, exist_ok=True)

    existing = _STORY_BANK_PATH.read_text(encoding="utf-8") if _STORY_BANK_PATH.exists() else ""

    if entry_header in existing:
        return {"errors": []}

    if not existing:
        existing = "# Interview Story Bank\n\nSTAR+R stories accumulated across evaluations. Reflection column signals seniority.\n"

    entry = f"\n\n---\n\n{entry_header}\n\n{interview_content.strip()}\n"
    _STORY_BANK_PATH.write_text(existing.rstrip() + entry, encoding="utf-8")
    return {"errors": []}
