"""Workday CxS adapter — the JSON API behind every ``*.myworkdayjobs.com`` site.

Workday is the dominant ATS among large Canadian employers (banks, crown
corporations, utilities), and it exposes a clean JSON search:

``POST https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs``
body ``{"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}``
returns ``{"total": N, "jobPostings": [{title, locationsText, externalPath}]}``.

⚠️ **Tenant and site ids cannot be guessed.** Probing 21 plausible Canadian
tenant names produced exactly one hit (BMO). The two that work in this repo —
OLG (``olg`` / ``Careers``) and TC Energy (``tcenergy`` / ``CAREER_SITE_TC``)
— were recovered from real posting URLs. ``resolve_workday_target`` exists for
that reason: give it any ``myworkdayjobs.com`` URL and it extracts the triple,
so new employers are added by pasting a URL rather than by guesswork.

A 422 from the endpoint means the tenant exists but the site id is wrong; a
404 means the tenant itself is wrong. Both are treated as "no data".
"""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx

_CXS = "https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_HOST_RE = re.compile(r"^(?P<tenant>[a-z0-9-]+)\.(?P<host>wd\d+)\.myworkdayjobs\.com$", re.I)
# Path is /<site>/job/... or /<locale>/<site>/job/... — the locale segment
# looks like "en-US" and must not be mistaken for the site id.
_LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")


def resolve_workday_target(url: str) -> tuple[str, str, str] | None:
    """Extract ``(tenant, host, site)`` from any myworkdayjobs.com URL.

    Returns ``None`` when the URL is not a Workday job board.
    """
    parsed = urlparse((url or "").strip())
    match = _HOST_RE.match(parsed.netloc or "")
    if not match:
        return None
    segments = [s for s in (parsed.path or "").split("/") if s]
    if segments and _LOCALE_RE.match(segments[0]):
        segments = segments[1:]
    if not segments:
        return None
    site = segments[0]
    if site in {"job", "jobs", "wday"}:
        return None
    return match.group("tenant"), match.group("host").lower(), site


def parse_workday_response(
    payload: dict[str, Any], tenant: str, host: str, site: str
) -> list[dict[str, str]]:
    """Map a CxS response onto flat rows with absolute posting URLs."""
    postings = payload.get("jobPostings") if isinstance(payload, dict) else None
    if not isinstance(postings, list):
        return []
    base = f"https://{tenant}.{host}.myworkdayjobs.com/en-US/{site}"
    rows: list[dict[str, str]] = []
    for item in postings:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        path = str(item.get("externalPath") or "").strip()
        if not title or not path:
            continue
        rows.append(
            {
                "url": base + path,
                "title": title,
                "location": str(item.get("locationsText") or "").strip(),
                "posted": str(item.get("postedOn") or "").strip(),
            }
        )
    return rows


def fetch_workday_page(
    tenant: str,
    host: str,
    site: str,
    *,
    offset: int,
    limit: int,
    client: httpx.Client,
) -> dict[str, Any]:
    """One page of postings. ``{}`` on transport failure, 404 or 422."""
    url = _CXS.format(tenant=tenant, host=host, site=site)
    body = {"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""}
    try:
        response = client.post(url, json=body)
    except httpx.HTTPError:
        return {}
    if response.status_code != 200:
        return {}
    try:
        return response.json()
    except ValueError:
        return {}


def scan_workday(
    config: dict[str, Any] | None,
    *,
    client: httpx.Client | None = None,
    sleep=time.sleep,
    stats: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Fetch every configured Workday employer.

    Each entry needs ``name`` plus either a ``url`` (any posting or board URL,
    from which the triple is resolved) or explicit ``tenant`` / ``site``.

    Pass ``stats`` to find out whether each employer's pages actually loaded.
    ``fetch_workday_page`` returns ``{}`` on a transport error, a 404 or a
    422 — the same value a real CxS response never produces (a 200 always
    carries ``total``/``jobPostings``), so ``{}`` is counted as a failed
    request here rather than silently read as "employer has no jobs".
    Keyed by employer name.
    """
    if not config or not config.get("enabled", False):
        return []
    employers = config.get("employers") or []
    if not employers:
        return []
    # Workday hard-caps the page size at 20: limit=21 returns HTTP 400, which
    # this adapter turns into an empty page and therefore into "this employer
    # has no jobs". Clamp instead of letting a config typo silently zero out
    # a whole board.
    limit = max(1, min(int(config.get("page_size", 20)), 20))
    max_pages = max(1, int(config.get("max_pages", 3)))
    delay = float(config.get("delay_s", 1.0))
    timeout = float(config.get("timeout_s", 30.0))

    owns_client = client is None
    if client is None:
        client = httpx.Client(
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                # Required. Some tenants (verified: irvingoil) reject httpx's
                # default python-httpx UA and the CxS call fails silently,
                # which reads as "employer has no postings". Others (olg,
                # bmo, tcenergy) do not care, so the gap went unnoticed.
                "User-Agent": _USER_AGENT,
            },
            follow_redirects=True,
        )
    try:
        out: list[dict[str, str]] = []
        seen: set[str] = set()
        first = True
        for employer in employers:
            if not isinstance(employer, dict) or not employer.get("enabled", True):
                continue
            target = None
            if employer.get("url"):
                target = resolve_workday_target(str(employer["url"]))
            elif employer.get("tenant") and employer.get("site"):
                target = (
                    str(employer["tenant"]),
                    str(employer.get("host", "wd3")),
                    str(employer["site"]),
                )
            if not target:
                continue
            tenant, host, site = target
            name = str(employer.get("name") or tenant)
            entry = stats.setdefault(name, {"collected": 0, "errors": 0}) if stats is not None else None
            for page in range(max_pages):
                if not first and delay > 0:
                    sleep(delay)
                first = False
                payload = fetch_workday_page(
                    tenant, host, site, offset=page * limit, limit=limit, client=client
                )
                if not payload and entry is not None:
                    entry["errors"] += 1
                rows = parse_workday_response(payload, tenant, host, site)
                if not rows:
                    break
                for row in rows:
                    if row["url"] in seen:
                        continue
                    seen.add(row["url"])
                    row["company"] = name
                    out.append(row)
                    if entry is not None:
                        entry["collected"] += 1
                if len(rows) < limit:
                    break
        return out
    finally:
        if owns_client:
            client.close()
