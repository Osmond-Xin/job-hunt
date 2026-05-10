"""Tests for Brave-backed company_comp_research prompt context."""

from __future__ import annotations

import asyncio

from job_hunt.models.job import JobMeta
from job_hunt.nodes import research as research_module
from job_hunt.nodes.research import build_comp_research_context, company_comp_research
from job_hunt.services.llm.base import ChatResult
from job_hunt.services.web_search import SearchHit


class _StubProvider:
    def __init__(self) -> None:
        self.queries: list[tuple[str, int | None]] = []

    def search(self, query: str, *, count=None, freshness=None) -> list[SearchHit]:
        self.queries.append((query, count))
        return [
            SearchHit(
                title=f"{query} result",
                url=f"https://example.com/{len(self.queries)}",
                description="Useful public snippet.",
                age="2 days ago",
            )
        ]


def test_build_comp_research_context_runs_three_brave_queries() -> None:
    provider = _StubProvider()
    jd_meta = JobMeta(company="Anthropic", title="AI Engineer", location="Toronto")

    context = build_comp_research_context(jd_meta, provider, existing_context="Existing proof.")

    assert "Existing proof." in context
    assert "WebSearch results (Brave):" in context
    assert "Anthropic AI Engineer salary Toronto" in context
    assert "Anthropic levels.fyi AI Engineer" in context
    assert "Anthropic glassdoor blind culture compensation" in context
    assert all(count == 3 for _, count in provider.queries)


def test_build_comp_research_context_skips_without_provider() -> None:
    jd_meta = JobMeta(company="Anthropic", title="AI Engineer", location="Toronto")
    assert build_comp_research_context(jd_meta, None, existing_context="Proof") == "Proof"


def test_company_comp_research_injects_web_context(monkeypatch) -> None:
    provider = _StubProvider()
    captured: dict[str, str] = {}

    async def fake_call(state, *, prompt, **kwargs):
        captured["prompt"] = prompt
        return (
            ChatResult(
                content="briefing",
                model="test",
                provider="test",
                tier="cheap",
                invocation="http",
            ),
            [],
        )

    monkeypatch.setattr(research_module, "call_node_llm_or_fallback", fake_call)

    result = asyncio.run(
        company_comp_research(
            {
                "jd_meta": JobMeta(
                    company="Anthropic",
                    title="AI Engineer",
                    location="Toronto",
                ),
                "proof_points": "Existing proof.",
            },
            {"configurable": {"web_search_provider": provider}},
        )
    )

    assert result["evaluation_blocks"]["comp_research"] == "briefing"
    assert "## Web research snippets" in captured["prompt"]
    assert "Existing proof." in captured["prompt"]
    assert "WebSearch results (Brave):" in captured["prompt"]
    assert "https://example.com/1" in captured["prompt"]
