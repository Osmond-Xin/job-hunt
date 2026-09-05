from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from job_hunt.config.models import Settings
from job_hunt.services.llm.base import ChatMessage, ChatResult
from job_hunt.services.observability import get_langsmith_traceable, write_usage_ledger


async def traced_chat(
    provider,
    *,
    settings: Settings,
    messages: list[ChatMessage],
    model: str,
    node_name: str,
    graph_name: str,
    model_tier: str,
    temperature: float = 0.2,
    max_tokens: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> ChatResult:
    traceable = get_langsmith_traceable(settings)

    async def call() -> ChatResult:
        started = time.perf_counter()
        result = await provider.chat(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            trace_name=f"{provider.provider}.chat",
            trace_metadata=metadata or {},
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        write_usage_ledger(
            settings,
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "trace_id": f"local:{uuid.uuid4().hex}",
                "graph_name": graph_name,
                "node_name": node_name,
                "provider": result.provider,
                "model": result.model,
                "model_tier": model_tier,
                "invocation": result.invocation,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "total_tokens": result.total_tokens,
                "latency_ms": latency_ms,
                "usage_estimated": result.usage_estimated,
                "cost_usd": result.cost_usd,
            },
        )
        return result

    if traceable is None:
        return await call()

    @traceable(
        name=f"{graph_name}.{node_name}",
        run_type="llm",
        metadata={
            "app": "job-hunt",
            "graph_name": graph_name,
            "node_name": node_name,
            "model_tier": model_tier,
            "provider": getattr(provider, "provider", "unknown"),
            "model": model,
            **(metadata or {}),
        },
    )
    async def traced_call() -> dict:
        result = await call()
        try:
            from langsmith.run_helpers import get_current_run_tree

            run_tree = get_current_run_tree()
            if run_tree:
                run_tree.set(
                    usage_metadata={
                        "input_tokens": result.input_tokens,
                        "output_tokens": result.output_tokens,
                        "total_tokens": result.total_tokens,
                    }
                )
        except Exception:
            if settings.observability.langsmith.fail_closed:
                raise
        return result.model_dump()

    raw = await traced_call()
    return ChatResult.model_validate(raw)
