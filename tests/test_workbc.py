"""Tier 14: WorkBC's job-search API. Rows trimmed from the live response of
2026-09-27: one employer-ATS posting and one Job Bank mirror."""

from __future__ import annotations

import json

import httpx

from job_hunt.services.row_guard import SKIP
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
    assert parse_job(JOB_BANK_MIRROR) is SKIP


def test_a_title_search_per_role_paged_to_the_count():
    """151 rows including one Job Bank mirror, at most 100 a page (L3)."""
    rows = [JOB_BANK_MIRROR] + [_job(i) for i in range(150)]
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        page = body["Page"]
        return httpx.Response(200, json={"count": 151, "result": rows[(page - 1) * 100: page * 100]})

    result = _scan(handler)
    assert str(API) and [b["Page"] for b in bodies] == [1, 2]
    assert bodies[0]["Keyword"] == "Data Analyst" and bodies[0]["SearchInField"] == "title"
    assert len(result.postings) == 150
    assert result.health.ok and not result.health.incomplete and not result.health.truncated


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


# --- review of the new-sources batch, 2026-09-27 ------------------------------


def test_a_short_last_page_is_incomplete_not_truncated():
    """L1."""
    result = _scan(lambda request: httpx.Response(200, json={"count": 300, "result": [_job(i) for i in range(40)]}),
                   max_pages=5)
    assert result.health.incomplete and not result.health.truncated


def test_a_repeated_page_is_not_counted_twice():
    """H5: repeats counted as coverage."""
    page = [_job(i) for i in range(100)]
    result = _scan(lambda request: httpx.Response(200, json={"count": 200, "result": page}), max_pages=5)
    assert len(result.postings) == 100
    assert any("Data Analyst 100 of 200" in w for w in result.health.warnings())


def test_null_rows_and_bad_links_are_loss_and_mirrors_are_not():
    """H5, M2, M3."""
    jobs = [JOB_BANK_MIRROR, None, {**ATS_ROW, "JobId": "x1", "ExternalSource": "not a dict"},
            {**ATS_ROW, "JobId": "x2", "ExternalSource": {"Source": [{"Url": "javascript:void(0)"}]}}, _job(1)]
    result = _scan(lambda request: httpx.Response(200, json={"count": 5, "result": jobs}))
    assert len(result.postings) == 1
    assert any("3 row(s) could not be parsed" in w for w in result.health.warnings())


def test_an_unknown_count_keeps_paging_to_a_short_page():
    """M1: a missing count ended the search after page 1."""
    requested: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = json.loads(request.content)["Page"]
        requested.append(page)
        return httpx.Response(200, json={"result": [_job(page * 1000 + i) for i in range(100 if page < 3 else 7)]})

    result = _scan(handler, max_pages=5)
    assert requested == [1, 2, 3] and len(result.postings) == 207 and result.health.ok


def test_a_malformed_or_failed_response_is_an_error():
    assert _scan(lambda request: httpx.Response(200, json={"message": "x"})).health.errors == 1
    assert _scan(lambda request: httpx.Response(503)).health.errors == 1


def test_enabled_without_role_terms_says_so():
    result = scan_workbc_source({"enabled": True}, [])
    assert not result.health.ok and "no role terms" in result.health.note



# --- review round 9, 2026-09-27 ------------------------------------------------


def test_a_known_count_is_read_past_a_short_middle_page():
    """Round 9: pages of 40, 100 and 10 for a count of 150 stopped after 40."""
    sizes = {1: 40, 2: 100, 3: 10}

    def handler(request: httpx.Request) -> httpx.Response:
        page = json.loads(request.content)["Page"]
        start = sum(sizes[p] for p in range(1, page))
        return httpx.Response(200, json={"count": 150, "result": [_job(start + i) for i in range(sizes.get(page, 0))]})

    result = _scan(handler, max_pages=5)
    assert len(result.postings) == 150 and not result.health.incomplete


def test_an_unhashable_job_id_does_not_crash_the_search():
    """Round 9: JobId={"value": 1} raised in ids.update()."""
    jobs = [{**_job(1), "JobId": {"value": 1}}, {**_job(2), "JobId": ["x"]}]
    result = _scan(lambda request: httpx.Response(200, json={"count": 2, "result": jobs}))
    assert len(result.postings) == 2 and result.health.ok and not result.health.incomplete
