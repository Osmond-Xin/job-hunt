"""Tier 13: Y Combinator companies hiring in Canada, read from ycombinator.com.

Two public reads, no login:

1. **Which companies.** ycombinator.com/companies is backed by an Algolia
   index (``YCCompany_production``). The page embeds a search-only key for it
   in ``window.AlgoliaOpts = {"app": ..., "key": ...}``; the key is a secured
   key scoped to the public tag, and it is re-read from the page on every run
   rather than stored here, because YC can rotate it. The query is facet
   filters only — ``regions:Canada`` and ``isHiring:true`` — which returned
   36 companies on 2026-09-27.
2. **Their postings.** Each ``/companies/<slug>/jobs`` page is an Inertia
   app whose ``data-page`` attribute holds the props as HTML-escaped JSON,
   postings under ``props.jobPostings``: title, url, location, createdAt.

Two properties of those postings shape the filtering here:

- ``createdAt`` is relative ("5 days", "about 2 years") and YC leaves
  postings up for years — 2026-09-27 saw "almost 7 years". Postings older
  than ``max_age_days`` are dropped.
- A company tagged Canada still posts elsewhere: ``"Remote (US)"``,
  ``"IN / Remote (IN)"``. Locations are ``City, ST, CC`` places joined by
  " / ", with remote places inside ``Remote (...)``. A place is Canadian when
  its country code is ``CA`` or it is ``City, <province>``; a posting with
  only foreign places is dropped, and one with an uncoded place ("Toronto
  Office", "Montreal") is left to the scan's own Canada filter. The bare
  ``CA`` would not survive that filter, so a Canadian posting's location is
  rewritten to the Canadian place with the country spelled out.

This tier supersedes the ``ycombinator`` web-search discovery channel for
Canadian roles; that channel stays for what it reaches beyond this one.
YC companies post sales and design roles beside engineering, so scan.py keeps
the positive title filter on these rows.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
import time
from typing import Any, Callable
from urllib.parse import urljoin

import httpx

from job_hunt.models.posting import JobPosting, SourceHealth, SourceResult, from_row
from job_hunt.services.immigration import AMBIGUOUS_PROVINCE_CODES, UNAMBIGUOUS_PROVINCE_CODES
from job_hunt.services.posted_date import relative_age_to_iso

BASE = "https://www.ycombinator.com"
DIRECTORY = f"{BASE}/companies"
_ALGOLIA = "https://{app}-dsn.algolia.net/1/indexes/YCCompany_production/query"
_OPTS_RE = re.compile(r"window\.AlgoliaOpts\s*=\s*(\{.*?\})")
_PAGE_RE = re.compile(r'data-page="([^"]*)"')
_APP_ID_RE = re.compile(r"^[A-Za-z0-9]+$")
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


def algolia_opts(page_html: str) -> tuple[str, str] | None:
    """``(app id, search key)`` embedded in the directory page."""
    match = _OPTS_RE.search(page_html or "")
    if not match:
        return None
    try:
        opts = json.loads(match.group(1))
    except ValueError:
        return None
    app, key = str(opts.get("app") or ""), str(opts.get("key") or "")
    # The app id becomes a hostname; anything but an id is not used as one.
    return (app, key) if _APP_ID_RE.match(app) and key else None


def job_postings(page_html: str) -> list[dict[str, Any]] | None:
    """``props.jobPostings`` of a company jobs page; None when the page has none."""
    match = _PAGE_RE.search(page_html or "")
    if not match:
        return None
    try:
        props = json.loads(html.unescape(match.group(1))).get("props") or {}
    except ValueError:
        return None
    postings = props.get("jobPostings")
    return postings if isinstance(postings, list) else None


def canadian_location(location: str) -> str | None:
    """The Canadian place in a YC location, "" to defer, None when foreign.

    "" means "some place carries no country code — let the scan's Canada
    filter read the raw string"; the caller keeps the original location then.
    """
    places = [p.strip() for p in re.split(r"\s*/\s*|;|\(|\)", location or "") if p.strip()]
    uncoded = False
    for place in places:
        if place.lower() == "remote":
            continue
        parts = [part.strip() for part in place.split(",")]
        if len(parts) in (1, 3) and re.fullmatch(r"[A-Z]{2}", parts[-1]):
            if parts[-1] == "CA":
                return ", ".join(parts[:-1] + ["Canada"])
        elif len(parts) == 2 and re.fullmatch(r"[A-Z]{2}", parts[1]):
            # "City, XX": a province only when XX cannot be anything else.
            # "Waterloo, IA" is Iowa and "Amsterdam, NL" the Netherlands; a code
            # that is both ("Regina, SK") is left to the scan's gate, which
            # places Canadian capitals by name.
            if parts[1] in UNAMBIGUOUS_PROVINCE_CODES:
                return f"{place}, Canada"
            if parts[1] in AMBIGUOUS_PROVINCE_CODES:
                uncoded = True
        else:
            uncoded = True
    return "" if uncoded else None


def _age_days(created: str, today: dt.date) -> int | None:
    # "about a year" / "an hour": the shared parser wants a digit.
    iso = relative_age_to_iso(re.sub(r"\ban?\b", "1", created or ""), today)
    return (today - dt.date.fromisoformat(iso)).days if iso else None


def scan_yc_companies_source(
    config: dict[str, Any] | None,
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    today: dt.date | None = None,
) -> SourceResult:
    if not config or not config.get("enabled", False):
        return SourceResult(postings=[], health=SourceHealth(source_id="yc_companies", ok=True))
    max_age = int(config.get("max_age_days", 120))
    delay = float(config.get("delay_s", 1.0))
    today = today or dt.date.today()

    owns_client = client is None
    if client is None:
        client = httpx.Client(
            headers={"User-Agent": _USER_AGENT},
            timeout=float(config.get("timeout_s", 30.0)),
            follow_redirects=True,
        )

    def failed(note: str) -> SourceResult:
        return SourceResult(
            postings=[], health=SourceHealth(source_id="yc_companies", ok=False, errors=1, note=note)
        )

    postings: list[JobPosting] = []
    errors = 0
    try:
        try:
            directory = client.get(DIRECTORY)
            directory.raise_for_status()
        except httpx.HTTPError:
            return failed("company directory did not load")
        opts = algolia_opts(directory.text)
        if opts is None:
            return failed("no Algolia key on the company directory page")
        app, key = opts
        try:
            response = client.post(
                _ALGOLIA.format(app=app.lower()),
                headers={"X-Algolia-Application-Id": app, "X-Algolia-API-Key": key},
                json={
                    "query": "",
                    "hitsPerPage": 1000,
                    "facetFilters": [["regions:Canada"], ["isHiring:true"]],
                    "attributesToRetrieve": ["slug"],
                    "attributesToHighlight": [],
                },
            )
            response.raise_for_status()
            found = response.json()
        except (httpx.HTTPError, ValueError):
            return failed("company search failed")
        slugs = [str(hit.get("slug")) for hit in found.get("hits") or [] if hit.get("slug")]

        for slug in slugs:
            if delay > 0:
                sleep(delay)
            try:
                page = client.get(f"{DIRECTORY}/{slug}/jobs")
                page.raise_for_status()
            except httpx.HTTPError:
                errors += 1
                continue
            jobs = job_postings(page.text)
            if jobs is None:
                errors += 1
                continue
            for job in jobs:
                location = str(job.get("location") or "")
                place = canadian_location(location)
                if place is None:
                    continue
                age = _age_days(str(job.get("createdAt") or ""), today)
                if age is not None and age > max_age:
                    continue
                posting = from_row(
                    {
                        "url": urljoin(BASE, str(job.get("url"))) if job.get("url") else "",
                        "title": str(job.get("title") or "").strip(),
                        "company": str(job.get("companyName") or slug),
                        "location": place or location,
                        "posted": (today - dt.timedelta(days=age)).isoformat() if age is not None else "",
                        "source": f"yc {slug}",
                    },
                    source_id="yc_companies",
                    portal="yc_companies",
                )
                if posting is not None:
                    postings.append(posting)
    finally:
        if owns_client:
            client.close()

    return SourceResult(
        postings=postings,
        health=SourceHealth(
            source_id="yc_companies",
            ok=errors == 0,
            collected=len(postings),
            errors=errors,
            truncated=int(found.get("nbHits") or 0) > len(slugs),
        ),
    )
