"""cv_match, role_summary, level_strategy, score_and_recommend nodes."""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from job_hunt.models.evaluation import DimensionScore, EvaluationScores, PdfContent
from job_hunt.models.state import JobHuntState
from job_hunt.nodes._llm import call_node_llm_or_fallback
from job_hunt.nodes._prompts import render
from job_hunt.services.immigration import immigration_context
from job_hunt.services.llm.content import extract_json_object


async def cv_match(state: JobHuntState, config: RunnableConfig) -> dict:
    prompt = render(
        "evaluate/cv_match.md",
        cv=state.get("cv", ""),
        article_digest=state.get("article_digest") or "",
        jd_meta=state.get("jd_meta"),
        jd_text=state.get("jd_text", ""),
        archetype=state.get("archetype"),
    )
    result, errors = await call_node_llm_or_fallback(
        state,
        node_name="cv_match",
        prompt=prompt,
        prompt_version="evaluate/cv_match.md:v2",
        fallback_content=(
            "CV match unavailable because the LLM provider timed out or failed. "
            "Manually compare the JD requirements against profile/cv.md before applying."
        ),
        temperature=0.1,
        max_tokens=1200,
    )
    return {"evaluation_blocks": {"cv_match": result.content}, "errors": errors}


async def role_summary(state: JobHuntState, config: RunnableConfig) -> dict:
    prompt = render(
        "evaluate/role_summary.md",
        jd_meta=state.get("jd_meta"),
        jd_text=state.get("jd_text", ""),
    )
    result, errors = await call_node_llm_or_fallback(
        state,
        node_name="role_summary",
        prompt=prompt,
        prompt_version="evaluate/role_summary.md:v1",
        fallback_content=(
            "Role summary unavailable because the LLM provider timed out or failed. "
            "Use the extracted JD text as the source of truth."
        ),
        temperature=0.1,
        max_tokens=1000,
    )
    return {"evaluation_blocks": {"role_summary": result.content}, "errors": errors}


async def level_strategy(state: JobHuntState, config: RunnableConfig) -> dict:
    prompt = render(
        "evaluate/level_strategy.md",
        jd_meta=state.get("jd_meta"),
        jd_text=state.get("jd_text", ""),
        profile=state.get("profile"),
    )
    result, errors = await call_node_llm_or_fallback(
        state,
        node_name="level_strategy",
        prompt=prompt,
        prompt_version="evaluate/level_strategy.md:v1",
        fallback_content=(
            "Level strategy unavailable because the LLM provider timed out or failed. "
            "Check seniority, location, compensation, and work authorization manually."
        ),
        temperature=0.1,
        max_tokens=1000,
    )
    return {"evaluation_blocks": {"level_strategy": result.content}, "errors": errors}


async def score_and_recommend(state: JobHuntState, config: RunnableConfig) -> dict:
    blocks = state.get("evaluation_blocks", {})
    mode = state.get("mode", "full")
    jd_meta = state.get("jd_meta")
    prompt = render(
        "evaluate/score_and_recommend.md",
        evaluation_blocks=blocks,
        mode=mode,
        cv=state.get("cv", ""),
        article_digest=state.get("article_digest") or "",
        jd_text=state.get("jd_text", ""),
        immigration_context=immigration_context(jd_meta.location if jd_meta else ""),
    )
    result, errors = await call_node_llm_or_fallback(
        state,
        node_name="score_and_recommend",
        prompt=prompt,
        prompt_version="evaluate/score_and_recommend.md:v3",
        fallback_content=(
            '{"weighted_total": 0.0, "recommendation": "skip", '
            '"recommendation_rationale": "Scoring unavailable because the LLM provider failed.", '
            '"generate_pdf": false, "dimensions": [], "strengths": [], "gaps": [], "pdf_content": {}}'
        ),
        temperature=0.1,
        max_tokens=3200,
    )

    scores = _parse_scores(result.content)
    return {
        "scores": scores,
        "recommendation": scores.recommendation,
        "errors": errors,
    }


def _coerce_bool(value: object) -> bool:
    """Coerce an LLM-returned flag to bool. ``bool("false")`` is True, so a
    model that emits the string "false" would wrongly generate a PDF —
    handle string forms explicitly."""
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return bool(value)


def _parse_scores(content: str) -> EvaluationScores:
    try:
        data = extract_json_object(content)
        if data:
            dims = []
            for raw_dim in data.get("dimensions", []):
                raw_dim["score"] = float(raw_dim.get("score") or 0.0)
                raw_dim["weight"] = float(raw_dim.get("weight") or 0.0)
                raw_dim["rationale"] = raw_dim.get("rationale") or ""
                dims.append(DimensionScore(**raw_dim))
            pdf_raw = data.get("pdf_content", {})
            return EvaluationScores(
                dimensions=dims,
                weighted_total=float(data.get("weighted_total", 0.0)),
                recommendation=data.get("recommendation", "skip"),
                recommendation_rationale=data.get("recommendation_rationale", ""),
                generate_pdf=_coerce_bool(data.get("generate_pdf", False)),
                strengths=data.get("strengths", []),
                gaps=data.get("gaps", []),
                pdf_content=PdfContent(**pdf_raw),
            )
    except Exception:
        pass
    return EvaluationScores(recommendation="skip", recommendation_rationale=content[:200])
