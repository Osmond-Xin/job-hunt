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


def test_factory_builds_brave_when_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("BRAVE_API_KEY", "sk-real-key")
    monkeypatch.chdir(tmp_path)  # confine cache dir to tmpdir
    settings = Settings(web_search=WebSearchConfig(provider="brave", count=5))
    provider = build_web_search_provider(settings)
    # Default config wraps in CachingProvider; the inner is a BraveProvider.
    from job_hunt.services.web_search import CachingProvider

    assert isinstance(provider, CachingProvider)
    assert isinstance(provider.inner, BraveProvider)


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


# ----- rate limiting / 429 handling (added 2026-08-06) -----
#
# Regression guard: a 336-query scan on Brave's Free plan (1 query/second)
# produced 247 failures and 5 hits, because a 429 was swallowed by the
# generic `status_code >= 400: return []` branch and reported as "no results".


def _client_patch(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def make_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr("job_hunt.services.web_search.httpx.Client", make_client)


def test_brave_provider_throttles_between_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    _client_patch(
        monkeypatch,
        lambda request: httpx.Response(200, json={"web": {"results": []}}, request=request),
    )
    slept: list[float] = []
    clock = {"t": 0.0}

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["t"] += seconds

    provider = BraveProvider(
        api_key="sk-test",
        rate_limit_qps=1.0,
        sleep=fake_sleep,
        monotonic=lambda: clock["t"],
    )
    provider.search("one")
    provider.search("two")

    # Second call had to wait out the remainder of the 1s window.
    assert slept == [pytest.approx(1.0)]


def test_brave_provider_retries_on_429_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"web": {"results": [{"title": "T", "url": "https://example.com/a"}]}}
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "rate limited"}, request=request)
        return httpx.Response(200, json=payload, request=request)

    _client_patch(monkeypatch, handler)
    provider = BraveProvider(
        api_key="sk-test", rate_limit_qps=0, sleep=lambda s: None, rate_limit_retries=2
    )
    hits = provider.search("q")

    assert calls["n"] == 2
    assert len(hits) == 1


def test_brave_provider_honours_retry_after_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429, headers={"Retry-After": "3"}, json={}, request=request
            )
        return httpx.Response(200, json={"web": {"results": []}}, request=request)

    _client_patch(monkeypatch, handler)
    slept: list[float] = []
    provider = BraveProvider(
        api_key="sk-test", rate_limit_qps=0, sleep=slept.append, rate_limit_retries=2
    )
    provider.search("q")

    assert slept == [pytest.approx(3.0)]


def test_brave_provider_gives_up_after_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, json={}, request=request)

    _client_patch(monkeypatch, handler)
    provider = BraveProvider(
        api_key="sk-test", rate_limit_qps=0, sleep=lambda s: None, rate_limit_retries=2
    )

    assert provider.search("q") == []
    # initial attempt + 2 retries
    assert calls["n"] == 3


def test_build_provider_passes_rate_limit_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAVE_API_KEY", "sk-test")
    settings = Settings(
        web_search=WebSearchConfig(
            provider="brave", cache_enabled=False, rate_limit_qps=50.0
        )
    )
    provider = build_web_search_provider(settings)

    assert isinstance(provider, BraveProvider)
    assert provider._min_interval == pytest.approx(1 / 50)
