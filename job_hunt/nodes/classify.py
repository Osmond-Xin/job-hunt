"""classify_archetype node."""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from job_hunt.models.job import ArchetypeResult
from job_hunt.models.state import JobHuntState
from job_hunt.services.llm.call import call_node_llm_or_fallback
from job_hunt.services.prompts import render
from job_hunt.services.llm.content import extract_json_object


async def classify_archetype(state: JobHuntState, config: RunnableConfig) -> dict:
    jd_meta = state.get("jd_meta")
    prompt = render(
        "evaluate/classify_archetype.md",
        jd_meta=jd_meta,
        jd_text=state.get("jd_text", ""),
    )

    result, errors = await call_node_llm_or_fallback(
        state,
        node_name="classify_archetype",
        prompt=prompt,
        prompt_version="evaluate/classify_archetype.md:v1",
        fallback_content=(
            '{"archetype": "other", "confidence": 0.0, '
            '"rationale": "Archetype classification unavailable because the LLM provider failed.", '
            '"key_signals": []}'
        ),
        temperature=0.1,
        max_tokens=900,
    )

    archetype = _parse_archetype(result.content)
    return {"archetype": archetype, "errors": errors}


def _parse_archetype(content: str) -> ArchetypeResult:
    try:
        data = extract_json_object(content)
        if data:
            return ArchetypeResult.model_validate(data)
    except Exception:
        pass
    return ArchetypeResult(archetype="other", confidence=0.0, rationale=content[:200])
