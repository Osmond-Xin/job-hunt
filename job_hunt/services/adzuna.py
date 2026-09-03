"""Adzuna aggregator API adapter — replaces the Brave `site:` job channels.

Why this beats the search-engine channels it replaces
-----------------------------------------------------
One Adzuna call returns up to 50 postings with structured ``title``,
``company.display_name``, ``location.display_name``, salary and
``created`` date. One Brave call returns ten links that then have to be
guessed at. Measured on the same scan: the ``indeed_canada`` Brave channel
spent 78 queries to put 6 rows in the pipeline; a single Adzuna query for
"software developer" reports 3,507 Canadian matches inside 30 days.

Query shape (verified 2026-08-06 against the live API):
``GET https://api.adzuna.com/v1/api/jobs/{country}/search/{page}``
with ``app_id`` + ``app_key`` and ``what`` / ``results_per_page`` /
``max_days_old`` / ``sort_by=date``.

``where`` is deliberately NOT used by default. It filters hard: "data
analyst" nationally returns 477 matches but only 1 when scoped to Halifax,
and 0 for Yellowknife. Querying nationally and letting the downstream
location filter do the work yields far more, and the country is already
pinned by the ``country`` path segment.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

_ENDPOINT = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def parse_adzuna_results(
    payload: dict[str, Any], *, category_exclude: list[str] | None = None
) -> list[dict[str, str]]:
    """Map an Adzuna search payload onto flat rows.

    ``category_exclude`` drops postings whose Adzuna category tag is clearly
    off-target. Rows keep their ``description`` and ``category`` so callers
    can screen on content rather than on job title.
    """
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return []
    blocked = {c.strip().lower() for c in (category_exclude or []) if c}
    rows: list[dict[str, str]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = _clean(item.get("redirect_url"))
        title = _clean(item.get("title"))
        if not url or not title:
            continue
        category = _clean((item.get("category") or {}).get("tag")).lower()
        if category and category in blocked:
            continue
        company = item.get("company") or {}
        location = item.get("location") or {}
        rows.append(
            {
                "url": url,
                "title": title,
                "company": _clean(company.get("display_name")) or "Unknown",
                "location": _clean(location.get("display_name")),
                "created": _clean(item.get("created")),
                "category": category,
                "description": _clean(item.get("description")),
            }
        )
    return rows


def fetch_adzuna_page(
    what: str,
    page: int,
    *,
    app_id: str,
    app_key: str,
    country: str = "ca",
    results_per_page: int = 50,
    max_days_old: int = 30,
    client: httpx.Client,
) -> dict[str, Any]:
    """One search page. Returns ``{}`` on any transport or API failure."""
    url = _ENDPOINT.format(country=country, page=max(1, page))
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": results_per_page,
        "what": what,
        "max_days_old": max_days_old,
        "sort_by": "date",
    }
    try:
        response = client.get(url, params=params)
    except httpx.HTTPError:
        return {}
    if response.status_code != 200:
        return {}
    try:
        return response.json()
    except ValueError:
        return {}


def scan_adzuna(
    config: Any,
    roles: list[str],
    *,
    app_id: str,
    app_key: str,
    client: httpx.Client | None = None,
    sleep=time.sleep,
    stats: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Fetch ``roles`` x pages from Adzuna. De-duplicated by URL.

    ``config`` is the ``AdzunaConfig`` model (or anything with the same
    attributes). Missing credentials or a disabled config yield ``[]``.

    Pass ``stats`` to find out whether each role's pages actually loaded.
    ``fetch_adzuna_page`` returns ``{}`` on a transport error, a non-200
    response or invalid JSON — a real Adzuna response always carries a
    ``results`` key (empty list included), so ``{}`` is counted as a failed
    request here rather than silently read as "no matches for this role".
    Keyed by role.
    """
    if config is None or not getattr(config, "enabled", False):
        return []
    if not app_id or not app_key or not roles:
        return []

    country = getattr(config, "country", "ca")
    per_page = int(getattr(config, "results_per_page", 50))
    max_pages = max(1, int(getattr(config, "max_pages", 2)))
    max_days_old = int(getattr(config, "max_days_old", 30))
    delay = float(getattr(config, "delay_s", 1.0))
    timeout = float(getattr(config, "timeout_s", 20.0))

    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=timeout)
    try:
        seen: set[str] = set()
        out: list[dict[str, str]] = []
        first = True
        for role in roles:
            entry = stats.setdefault(role, {"collected": 0, "errors": 0}) if stats is not None else None
            for page in range(1, max_pages + 1):
                if not first and delay > 0:
                    sleep(delay)
                first = False
                payload = fetch_adzuna_page(
                    role,
                    page,
                    app_id=app_id,
                    app_key=app_key,
                    country=country,
                    results_per_page=per_page,
                    max_days_old=max_days_old,
                    client=client,
                )
                if not payload and entry is not None:
                    entry["errors"] += 1
                # Paginate on what the API returned, not on what survived the
                # category screen — a fully-filtered page is not the end of
                # the result set.
                raw_count = len(payload.get("results") or [])
                rows = parse_adzuna_results(
                    payload,
                    category_exclude=list(getattr(config, "category_exclude", []) or []),
                )
                if raw_count == 0:
                    break
                for row in rows:
                    if row["url"] in seen:
                        continue
                    seen.add(row["url"])
                    row["query"] = role
                    out.append(row)
                    if entry is not None:
                        entry["collected"] += 1
        return out
    finally:
        if owns_client:
            client.close()
