"""Web search provider abstraction + Brave Search implementation.

Used by:
- ``services/scan.py::scan_via_websearch`` to complete the 3-tier scan
  (P1-4 sub-phase 4b).
- ``nodes/research.py::company_comp_research`` to inject Glassdoor / Levels.fyi
  / Blind summaries into the comp research prompt context.
- CLI ``job-hunt search-test`` smoke command.

The factory ``build_web_search_provider(settings)`` returns ``None`` when no
provider is configured or when the API key env var is unset. Callers MUST
tolerate ``None`` and degrade gracefully (skip the search step, no error).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import httpx

from job_hunt.config.models import Settings


_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


@dataclass
class SearchHit:
    """One result from a web search. ``age`` is None when the engine doesn't
    return a freshness string for this hit."""

    title: str
    url: str
    description: str
    age: str | None = None


class WebSearchProvider(Protocol):
    """Sync protocol — search calls are fast and tests are simpler this way."""

    def search(
        self,
        query: str,
        *,
        count: int | None = None,
        freshness: str | None = None,
    ) -> list[SearchHit]:
        ...


class BraveProvider:
    """Brave Search API adapter.

    Auth header: ``X-Subscription-Token``. The endpoint enforces ``count`` ≤ 20
    and accepts freshness codes ``pd`` / ``pw`` / ``pm`` / ``py`` (past day /
    week / month / year). We pass ``result_filter=web`` so we only get web
    results — the news/discussion sections of the response are dropped to
    keep payloads small.
    """

    def __init__(
        self,
        api_key: str,
        *,
        default_count: int = 10,
        default_freshness: str = "pw",
        timeout_s: float = 10.0,
    ) -> None:
        if not api_key:
            raise ValueError("BraveProvider requires a non-empty api_key")
        self._api_key = api_key
        self._default_count = max(1, min(default_count, 20))
        self._default_freshness = default_freshness
        self._timeout_s = timeout_s

    def search(
        self,
        query: str,
        *,
        count: int | None = None,
        freshness: str | None = None,
    ) -> list[SearchHit]:
        if not query.strip():
            return []
        params = {
            "q": query,
            "count": str(count or self._default_count),
            "result_filter": "web",
        }
        fresh = freshness if freshness is not None else self._default_freshness
        if fresh:
            params["freshness"] = fresh
        headers = {
            "X-Subscription-Token": self._api_key,
            "Accept": "application/json",
            "User-Agent": "job-hunt/0.1",
        }
        try:
            with httpx.Client(timeout=self._timeout_s) as client:
                response = client.get(_BRAVE_ENDPOINT, params=params, headers=headers)
        except httpx.HTTPError:
            return []
        if response.status_code >= 400:
            return []
        try:
            payload = response.json()
        except ValueError:
            return []
        return _parse_brave_response(payload)


def _parse_brave_response(payload: dict) -> list[SearchHit]:
    """Pull the ``web.results`` list out of a Brave response. Tolerant of
    missing keys / unexpected types so a one-off API change doesn't crash
    the apply or scan flow.
    """
    web = payload.get("web") if isinstance(payload, dict) else None
    if not isinstance(web, dict):
        return []
    results = web.get("results")
    if not isinstance(results, list):
        return []
    hits: list[SearchHit] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = (item.get("url") or "").strip()
        if not url:
            continue
        hits.append(
            SearchHit(
                title=str(item.get("title") or "").strip(),
                url=url,
                description=str(item.get("description") or "").strip(),
                age=item.get("age") if isinstance(item.get("age"), str) else None,
            )
        )
    return hits


def build_web_search_provider(settings: Settings) -> WebSearchProvider | None:
    """Construct a provider from settings, or return None when disabled."""
    config = getattr(settings, "web_search", None)
    if config is None:
        return None
    provider = (config.provider or "").lower()
    if provider in {"", "none"}:
        return None
    if provider == "brave":
        api_key = os.getenv(config.api_key_env or "BRAVE_API_KEY", "").strip()
        if not api_key:
            return None
        return BraveProvider(
            api_key=api_key,
            default_count=config.count,
            default_freshness=config.freshness,
            timeout_s=config.timeout_s,
        )
    # Unknown provider name — silently disable rather than crashing the app.
    return None
