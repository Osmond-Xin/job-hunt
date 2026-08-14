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

import hashlib
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
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
        rate_limit_qps: float = 1.0,
        rate_limit_retries: int = 2,
        sleep=time.sleep,
        monotonic=time.monotonic,
    ) -> None:
        if not api_key:
            raise ValueError("BraveProvider requires a non-empty api_key")
        self._api_key = api_key
        self._default_count = max(1, min(default_count, 20))
        self._default_freshness = default_freshness
        self._timeout_s = timeout_s
        self._min_interval = 1.0 / rate_limit_qps if rate_limit_qps > 0 else 0.0
        self._retries = max(0, int(rate_limit_retries))
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_call: float | None = None

    def _throttle(self) -> None:
        """Space requests out so we stay under the plan's queries-per-second."""
        if self._min_interval <= 0:
            return
        if self._last_call is not None:
            wait = self._min_interval - (self._monotonic() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._monotonic()

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
        for attempt in range(self._retries + 1):
            self._throttle()
            try:
                with httpx.Client(timeout=self._timeout_s) as client:
                    response = client.get(_BRAVE_ENDPOINT, params=params, headers=headers)
            except httpx.HTTPError:
                return []
            # 429 means we outran the plan's rate limit, not that the query has
            # no results. Back off and retry rather than reporting an empty
            # result set the caller would cache as a real answer.
            if response.status_code == 429 and attempt < self._retries:
                self._sleep(_retry_after_seconds(response, self._min_interval, attempt))
                continue
            if response.status_code >= 400:
                return []
            try:
                payload = response.json()
            except ValueError:
                return []
            return _parse_brave_response(payload)
        return []


def _retry_after_seconds(response, min_interval: float, attempt: int) -> float:
    """How long to wait before retrying a 429.

    Prefers the server's ``Retry-After``; otherwise backs off geometrically
    from the configured inter-request interval (with a 1s floor, since the
    Free plan's window is one second).
    """
    raw = (response.headers.get("Retry-After") or "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return max(1.0, min_interval) * (attempt + 1)


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


def format_search_hits(
    provider: WebSearchProvider | None,
    queries: list[str],
    *,
    label: str = "WebSearch results (Brave)",
    per_query_count: int = 3,
    description_chars: int = 280,
) -> str:
    """Run each query through the provider and format hits as a markdown block.

    Returns an empty string when ``provider`` is ``None``, ``queries`` is
    empty, or every query yields zero usable hits. Callers wire the return
    value into prompt context only when non-empty.

    Used by ``cli.py`` ``research --with-search`` and
    ``linkedin --with-search`` to ground the LLM on real web data, and is
    reusable by future ``--with-search`` style flags. URL deduping spans
    the whole call so the same hit on two queries only appears once.
    """
    if provider is None or not queries:
        return ""

    seen_urls: set[str] = set()
    lines: list[str] = []
    for query in queries:
        clean = query.strip()
        if not clean:
            continue
        try:
            hits = provider.search(clean, count=per_query_count)
        except Exception:
            continue
        for hit in hits:
            url = (getattr(hit, "url", "") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            lines.append(_format_hit_line(clean, hit, description_chars))

    if not lines:
        return ""
    return f"{label}:\n" + "\n".join(lines)


def _format_hit_line(query: str, hit: SearchHit, description_chars: int) -> str:
    title = (getattr(hit, "title", "") or "Untitled result").strip()
    url = (getattr(hit, "url", "") or "").strip()
    desc = (getattr(hit, "description", "") or "").strip()
    if len(desc) > description_chars:
        desc = f"{desc[: description_chars - 3].rstrip()}..."
    age = (getattr(hit, "age", None) or "").strip()
    suffix = f" ({age})" if age else ""
    line = f"- [{query}] {title}{suffix}: {url}"
    return f"{line}\n  {desc}" if desc else line


def build_web_search_provider(settings: Settings) -> WebSearchProvider | None:
    """Construct a provider from settings, or return None when disabled.

    When ``web_search.cache_enabled`` is true (the default), the underlying
    provider is wrapped in ``CachingProvider`` so identical queries within
    ``cache_ttl_seconds`` (default 24h) skip the API and the monthly quota
    counter under ``cache/web_search/<provider>/usage.json`` reflects API
    calls vs cache hits.
    """
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
        brave = BraveProvider(
            api_key=api_key,
            default_count=config.count,
            default_freshness=config.freshness,
            timeout_s=config.timeout_s,
            rate_limit_qps=getattr(config, "rate_limit_qps", 1.0),
            rate_limit_retries=getattr(config, "rate_limit_retries", 2),
        )
        if not getattr(config, "cache_enabled", True):
            return brave
        cache_dir = _resolve_cache_dir(settings, "brave")
        cache = WebSearchCache(
            cache_dir,
            ttl_seconds=getattr(config, "cache_ttl_seconds", 86_400),
        )
        return CachingProvider(
            brave,
            cache,
            default_count=config.count,
            default_freshness=config.freshness,
        )
    # Unknown provider name — silently disable rather than crashing the app.
    return None


def _resolve_cache_dir(settings: Settings, provider_slug: str) -> Path:
    paths = getattr(settings, "paths", None)
    base = getattr(paths, "cache_dir", None) if paths is not None else None
    root = Path(base) if base else Path("cache")
    return root / "web_search" / provider_slug


# ---------------------------------------------------------------------------
# Cache + quota counter
# ---------------------------------------------------------------------------


@dataclass
class WebSearchUsage:
    """One month's counters. Months are UTC ``YYYY-MM`` strings."""

    month: str
    api_calls: int = 0
    cache_hits: int = 0
    errors: int = 0


class WebSearchCache:
    """24h on-disk JSON cache + monthly usage counter.

    Layout under ``root``:

        entries/<sha256>.json   — one cached query: {created_at, hits[]}
        usage.json              — monthly counters: {"YYYY-MM": {...}}

    All file writes go through a tempfile+rename to stay atomic against
    concurrent CLI invocations on the same key. Reads tolerate missing /
    malformed files and return ``None`` / zero counters rather than raising —
    this is a personal-tool cache and a corrupted file should never break
    the apply/scan flow.
    """

    def __init__(self, root: Path, *, ttl_seconds: int = 86_400) -> None:
        self._root = Path(root)
        self._entries = self._root / "entries"
        self._usage_file = self._root / "usage.json"
        self._ttl = max(0, int(ttl_seconds))

    # ----- cache lookups -----

    def get(self, key: str) -> list[SearchHit] | None:
        path = self._entries / f"{key}.json"
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError:
            return None
        try:
            payload = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        created = payload.get("created_at")
        if not isinstance(created, str):
            return None
        if self._is_expired(created):
            return None
        items = payload.get("hits")
        if not isinstance(items, list):
            return None
        hits: list[SearchHit] = []
        for item in items:
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

    def put(self, key: str, hits: list[SearchHit]) -> None:
        self._entries.mkdir(parents=True, exist_ok=True)
        payload = {
            "created_at": _utcnow().isoformat(),
            "hits": [asdict(hit) for hit in hits],
        }
        _atomic_write_json(self._entries / f"{key}.json", payload)

    # ----- usage counters -----

    def record_api_call(self) -> None:
        self._bump("api_calls", 1)

    def record_cache_hit(self) -> None:
        self._bump("cache_hits", 1)

    def record_error(self) -> None:
        self._bump("errors", 1)

    def usage(self, *, month: str | None = None) -> WebSearchUsage:
        target = month or _utcnow().strftime("%Y-%m")
        data = self._read_usage()
        bucket = data.get(target) if isinstance(data, dict) else None
        if not isinstance(bucket, dict):
            return WebSearchUsage(month=target)
        return WebSearchUsage(
            month=target,
            api_calls=int(bucket.get("api_calls") or 0),
            cache_hits=int(bucket.get("cache_hits") or 0),
            errors=int(bucket.get("errors") or 0),
        )

    def usage_path(self) -> Path:
        return self._usage_file

    # ----- internals -----

    def _is_expired(self, iso: str) -> bool:
        if self._ttl <= 0:
            return False
        try:
            created = datetime.fromisoformat(iso)
        except ValueError:
            return True
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age = (_utcnow() - created).total_seconds()
        return age >= self._ttl

    def _bump(self, field: str, delta: int) -> None:
        data = self._read_usage()
        if not isinstance(data, dict):
            data = {}
        month = _utcnow().strftime("%Y-%m")
        bucket = data.get(month)
        if not isinstance(bucket, dict):
            bucket = {"api_calls": 0, "cache_hits": 0, "errors": 0}
        bucket[field] = int(bucket.get(field) or 0) + delta
        data[month] = bucket
        self._root.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(self._usage_file, data)

    def _read_usage(self) -> dict:
        try:
            raw = self._usage_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError:
            return {}
        try:
            payload = json.loads(raw)
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}


class CachingProvider:
    """Wraps a ``WebSearchProvider`` with the 24h cache + usage counter.

    Distinct ``(query, count, freshness)`` triples are independent cache
    keys; blank queries short-circuit (matching ``BraveProvider``). When the
    inner provider returns an empty list, we record an ``error`` and do NOT
    cache the result — a transient outage shouldn't be pinned for 24h.
    """

    def __init__(
        self,
        inner: WebSearchProvider,
        cache: WebSearchCache,
        *,
        default_count: int = 10,
        default_freshness: str = "pw",
    ) -> None:
        self._inner = inner
        self._cache = cache
        self._default_count = default_count
        self._default_freshness = default_freshness

    @property
    def inner(self) -> WebSearchProvider:
        return self._inner

    @property
    def cache(self) -> WebSearchCache:
        return self._cache

    def search(
        self,
        query: str,
        *,
        count: int | None = None,
        freshness: str | None = None,
    ) -> list[SearchHit]:
        if not query.strip():
            return []
        effective_count = count or self._default_count
        effective_fresh = freshness if freshness is not None else self._default_freshness
        key = _cache_key(query, effective_count, effective_fresh)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.record_cache_hit()
            return cached
        hits = self._inner.search(query, count=count, freshness=freshness)
        if hits:
            self._cache.record_api_call()
            self._cache.put(key, hits)
        else:
            # Could be a real "no results" or a transport error swallowed by
            # the inner provider. Either way: don't cache, count as error so
            # the operator can see quota wastage in `search-usage`.
            self._cache.record_error()
        return hits


def _cache_key(query: str, count: int, freshness: str | None) -> str:
    """Stable hash of the search parameters. Query is normalized (stripped +
    lowercased) so trivial whitespace differences hit the same entry."""
    normalized = "|".join(
        [
            query.strip().lower(),
            str(int(count)),
            (freshness or "").strip().lower(),
        ]
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _atomic_write_json(path: Path, payload: object) -> None:
    """Write JSON to ``path`` via a sibling tempfile + os.replace.

    Cross-platform atomic for a single filesystem; good enough for personal
    tooling concurrency (one CLI invocation at a time, occasionally two).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
