"""Tier 16: Himalayas remote roles open to Canada. Row trimmed from the live
response of 2026-09-27."""

from __future__ import annotations

import httpx

from job_hunt.services.himalayas import parse_job, scan_himalayas_source

JOB = {
    "title": "Data Analyst", "companyName": "lemon.io", "pubDate": 1790075996,
    "applicationLink": "https://himalayas.app/companies/lemon-io/jobs/data-analyst-4016266853",
    "guid": "https://himalayas.app/companies/lemon-io/jobs/data-analyst-4016266853",
    "locationRestrictions": ["Canada", "United States"],
}


def _job(i: int) -> dict:
    return {**JOB, "applicationLink": f"https://himalayas.app/j/{i}"}


def _scan(handler, **config):
    return scan_himalayas_source(
        {"enabled": True, "delay_s": 0, **config},
        ["Data Analyst"],
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _s: None,
    )


def test_a_row_is_remote_in_canada_with_its_posted_date():
    row = parse_job(JOB)
    assert row["location"] == "Remote, Canada" and row["company"] == "lemon.io"
    assert row["posted"] == "2026-09-22"


def test_a_role_search_is_paged_by_page_number_to_the_count():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        page = int(params["page"])
        ids = range((page - 1) * 20, min(page * 20, 25))
        return httpx.Response(200, json={"totalCount": 25, "jobs": [_job(i) for i in ids]})

    result = _scan(handler)
    assert seen[0] == {"country": "Canada", "q": "Data Analyst", "page": "1"} and len(seen) == 2
    assert len(result.postings) == 25 and result.health.ok
    assert not result.health.truncated and not result.health.incomplete


def test_the_budget_running_out_is_truncation():
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        return httpx.Response(200, json={"totalCount": 327, "jobs": [_job(page * 20 + i) for i in range(20)]})

    assert _scan(handler, max_pages=2).health.truncated


def test_a_short_read_is_incomplete_and_a_bad_response_is_an_error():
    short = _scan(lambda request: httpx.Response(200, json={"totalCount": 90, "jobs": [_job(1)]}))
    assert short.health.incomplete and any("Data Analyst 1 of 90" in w for w in short.health.warnings())
    assert _scan(lambda request: httpx.Response(200, json={"error": 1})).health.errors == 1
