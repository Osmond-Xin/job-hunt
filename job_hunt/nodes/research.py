"""company_comp_research node."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from job_hunt.config.models import load_settings
from job_hunt.models.job import JobMeta
from job_hunt.models.state import JobHuntState
from job_hunt.nodes._llm import call_node_llm_or_fallback
from job_hunt.nodes._prompts import render
from job_hunt.services.web_search import WebSearchProvider, build_web_search_provider


def _resolve_web_search_provider(config: RunnableConfig) -> WebSearchProvider | None:
    """Resolve an optional provider from RunnableConfig or settings.

    Tests can inject ``configurable.web_search_provider``. Normal graph runs build
    the provider from ``config/settings.yml`` and gracefully skip search when the
    provider is disabled or the API key env var is missing.
    """
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    if isinstance(configurable, dict) and "web_search_provider" in configurable:
        return configurable["web_search_provider"]
    return build_web_search_provider(load_settings())


def _format_hit(query: str, hit: Any) -> str:
    desc = (getattr(hit, "description", "") or "").strip()
    if len(desc) > 280:
        desc = f"{desc[:277].rstrip()}..."
    age = (getattr(hit, "age", None) or "").strip()
    suffix = f" ({age})" if age else ""
    title = (getattr(hit, "title", "") or "Untitled result").strip()
    url = (getattr(hit, "url", "") or "").strip()
    line = f"- [{query}] {title}{suffix}: {url}"
    if desc:
        line = f"{line}\n  {desc}"
    return line


def build_comp_research_context(
    jd_meta: JobMeta | None,
    provider: WebSearchProvider | None,
    *,
    existing_context: str = "",
) -> str:
    """Build the web snippet block for the compensation research prompt."""
    chunks: list[str] = []
    existing = existing_context.strip()
    if existing:
        chunks.append(existing)
    if not jd_meta or not jd_meta.company or provider is None:
        return "\n\n".join(chunks)

    company = jd_meta.company.strip()
    role = jd_meta.title.strip()
    location = jd_meta.location.strip()
    queries = [
        f"{company} {role} salary {location}".strip(),
        f"{company} levels.fyi {role}".strip(),
        f"{company} glassdoor blind culture compensation".strip(),
    ]

    seen_urls: set[str] = set()
    lines: list[str] = []
    for query in queries:
        try:
            hits = provider.search(query, count=3)
        except Exception:
            continue
        for hit in hits[:3]:
            url = (getattr(hit, "url", "") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            lines.append(_format_hit(query, hit))

    if lines:
        chunks.append("WebSearch results (Brave):\n" + "\n".join(lines))
    return "\n\n".join(chunks)


async def company_comp_research(state: JobHuntState, config: RunnableConfig) -> dict:
    jd_meta = state.get("jd_meta")
    provider = _resolve_web_search_provider(config)
    research_context = build_comp_research_context(
        jd_meta,
        provider,
        existing_context=state.get("proof_points", "") or "",
    )

    prompt = render(
        "evaluate/comp_research.md",
        jd_meta=jd_meta,
        research_context=research_context,
    )
    result, errors = await call_node_llm_or_fallback(
        state,
        node_name="company_comp_research",
        prompt=prompt,
        prompt_version="evaluate/comp_research.md:v1",
        fallback_content=(
            "Company and compensation research unavailable because the LLM provider timed out or failed. "
            "Verify company, compensation, location, and work authorization manually before applying."
        ),
        temperature=0.2,
        max_tokens=1000,
    )
    return {"evaluation_blocks": {"comp_research": result.content}, "errors": errors}
