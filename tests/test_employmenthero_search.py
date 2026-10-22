"""Tier 18: Employment Hero's cross-employer job search (Canada). Row trimmed
from the live response of 2026-10-21."""

from __future__ import annotations

import httpx

from job_hunt.services.employmenthero_search import JOBS_API, NONCE_URL, parse_job, scan_employmenthero_source

JOB = {
    "id": "6f1c2a7e-0000-4000-8000-000000000001", "title": "AI Analyst",
    "organisation_name": "Jazz Aviation LP", "vendor_location_name": "Halifax, Nova Scotia B3H 1S6",
    "country_code": "CA", "created_at": "2026-10-17T13:02:11Z", "remote": False,
    "friendly_id": "cb-ai-analyst-mkexw", "is_careerbeacon_demo_job": True,
}


def _job(i: int) -> dict:
    return {**JOB, "id": f"job-{i}"}


def _handler(search, nonces: list[str] | None = None):
    """Serve the nonce endpoint, then hand searches to ``search``."""
    issued = iter(nonces or ["n1"])

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(NONCE_URL):
            return httpx.Response(200, json={"nonce": next(issued)})
        assert str(request.url).startswith(JOBS_API)
        return search(request)

    return handler


def _scan(search, nonces=None, **config):
    return scan_employmenthero_source(
        {"enabled": True, "delay_s": 0, **config},
        ["AI Analyst"],
        client=httpx.Client(transport=httpx.MockTransport(_handler(search, nonces))),
        sleep=lambda _s: None,
    )


def _page(items, total_items, total_pages, current):
    return httpx.Response(200, json={"data": {
        "items": items, "total_items": total_items, "total_pages": total_pages, "current_page": current}})


def test_a_row_links_the_job_id_and_drops_the_postal_code():
    """The id is the one Amii's widget links (jobs.employmenthero.com/jobs/<id>),
    so the two tiers dedupe; the placeholder postal codes would hide the town."""
    row = parse_job(JOB)
    assert row["url"] == "https://jobs.employmenthero.com/jobs/6f1c2a7e-0000-4000-8000-000000000001"
    assert row["location"] == "Halifax, Nova Scotia" and row["company"] == "Jazz Aviation LP"
    assert row["posted"] == "2026-10-17"
    assert parse_job({**JOB, "remote": True})["location"] == "Remote, Halifax, Nova Scotia"
    assert parse_job({**JOB, "title": "Business Analyst &ndash; Pension"})["title"] == "Business Analyst \u2013 Pension"


def test_the_search_sends_the_nonce_and_pages_to_the_count():
    seen: list[httpx.Request] = []

    def search(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        page = int(request.url.params["page_index"])
        return _page([_job(page * 100 + i) for i in range(50 if page == 1 else 10)], 60, 2, page)

    result = _scan(search)
    assert dict(seen[0].url.params) == {
        "page_index": "1", "item_per_page": "50", "sort": "relevance",
        "date_posted": "30", "country_code": "CA", "query": "AI Analyst",
    }
    assert seen[0].headers["X-WP-Nonce"] == "n1" and len(seen) == 2
    assert len(result.postings) == 60 and result.health.ok
    assert not result.health.truncated and not result.health.incomplete


def test_the_ten_page_cap_is_truncation():
    """Measured 2026-10-21: "analyst" counts 676, total_pages stops at 10, and
    page 11 repeats page 10 — only 500 rows of any search are reachable."""
    def search(request: httpx.Request) -> httpx.Response:
        page = min(int(request.url.params["page_index"]), 10)
        return _page([_job(page * 100 + i) for i in range(50)], 676, 10, page)

    result = _scan(search, max_pages=12)
    assert len(result.postings) == 500 and result.health.truncated


def test_a_repeated_page_ends_the_search():
    def search(request: httpx.Request) -> httpx.Response:
        return _page([_job(i) for i in range(50)], 400, 8, 1)

    seen = []
    result = _scan(lambda r: (seen.append(r), search(r))[1])
    assert len(seen) == 2 and len(result.postings) == 50


def test_an_expired_nonce_is_fetched_again_once():
    calls = {"n": 0}

    def search(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if request.headers["X-WP-Nonce"] == "stale":
            return httpx.Response(401, json={"code": "rest_forbidden"})
        return _page([_job(1)], 1, 1, 1)

    result = _scan(search, nonces=["stale", "fresh"])
    assert calls["n"] == 2 and len(result.postings) == 1 and result.health.ok


def test_no_nonce_is_an_error_and_nothing_is_searched():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(NONCE_URL), "searched without a nonce"
        return httpx.Response(200, json={"nonce": ""})

    result = scan_employmenthero_source(
        {"enabled": True, "delay_s": 0}, ["AI Analyst"],
        client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _s: None,
    )
    assert result.postings == [] and not result.health.ok and result.health.errors == 1


def test_a_failed_page_is_retried_once():
    calls = {"n": 0}

    def search(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(502, text="Bad Gateway")
        return _page([_job(1)], 1, 1, 1)

    result = _scan(search)
    assert calls["n"] == 2 and len(result.postings) == 1 and result.health.ok


def test_a_bad_response_is_an_error_and_bad_rows_are_loss():
    calls = {"n": 0}

    def failing(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"code": "api_request_failed"})

    assert _scan(failing).health.errors == 1 and calls["n"] == 2
    result = _scan(lambda r: _page([_job(1), {**JOB, "id": None}, None], 3, 1, 1))
    assert len(result.postings) == 1
    assert any("2 row(s) could not be parsed" in w for w in result.health.warnings())


def test_a_row_found_by_two_terms_is_kept_once():
    def search(request: httpx.Request) -> httpx.Response:
        ids = range(0, 30) if request.url.params["query"] == "AI Analyst" else range(20, 40)
        return _page([_job(i) for i in ids], 30 if request.url.params["query"] == "AI Analyst" else 20, 1, 1)

    result = scan_employmenthero_source(
        {"enabled": True, "delay_s": 0, "queries": ["AI Analyst", "Data Analyst"]}, [],
        client=httpx.Client(transport=httpx.MockTransport(_handler(search))), sleep=lambda _s: None,
    )
    assert len(result.postings) == 40 and result.health.ok and not result.health.incomplete


def test_a_disabled_tier_reads_nothing():
    result = scan_employmenthero_source({"enabled": False}, ["AI Analyst"])
    assert result.postings == [] and result.health.ok
