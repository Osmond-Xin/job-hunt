"""Tier 10: Hacker News "Ask HN: Who is hiring?" — the monthly founders' thread.

Why this exists: on 2026-09-21 the operator asked for more companies he can
apply to, and his record says where replies come from — both real interviews to
date came from small companies, none from the large-employer referrals. The
monthly "Who is hiring?" thread is where small companies post themselves: ~400
top-level comments a month, written by a founder or an engineer, most of them
naming a person to email. No ATS sits between the posting and its author.

The thread is read through Algolia's public HN Search API, which exists for
exactly this (https://hn.algolia.com/api). Two calls: find the newest thread by
the ``whoishiring`` account, then search its comments for Canadian places.

A comment is free text, but the convention is strict enough to parse — the
first line is a header of ``|``-separated fields::

    Acme (YC W24) | Forward Deployed Engineer | Toronto or Remote (Canada) | $120-160k

Company is the first field. The role is whichever later field names an
occupation; when none does, the header itself is kept, because the scan's title
filter runs next and a header with no recognisable role should fail it. The row
links to the comment, not to an ATS: the comment *is* the posting.
"""

from __future__ import annotations

import html
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from job_hunt.models.posting import JobPosting, SourceHealth, SourceResult, from_row

ALGOLIA = "https://hn.algolia.com/api/v1"
ITEM_URL = "https://news.ycombinator.com/item?id={id}"
_ROLE_RE = re.compile(
    r"engineer|developer|analyst|scientist|architect|consultant|specialist|designer|manager|"
    r"devops|sre\b|full[- ]?stack|backend|frontend|founding|forward deployed|support|intern",
    re.I,
)
_TAG_RE = re.compile(r"<[^>]+>")


def latest_thread(client: httpx.Client) -> tuple[int, str] | None:
    """(story id, title) of the newest "Who is hiring?" thread."""
    response = client.get(
        f"{ALGOLIA}/search_by_date",
        params={"tags": "story,author_whoishiring", "query": "Who is hiring", "hitsPerPage": 5},
    )
    response.raise_for_status()
    for hit in response.json().get("hits", []):
        if "who is hiring" in (hit.get("title") or "").lower():
            return int(hit["objectID"]), hit["title"]
    return None


def parse_comment(hit: dict[str, Any]) -> dict[str, str] | None:
    """A pipeline row from one top-level comment, or None if it has no header."""
    text = html.unescape(_TAG_RE.sub("\n", hit.get("comment_text") or "")).strip()
    header = next((line.strip() for line in text.splitlines() if line.strip()), "")
    fields = [part.strip() for part in header.split("|") if part.strip()]
    if len(fields) < 2 or len(header) > 400:
        return None
    roles = [part for part in fields[1:] if _ROLE_RE.search(part)]
    created = hit.get("created_at_i")
    return {
        "url": ITEM_URL.format(id=hit["objectID"]),
        "company": fields[0][:80],
        "title": (" / ".join(roles) if roles else " | ".join(fields[1:]))[:140],
        # The whole header: the Canada filter reads it, and "Toronto or Remote"
        # sits in whichever field the author chose.
        "location": " | ".join(fields[1:])[:160],
        "posted": datetime.fromtimestamp(created, timezone.utc).strftime("%Y-%m-%d") if created else "",
        "source": "hn_who_is_hiring",
    }


def scan_hn_hiring_source(
    config: dict[str, Any] | None,
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SourceResult:
    if not config or not config.get("enabled", False):
        return SourceResult(postings=[], health=SourceHealth(source_id="hn_hiring", ok=True))
    places = [str(place) for place in (config.get("places") or ["Canada"])]
    delay = float(config.get("delay_s", 0.5))
    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=float(config.get("timeout_s", 30.0)), follow_redirects=True)

    postings: list[JobPosting] = []
    errors = 0
    note = ""
    try:
        thread = latest_thread(client)
        if thread is None:
            return SourceResult(
                postings=[],
                health=SourceHealth(source_id="hn_hiring", ok=False, errors=1, note="no Who-is-hiring thread found"),
            )
        story_id, note = thread
        seen: set[str] = set()
        for index, place in enumerate(places):
            if index and delay > 0:
                sleep(delay)
            try:
                response = client.get(
                    f"{ALGOLIA}/search",
                    params={"tags": f"comment,story_{story_id}", "query": place, "hitsPerPage": 100},
                )
                response.raise_for_status()
            except httpx.HTTPError:
                errors += 1
                continue
            for hit in response.json().get("hits", []):
                if hit.get("parent_id") != story_id or hit["objectID"] in seen:
                    continue  # a reply to a posting is not a posting
                seen.add(hit["objectID"])
                row = parse_comment(hit)
                posting = from_row(row, source_id="hn_hiring", portal="hn_who_is_hiring") if row else None
                if posting is not None:
                    postings.append(posting)
    finally:
        if owns_client:
            client.close()
    return SourceResult(
        postings=postings,
        health=SourceHealth(source_id="hn_hiring", ok=errors == 0, errors=errors, note=note),
    )
