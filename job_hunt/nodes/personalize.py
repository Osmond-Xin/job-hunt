"""personalization_plan, interview_prep, draft_application_answers, and update_story_bank nodes."""

from __future__ import annotations

import asyncio
import datetime
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from job_hunt.models.state import JobHuntState
from job_hunt.nodes._llm import call_node_llm_or_fallback
from job_hunt.nodes._prompts import render

_STORY_BANK_PATH = Path("interview-prep/story-bank.md")
# Aligned with the "apply" band in prompts/evaluate/score_and_recommend.md.
# It sat at 4.5 while that band was 4.0; when the band dropped to 3.5 on
# 2026-08-16 the gate stopped firing at all — the whole day's best score was
# 4.35 — so the node was spending nothing and producing nothing. Every role
# the scorer says to apply to now gets its form answers drafted, which is the
# point at which they are actually used.
_DRAFT_ANSWERS_SCORE_THRESHOLD = 3.5

# The story bank is read-modify-written whole. The critical section below is
# synchronous end to end, so today asyncio cannot interleave two `evaluate-batch`
# jobs inside it and the lock is not load-bearing. It is kept because that
# safety is an accident of there being no `await` between the read and the
# write: add one (async file I/O, a lookup, a retry) and concurrent jobs would
# start overwriting each other's stories with no other signal. Locking the
# invariant is cheaper than re-deriving it later.
_STORY_BANK_LOCK = asyncio.Lock()


async def personalization_plan(state: JobHuntState, config: RunnableConfig) -> dict:
    prompt = render(
        "evaluate/personalization.md",
        cv=state.get("cv", ""),
        article_digest=state.get("article_digest") or "",
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
    """Generate Section G draft answers only for roles the scorer says to apply to."""
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
    async with _STORY_BANK_LOCK:
        return _append_story(state, interview_content)


def _append_story(state: JobHuntState, interview_content: str) -> dict:
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
