"""Tests for ``--with-search`` grounding on research / linkedin commands."""

from __future__ import annotations

from typing import Any

import pytest

from job_hunt.nodes._prompts import render
from job_hunt.services.web_search import SearchHit, format_search_hits


class _StubProvider:
    """Records queries; returns canned hits per query."""

    def __init__(self, mapping: dict[str, list[SearchHit]] | None = None) -> None:
        self.calls: list[str] = []
        self._mapping = mapping or {}

    def search(
        self,
        query: str,
        *,
        count: int | None = None,
        freshness: str | None = None,
    ) -> list[SearchHit]:
        self.calls.append(query)
        return list(self._mapping.get(query, []))


# --- format_search_hits ---


def test_format_search_hits_returns_empty_when_provider_none() -> None:
    assert format_search_hits(None, ["a"]) == ""


def test_format_search_hits_returns_empty_when_queries_empty() -> None:
    provider = _StubProvider()
    assert format_search_hits(provider, []) == ""
    assert provider.calls == []


def test_format_search_hits_renders_block_with_label_and_lines() -> None:
    hit = SearchHit(title="Acme launches Foo", url="https://acme.example/foo", description="Big news.", age="2d")
    provider = _StubProvider({"acme news": [hit]})
    block = format_search_hits(provider, ["acme news"], label="WebSearch (Brave)")
    assert block.startswith("WebSearch (Brave):\n")
    assert "https://acme.example/foo" in block
    assert "Acme launches Foo" in block
    assert "(2d)" in block
    assert "Big news." in block


def test_format_search_hits_dedupes_urls_across_queries() -> None:
    hit = SearchHit(title="T", url="https://x.example", description="d")
    provider = _StubProvider({"q1": [hit], "q2": [hit]})
    block = format_search_hits(provider, ["q1", "q2"])
    # Only one occurrence of the URL in the block.
    assert block.count("https://x.example") == 1


def test_format_search_hits_skips_blank_queries() -> None:
    provider = _StubProvider()
    block = format_search_hits(provider, ["", "  ", "\t"])
    assert block == ""
    assert provider.calls == []


def test_format_search_hits_swallows_provider_exceptions() -> None:
    class _Boom:
        def search(self, *_args: Any, **_kwargs: Any) -> list[SearchHit]:
            raise RuntimeError("API down")

    block = format_search_hits(_Boom(), ["q"])
    assert block == ""


def test_format_search_hits_truncates_long_descriptions() -> None:
    long_desc = "A" * 500
    hit = SearchHit(title="t", url="https://x.example", description=long_desc)
    provider = _StubProvider({"q": [hit]})
    block = format_search_hits(provider, ["q"], description_chars=50)
    assert "..." in block
    # Truncation respects the budget.
    truncated_line = [line for line in block.splitlines() if line.startswith("  ")][0]
    assert len(truncated_line.strip()) <= 60  # 50 + ellipsis


def test_format_search_hits_skips_results_with_blank_url() -> None:
    blank = SearchHit(title="t", url="", description="d")
    real = SearchHit(title="real", url="https://x.example", description="d")
    provider = _StubProvider({"q": [blank, real]})
    block = format_search_hits(provider, ["q"])
    assert "https://x.example" in block
    assert block.count("- [q]") == 1  # blank-url hit skipped


# --- prompt rendering: research_context is optional ---


def _research_context_args(extra: dict | None = None) -> dict:
    base = {
        "company": "Acme",
        "role": "AI Engineer",
        "jd_text": "JD text here.",
        "cv_excerpt": "CV excerpt here.",
        "research_context": "",
    }
    if extra:
        base.update(extra)
    return base


def test_deep_research_prompt_omits_block_when_context_empty() -> None:
    out = render("deep_research.md", **_research_context_args())
    assert "Live web snippets" not in out


def test_deep_research_prompt_renders_block_when_context_present() -> None:
    block = "WebSearch results (Brave):\n- [acme news] Hit: https://acme.example"
    out = render("deep_research.md", **_research_context_args({"research_context": block}))
    assert "Live web snippets" in out
    assert "https://acme.example" in out


def test_linkedin_prompt_omits_block_when_context_empty() -> None:
    out = render("linkedin_outreach.md", **_research_context_args())
    assert "Live web snippets" not in out


def test_linkedin_prompt_renders_block_when_context_present() -> None:
    block = "WebSearch results (Brave):\n- [acme news] Hit: https://acme.example"
    out = render("linkedin_outreach.md", **_research_context_args({"research_context": block}))
    assert "Live web snippets" in out
    assert "use one of these recent items as the **hook**" in out.lower()
    assert "https://acme.example" in out


# --- _build_research_context_for_prompt CLI helper ---


def test_build_research_context_returns_empty_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from job_hunt.cli import _build_research_context_for_prompt

    # Even with a working provider, --with-search=False short-circuits.
    monkeypatch.setattr(
        "job_hunt.services.web_search.build_web_search_provider",
        lambda _settings: _StubProvider({"x": []}),
    )
    out = _build_research_context_for_prompt(
        company="Acme", role="AI Engineer", enabled=False, purpose="research",
    )
    assert out == ""


def test_build_research_context_returns_empty_when_provider_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt.cli import _build_research_context_for_prompt

    monkeypatch.setattr(
        "job_hunt.services.web_search.build_web_search_provider",
        lambda _settings: None,
    )
    out = _build_research_context_for_prompt(
        company="Acme", role="AI Engineer", enabled=True, purpose="research",
    )
    assert out == ""


def test_build_research_context_uses_research_query_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    from job_hunt.cli import _build_research_context_for_prompt

    provider = _StubProvider({
        "Acme AI Engineer engineering team": [
            SearchHit(title="t", url="https://x.example", description="d"),
        ],
    })
    monkeypatch.setattr(
        "job_hunt.services.web_search.build_web_search_provider",
        lambda _settings: provider,
    )
    out = _build_research_context_for_prompt(
        company="Acme", role="AI Engineer", enabled=True, purpose="research",
    )
    assert "https://x.example" in out
    assert "Acme AI Engineer engineering team" in provider.calls
    assert "Acme recent product news" in provider.calls


def test_build_research_context_uses_linkedin_query_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    from job_hunt.cli import _build_research_context_for_prompt

    provider = _StubProvider()
    monkeypatch.setattr(
        "job_hunt.services.web_search.build_web_search_provider",
        lambda _settings: provider,
    )
    _build_research_context_for_prompt(
        company="Acme", role="AI Engineer", enabled=True, purpose="linkedin",
    )
    assert "Acme news AI Engineer" in provider.calls
    assert "Acme product announcement" in provider.calls
    assert "Acme AI Engineer engineering team" not in provider.calls
