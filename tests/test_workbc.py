"""Tier 14: WorkBC's job-search API. Rows trimmed from the live response of
2026-09-27: one employer-ATS posting and one Job Bank mirror."""

from __future__ import annotations

import json

import httpx

from job_hunt.services.workbc import API, parse_job, scan_workbc_source

ATS_ROW = {
    "JobId": "cmuhpjhyt1jei1s0485b1rwuf", "Title": "Marketing Data Analyst",
    "DatePosted": "2026-09-26T09:26:21", "EmployerName": "Martell Ventures", "City": "Kelowna",
    "IsFederalJob": False,
    "ExternalSource": {"Source": [{"Url": "https://job-boards.greenhouse.io/martell/jobs/5435680008",
                                   "Source": "job-boards.greenhouse.io"}]},
}
JOB_BANK_MIRROR = {
    "JobId": "50376038", "Title": "full stack Web developer", "DatePosted": "2026-09-27T04:19:00+00:00",
    "EmployerName": "Cela", "City": "Vancouver", "IsFederalJob": True,
}


def _job(i: int) -> dict:
    return {**ATS_ROW, "JobId": str(i), "ExternalSource": {"Source": [{"Url": f"https://ats.example/{i}"}]}}


def _scan(handler, **config):
    return scan_workbc_source(
        {"enabled": True, "delay_s": 0, **config},
        ["Data Analyst"],
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _s: None,
    )


def test_a_row_records_the_employers_own_posting_url():
    row = parse_job(ATS_ROW)
    assert row["url"] == "https://job-boards.greenhouse.io/martell/jobs/5435680008"
    assert row["company"] == "Martell Ventures"
    assert row["location"] == "Kelowna, BC"
    assert row["posted"] == "2026-09-26"


def test_job_bank_mirrors_are_skipped():
    assert parse_job(JOB_BANK_MIRROR) is None


def test_a_title_search_per_role_paged_to_the_count():
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        page = body["Page"]
        jobs = [_job(i) for i in range((page - 1) * 100, min(page * 100, 150))]
        return httpx.Response(200, json={"count": 150, "result": jobs + ([JOB_BANK_MIRROR] if page == 1 else [])})

    result = _scan(handler)
    assert str(API) and [b["Page"] for b in bodies] == [1, 2]
    assert bodies[0]["Keyword"] == "Data Analyst" and bodies[0]["SearchInField"] == "title"
    assert len(result.postings) == 150 and result.health.ok and not result.health.incomplete


def test_the_budget_running_out_is_truncation():
    def handler(request: httpx.Request) -> httpx.Response:
        page = json.loads(request.content)["Page"]
        return httpx.Response(200, json={"count": 500, "result": [_job(page * 100 + i) for i in range(100)]})

    result = _scan(handler, max_pages=2)
    assert len(result.postings) == 200 and result.health.truncated


def test_a_short_read_is_incomplete_and_named():
    result = _scan(lambda request: httpx.Response(200, json={"count": 150, "result": []}))
    assert result.health.incomplete
    assert any("Data Analyst 0 of 150" in w for w in result.health.warnings())


def test_a_malformed_or_failed_response_is_an_error():
    assert _scan(lambda request: httpx.Response(200, json={"message": "x"})).health.errors == 1
    assert _scan(lambda request: httpx.Response(503)).health.errors == 1


def test_enabled_without_role_terms_says_so():
    result = scan_workbc_source({"enabled": True}, [])
    assert not result.health.ok and "no role terms" in result.health.note
