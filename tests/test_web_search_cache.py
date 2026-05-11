"""Tests for the on-disk WebSearch cache + monthly quota counter."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from job_hunt.config.models import PathsConfig, Settings, WebSearchConfig
from job_hunt.services.web_search import (
    CachingProvider,
    SearchHit,
    WebSearchCache,
    _cache_key,
    build_web_search_provider,
)


# ----- _cache_key normalization -----


def test_cache_key_is_stable_for_equivalent_queries() -> None:
    a = _cache_key("Anthropic AI Engineer", 5, "pw")
    b = _cache_key("  anthropic ai engineer  ", 5, "PW")
    assert a == b


def test_cache_key_differs_on_count_or_freshness() -> None:
    base = _cache_key("q", 5, "pw")
    assert base != _cache_key("q", 10, "pw")
    assert base != _cache_key("q", 5, "pd")


# ----- WebSearchCache get/put -----


def test_cache_put_then_get_roundtrip(tmp_path: Path) -> None:
    cache = WebSearchCache(tmp_path)
    hits = [
        SearchHit(title="T1", url="https://example.com/1", description="d1", age="1d"),
        SearchHit(title="T2", url="https://example.com/2", description="d2"),
    ]
    cache.put("k1", hits)

    got = cache.get("k1")
    assert got is not None
    assert [h.url for h in got] == [h.url for h in hits]
    assert got[0].age == "1d"
    assert got[1].age is None


def test_cache_get_missing_returns_none(tmp_path: Path) -> None:
    cache = WebSearchCache(tmp_path)
    assert cache.get("never-written") is None


def test_cache_get_malformed_returns_none(tmp_path: Path) -> None:
    cache = WebSearchCache(tmp_path)
    (tmp_path / "entries").mkdir(parents=True)
    (tmp_path / "entries" / "bad.json").write_text("not-json", encoding="utf-8")
    assert cache.get("bad") is None


def test_cache_get_expires_after_ttl(tmp_path: Path) -> None:
    cache = WebSearchCache(tmp_path, ttl_seconds=60)
    cache.put("k", [SearchHit(title="T", url="https://x", description="")])

    # Backdate created_at to 2 hours ago
    entry = tmp_path / "entries" / "k.json"
    payload = json.loads(entry.read_text(encoding="utf-8"))
    payload["created_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=2)
    ).isoformat()
    entry.write_text(json.dumps(payload), encoding="utf-8")

    assert cache.get("k") is None


def test_cache_get_skips_entries_without_created_at(tmp_path: Path) -> None:
    cache = WebSearchCache(tmp_path)
    (tmp_path / "entries").mkdir(parents=True)
    (tmp_path / "entries" / "k.json").write_text(
        json.dumps({"hits": []}), encoding="utf-8"
    )
    assert cache.get("k") is None


# ----- Usage counters -----


def test_usage_starts_at_zero(tmp_path: Path) -> None:
    cache = WebSearchCache(tmp_path)
    usage = cache.usage()
    assert usage.api_calls == 0
    assert usage.cache_hits == 0
    assert usage.errors == 0


def test_usage_increments_per_bucket(tmp_path: Path) -> None:
    cache = WebSearchCache(tmp_path)
    cache.record_api_call()
    cache.record_api_call()
    cache.record_cache_hit()
    cache.record_error()
    usage = cache.usage()
    assert usage.api_calls == 2
    assert usage.cache_hits == 1
    assert usage.errors == 1


def test_usage_is_per_month(tmp_path: Path) -> None:
    cache = WebSearchCache(tmp_path)
    cache.record_api_call()

    # Manually inject a prior month bucket so we don't have to time-travel.
    data = json.loads(cache.usage_path().read_text(encoding="utf-8"))
    data["2025-01"] = {"api_calls": 99, "cache_hits": 0, "errors": 0}
    cache.usage_path().write_text(json.dumps(data), encoding="utf-8")

    current = cache.usage()
    older = cache.usage(month="2025-01")
    assert current.api_calls == 1
    assert older.api_calls == 99


def test_usage_tolerates_corrupted_file(tmp_path: Path) -> None:
    cache = WebSearchCache(tmp_path)
    cache._root.mkdir(parents=True, exist_ok=True)
    cache.usage_path().write_text("not-json", encoding="utf-8")
    # Recovers — corrupt usage file should never crash a search call.
    assert cache.usage().api_calls == 0
    cache.record_api_call()
    assert cache.usage().api_calls == 1


# ----- CachingProvider wrapping -----


class _StubProvider:
    """Records every call so tests can verify the cache served hits."""

    def __init__(self, hits: list[SearchHit]) -> None:
        self._hits = list(hits)
        self.calls: list[tuple[str, int | None, str | None]] = []

    def search(
        self,
        query: str,
        *,
        count: int | None = None,
        freshness: str | None = None,
    ) -> list[SearchHit]:
        self.calls.append((query, count, freshness))
        return list(self._hits)


def _make_caching_provider(tmp_path: Path, stub: _StubProvider) -> CachingProvider:
    cache = WebSearchCache(tmp_path)
    return CachingProvider(stub, cache, default_count=10, default_freshness="pw")


def test_caching_provider_first_call_hits_api_second_call_serves_cache(
    tmp_path: Path,
) -> None:
    stub = _StubProvider([SearchHit(title="T", url="https://x", description="")])
    provider = _make_caching_provider(tmp_path, stub)

    a = provider.search("Anthropic AI Engineer")
    b = provider.search("Anthropic AI Engineer")

    assert a == b
    # Second call did not hit the inner provider.
    assert len(stub.calls) == 1
    usage = provider.cache.usage()
    assert usage.api_calls == 1
    assert usage.cache_hits == 1
    assert usage.errors == 0


def test_caching_provider_separate_counts_are_separate_keys(tmp_path: Path) -> None:
    stub = _StubProvider([SearchHit(title="T", url="https://x", description="")])
    provider = _make_caching_provider(tmp_path, stub)

    provider.search("q", count=5)
    provider.search("q", count=10)

    assert len(stub.calls) == 2


def test_caching_provider_normalizes_whitespace_and_case(tmp_path: Path) -> None:
    stub = _StubProvider([SearchHit(title="T", url="https://x", description="")])
    provider = _make_caching_provider(tmp_path, stub)

    provider.search("Cohere AI Engineer")
    provider.search("  cohere ai engineer  ")

    assert len(stub.calls) == 1
    assert provider.cache.usage().cache_hits == 1


def test_caching_provider_blank_query_short_circuits(tmp_path: Path) -> None:
    stub = _StubProvider([SearchHit(title="T", url="https://x", description="")])
    provider = _make_caching_provider(tmp_path, stub)

    assert provider.search("") == []
    assert provider.search("   ") == []
    assert stub.calls == []
    usage = provider.cache.usage()
    assert usage.api_calls == 0
    assert usage.cache_hits == 0


def test_caching_provider_empty_hits_record_error_and_skip_cache(
    tmp_path: Path,
) -> None:
    stub = _StubProvider([])
    provider = _make_caching_provider(tmp_path, stub)

    # First call: API returns []. Should NOT cache the empty result.
    assert provider.search("nothing") == []
    # Second call: re-hits the API (no cached empty) and counts again.
    assert provider.search("nothing") == []

    assert len(stub.calls) == 2
    usage = provider.cache.usage()
    assert usage.api_calls == 0
    assert usage.cache_hits == 0
    assert usage.errors == 2


# ----- Factory wiring -----


def test_factory_wraps_brave_in_caching_provider_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BRAVE_API_KEY", "sk-real")
    settings = Settings(
        paths=PathsConfig(cache_dir=tmp_path),
        web_search=WebSearchConfig(provider="brave"),
    )
    provider = build_web_search_provider(settings)
    assert isinstance(provider, CachingProvider)


def test_factory_returns_raw_brave_when_cache_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BRAVE_API_KEY", "sk-real")
    settings = Settings(
        paths=PathsConfig(cache_dir=tmp_path),
        web_search=WebSearchConfig(provider="brave", cache_enabled=False),
    )
    provider = build_web_search_provider(settings)
    assert not isinstance(provider, CachingProvider)


def test_factory_cache_dir_lives_under_settings_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BRAVE_API_KEY", "sk-real")
    settings = Settings(
        paths=PathsConfig(cache_dir=tmp_path),
        web_search=WebSearchConfig(provider="brave"),
    )
    provider = build_web_search_provider(settings)
    assert isinstance(provider, CachingProvider)
    expected = tmp_path / "web_search" / "brave"
    # Cache root is the parent of usage.json.
    assert provider.cache.usage_path().parent == expected
