"""Tests for the Brave web search adapter and factory."""

from __future__ import annotations

import os

import httpx
import pytest

from job_hunt.config.models import Settings, WebSearchConfig
from job_hunt.services.web_search import (
    BraveProvider,
    SearchHit,
    _parse_brave_response,
    build_web_search_provider,
)


# ----- _parse_brave_response -----


def test_parse_brave_response_extracts_hits() -> None:
    payload = {
        "web": {
            "results": [
                {
                    "title": "Anthropic — AI Engineer",
                    "url": "https://www.anthropic.com/careers/ai-engineer",
                    "description": "Build safe AI.",
                    "age": "2 days ago",
                },
                {
                    "title": "Cohere — LLM Engineer",
                    "url": "https://jobs.ashbyhq.com/cohere/llm-eng",
                    "description": "Toronto, remote.",
                },
            ]
        }
    }
    hits = _parse_brave_response(payload)
    assert len(hits) == 2
    assert hits[0].url.endswith("/ai-engineer")
    assert hits[0].age == "2 days ago"
    assert hits[1].age is None  # missing key tolerated


def test_parse_brave_response_skips_results_without_url() -> None:
    payload = {"web": {"results": [{"title": "no url"}]}}
    assert _parse_brave_response(payload) == []


def test_parse_brave_response_handles_malformed_payload() -> None:
    assert _parse_brave_response({}) == []
    assert _parse_brave_response({"web": "string-instead-of-dict"}) == []
    assert _parse_brave_response({"web": {"results": "not-a-list"}}) == []


# ----- BraveProvider.search (with mocked httpx transport) -----


def _mock_transport_response(json_payload: dict, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=json_payload, request=request)

    return httpx.MockTransport(handler)


def test_brave_provider_returns_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"web": {"results": [
        {"title": "T", "url": "https://example.com/a", "description": "D", "age": "1d"},
    ]}}

    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json=payload, request=request)

    transport = httpx.MockTransport(handler)

    # Patch httpx.Client to use our transport
    real_client = httpx.Client

    def make_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr("job_hunt.services.web_search.httpx.Client", make_client)

    provider = BraveProvider(api_key="sk-test", default_count=5, default_freshness="pw")
    hits = provider.search("Anthropic AI Engineer")

    assert len(hits) == 1
    assert hits[0].url == "https://example.com/a"
    request = captured["request"]
    # Auth header carries our key
    assert request.headers.get("X-Subscription-Token") == "sk-test"
    # Default freshness propagated
    assert "freshness=pw" in str(request.url)
    assert "result_filter=web" in str(request.url)


def test_brave_provider_returns_empty_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _mock_transport_response({}, status=429)
    real_client = httpx.Client

    def make_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr("job_hunt.services.web_search.httpx.Client", make_client)
    provider = BraveProvider(api_key="sk-test")
    assert provider.search("anything") == []


def test_brave_provider_returns_empty_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def raising_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    transport = httpx.MockTransport(raising_handler)
    real_client = httpx.Client

    def make_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr("job_hunt.services.web_search.httpx.Client", make_client)
    provider = BraveProvider(api_key="sk-test")
    assert provider.search("anything") == []


def test_brave_provider_skips_blank_query() -> None:
    """Empty / whitespace-only query short-circuits before any HTTP call."""
    provider = BraveProvider(api_key="sk-test")
    assert provider.search("") == []
    assert provider.search("   ") == []


def test_brave_provider_rejects_blank_api_key() -> None:
    with pytest.raises(ValueError):
        BraveProvider(api_key="")


# ----- build_web_search_provider factory -----


def test_factory_disabled_when_provider_none() -> None:
    settings = Settings(web_search=WebSearchConfig(provider="none"))
    assert build_web_search_provider(settings) is None


def test_factory_returns_none_when_api_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    settings = Settings(web_search=WebSearchConfig(provider="brave"))
    assert build_web_search_provider(settings) is None


def test_factory_builds_brave_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAVE_API_KEY", "sk-real-key")
    settings = Settings(web_search=WebSearchConfig(provider="brave", count=5))
    provider = build_web_search_provider(settings)
    assert isinstance(provider, BraveProvider)


def test_factory_respects_custom_env_var_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAVE_KEY_ALT", "sk-alt")
    settings = Settings(
        web_search=WebSearchConfig(provider="brave", api_key_env="BRAVE_KEY_ALT")
    )
    assert build_web_search_provider(settings) is not None


def test_factory_unknown_provider_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pydantic validates Literal so this can only happen if the field is mutated
    after construction (e.g. tests). The factory should still return None rather
    than raise."""
    settings = Settings(web_search=WebSearchConfig(provider="brave"))
    settings.web_search.provider = "tavily"  # bypass validation deliberately
    monkeypatch.setenv("BRAVE_API_KEY", "x")
    assert build_web_search_provider(settings) is None
