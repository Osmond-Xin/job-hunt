"""Tier 15: Workable's cross-employer search. Row trimmed from the live
response of 2026-09-27."""

from __future__ import annotations

import httpx

from job_hunt.services.workable_search import parse_job, scan_workable_search_source

JOB = {
    "id": "4da1deb0", "title": "Data Analyst - EPESI",
    "url": "https://jobs.workable.com/view/aA1xpUGyUuwcVgAeakHzkQ/data-analyst",
    "created": "2026-06-22T15:17:41.607Z", "workplace": "on_site",
    "location": {"city": "Pickering", "subregion": "Ontario", "countryName": "Canada"},
    "company": {"title": "Durham Community Health Centre"},
}


def _job(i: int) -> dict:
    return {**JOB, "id": str(i), "url": f"https://jobs.workable.com/view/{i}"}


def _scan(handler, **config):
    return scan_workable_search_source(
        {"enabled": True, "delay_s": 0, **config},
        ["Data Analyst"],
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _s: None,
    )


def test_a_row_carries_employer_place_and_date():
    row = parse_job(JOB)
    assert row["company"] == "Durham Community Health Centre"
    assert row["location"] == "Pickering, Ontario, Canada"
    assert row["posted"] == "2026-06-22"
    assert parse_job({**JOB, "workplace": "remote"})["location"] == "Remote, Pickering, Ontario, Canada"


def test_pages_follow_the_next_page_token_to_the_end():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        if "pageToken" not in params:
            return httpx.Response(200, json={"totalSize": 25, "nextPageToken": "t2",
                                             "jobs": [_job(i) for i in range(20)]})
        return httpx.Response(200, json={"totalSize": 25, "jobs": [_job(i) for i in range(20, 25)]})

    result = _scan(handler)
    assert seen[0] == {"query": "Data Analyst", "location": "Canada"} and seen[1]["pageToken"] == "t2"
    assert len(result.postings) == 25 and result.health.ok
    assert not result.health.truncated and not result.health.incomplete


def test_the_budget_running_out_is_truncation():
    def handler(request: httpx.Request) -> httpx.Response:
        n = len(request.url.params.get("pageToken", ""))
        return httpx.Response(200, json={"totalSize": 500, "nextPageToken": "t" * (n + 1),
                                         "jobs": [_job(n * 20 + i) for i in range(20)]})

    result = _scan(handler, max_pages=2)
    assert len(result.postings) == 40 and result.health.truncated


def test_a_search_that_ends_short_of_its_total_is_incomplete():
    result = _scan(lambda request: httpx.Response(200, json={"totalSize": 30, "jobs": [_job(1)]}))
    assert result.health.incomplete and any("Data Analyst 1 of 30" in w for w in result.health.warnings())


def test_a_malformed_or_failed_response_is_an_error():
    assert _scan(lambda request: httpx.Response(200, json={"error": "x"})).health.errors == 1
    assert _scan(lambda request: httpx.Response(429)).health.errors == 1



# --- review of the new-sources batch, 2026-09-27 ------------------------------


def test_a_budget_that_read_everything_is_not_truncated():
    """M4."""
    def handler(request: httpx.Request) -> httpx.Response:
        n = len(request.url.params.get("pageToken", ""))
        return httpx.Response(200, json={"totalSize": 40, "nextPageToken": "t" * (n + 1),
                                         "jobs": [_job(n * 20 + i) for i in range(20)]})

    result = _scan(handler, max_pages=2)
    assert len(result.postings) == 40 and not result.health.truncated


def test_repeats_nulls_bad_links_and_odd_locations_are_counted_not_raised():
    """H5, M2, M3."""
    jobs = [_job(1), _job(1), None, {**_job(2), "url": 123}, {**_job(3), "url": "/view/3"},
            {**_job(4), "location": None, "locations": "Toronto"}]
    result = _scan(lambda request: httpx.Response(200, json={"totalSize": 4, "jobs": jobs}))
    assert sorted(p.url for p in result.postings) == [
        "https://jobs.workable.com/view/1", "https://jobs.workable.com/view/3", "https://jobs.workable.com/view/4"]
    assert any("2 row(s) could not be parsed" in w for w in result.health.warnings())



# --- review round 9, 2026-09-27 ------------------------------------------------


def test_rows_without_ids_still_show_progress_and_a_token_cycle_ends_the_search():
    """Round 9: no ids + no total + a next token stopped after one request."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("pageToken", "")
        calls.append(token)
        page = {"": 0, "t1": 1, "t2": 2}[token]
        jobs = [{**_job(page * 20 + i), "id": None} for i in range(20)]
        # t2 hands back t1: a cycle.
        return httpx.Response(200, json={"nextPageToken": {"": "t1", "t1": "t2", "t2": "t1"}[token], "jobs": jobs})

    result = _scan(handler, max_pages=10)
    assert calls == ["", "t1", "t2"] and len(result.postings) == 60 and result.health.ok


def test_an_unhashable_id_does_not_crash_the_search():
    jobs = [{**_job(1), "id": ["x"]}, {**_job(2), "id": {"v": 1}}]
    result = _scan(lambda request: httpx.Response(200, json={"totalSize": 2, "jobs": jobs}))
    assert len(result.postings) == 2 and result.health.ok



def test_a_page_of_only_malformed_rows_reports_loss_not_a_silent_zero():
    """Round 10: every row failing validation left rows empty, the loss was
    never added, and the source read as healthy with nothing collected."""
    result = _scan(lambda request: httpx.Response(200, json={"totalSize": 3, "jobs": [
        None, {**_job(1), "url": 123}, {**_job(2), "title": ""}]}))
    assert result.postings == []
    assert result.health.incomplete and any("3 row(s) could not be parsed" in w for w in result.health.warnings())
