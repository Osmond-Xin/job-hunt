"""Shared LLM helpers for graph nodes.

Graph nodes should use this module instead of calling providers directly so
LangSmith tracing and the local token ledger stay consistent across stages.
"""

from __future__ import annotations

from typing import Any, Literal

from job_hunt.config.models import Settings, load_settings
from job_hunt.models.state import JobHuntState
from job_hunt.services.llm.base import ChatMessage, ChatResult
from job_hunt.services.llm.factory import build_cheap_provider, build_premium_provider
from job_hunt.services.llm.traced import traced_chat

_GRAPH_NAME = "evaluate_job_graph"


def node_metadata(node_name: str, state: JobHuntState, prompt_version: str) -> dict[str, Any]:
    jd_meta = state.get("jd_meta")
    return {
        "graph_name": _GRAPH_NAME,
        "node_name": node_name,
        "job_company": jd_meta.company if jd_meta else "",
        "job_role": jd_meta.title if jd_meta else "",
        "prompt_version": prompt_version,
    }


async def call_node_llm(
    state: JobHuntState,
    *,
    node_name: str,
    prompt: str,
    prompt_version: str,
    tier: Literal["cheap", "premium"] = "cheap",
    temperature: float | None = None,
    max_tokens: int | None = None,
    settings: Settings | None = None,
) -> ChatResult:
    settings = settings or load_settings()
    if tier == "premium":
        provider = build_premium_provider(settings)
        tier_config = settings.llm.premium
    else:
        provider = build_cheap_provider(settings)
        tier_config = settings.llm.cheap

    return await traced_chat(
        provider,
        settings=settings,
        messages=[ChatMessage(role="user", content=prompt)],
        model=tier_config.model,
        node_name=node_name,
        graph_name=_GRAPH_NAME,
        model_tier=tier,
        temperature=tier_config.temperature if temperature is None else temperature,
        max_tokens=max_tokens,
        metadata=node_metadata(node_name, state, prompt_version),
    )


async def call_node_llm_or_fallback(
    state: JobHuntState,
    *,
    node_name: str,
    prompt: str,
    prompt_version: str,
    fallback_content: str,
    tier: Literal["cheap", "premium"] = "cheap",
    temperature: float | None = None,
    max_tokens: int | None = None,
    settings: Settings | None = None,
) -> tuple[ChatResult, list[str]]:
    """Call an LLM node but keep the graph moving if the provider times out."""
    try:
        result = await call_node_llm(
            state,
            node_name=node_name,
            prompt=prompt,
            prompt_version=prompt_version,
            tier=tier,
            temperature=temperature,
            max_tokens=max_tokens,
            settings=settings,
        )
        return result, []
    except Exception as exc:
        message = f"{node_name} LLM call failed; using fallback content: {type(exc).__name__}: {exc}"
        return (
            ChatResult(
                content=fallback_content,
                model="fallback",
                provider="local",
                tier=tier,
                invocation="local_command" if tier == "premium" else "http",
                usage_estimated=True,
                raw={"error": message},
            ),
            [message],
        )
