"""Tier 11: single-employer career sites on Oracle, Jibe and UKG.

Fixtures are trimmed from the live responses of 2026-09-27: J.D. Irving
(Oracle), City of Thunder Bay (Jibe) and Yukon Hospitals (UKG).
"""

from __future__ import annotations

import json

import httpx

from job_hunt.services.employer_boards import scan_employer_boards_source

JDI_URL = "https://hcpd.fa.ca2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_5003/requisitions"
THUNDER_BAY_URL = "https://careers.thunderbay.ca/careers-home/jobs"
YUKON_URL = (
    "https://recruiting.ultipro.ca/YUK5001YUHC/JobBoard/"
    "7ed94cc1-3925-495d-a225-c5d6b7c3ad3e/?q=&o=postedDateDesc"
)

JIBE_PAYLOAD = {
    "totalCount": 1,
    "count": 1,
    "jobs": [{"data": {
        "slug": "7172", "req_id": "7172",
        "title": "Transit Operator (Specialized & Conventional) (Part-Time)",
        "city": "Thunder Bay", "state": "Ontario", "country": "Canada",
        "posted_date": "2026-09-25T04:01:00+0000",
        "hiring_organization": "City of Thunder Bay",
    }}],
}

UKG_PAYLOAD = {
    "totalCount": 1,
    "opportunities": [{
        "Id": "8f681c67-d7a8-4265-855a-1a7c17514613",
        "Title": "Laboratory Assistant - Laboratory Services - Regular Part-Time",
        "Locations": [{"LocalizedName": "Whitehorse General Hospital", "Address": {
            "City": "Whitehorse", "State": {"Code": "YT", "Name": "Yukon"},
            "Country": {"Code": "CAN", "Name": "Canada"}}}],
        "PostedDate": "2026-09-25T16:50:23.649Z",
    }],
}


def _oracle_page(ids, total):
    return {"items": [{"TotalJobsCount": total, "requisitionList": [
        {"Id": str(i), "Title": f"Maintenance Planner {i}", "PrimaryLocation": "Saint John, NB, Canada",
         "PostedDate": "2026-09-25", "PostingEndDate": None} for i in ids]}]}


def _config(*employers, **overrides):
    config = {"enabled": True, "delay_s": 0, "max_pages": 5, "employers": list(employers)}
    config.update(overrides)
    return config


def _scan(config, handler):
    return scan_employer_boards_source(
        config, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _s: None
    )


def test_a_disabled_tier_returns_nothing_and_stays_healthy():
    result = scan_employer_boards_source({"enabled": False})
    assert result.postings == [] and result.health.ok


def test_an_oracle_employer_is_read_to_its_advertised_total():
    offsets: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        finder = request.url.params["finder"]
        offset = int(finder.split("offset=")[1].split(",")[0])
        offsets.append(offset)
        assert "siteNumber=CX_5003," in finder and "limit=200," in finder
        ids = range(offset, min(offset + 200, 250))
        return httpx.Response(200, json=_oracle_page(ids, 250))

    result = _scan(_config({"name": "J.D. Irving", "platform": "oracle", "url": JDI_URL}), handler)

    assert offsets == [0, 200]
    assert len(result.postings) == 250
    posting = result.postings[0]
    assert posting.company == "J.D. Irving"
    assert posting.url == "https://hcpd.fa.ca2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_5003/job/0"
    assert posting.location == "Saint John, NB, Canada" and posting.posted == "2026-09-25"
    assert posting.portal == "oracle"
    assert result.health.advertised == 250 and not result.health.truncated


def test_a_jibe_site_is_read_through_its_api():
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json=JIBE_PAYLOAD)

    result = _scan(_config({"name": "City of Thunder Bay", "platform": "jibe", "url": THUNDER_BAY_URL}), handler)

    assert seen[0].path == "/api/jobs" and seen[0].params["page"] == "1" and seen[0].params["limit"] == "100"
    assert len(seen) == 1  # totalCount reached on the first page
    posting = result.postings[0]
    assert posting.url == "https://careers.thunderbay.ca/jobs/7172"
    assert posting.location == "Thunder Bay, Ontario, Canada"
    assert posting.posted == "2026-09-25"
    assert posting.company == "City of Thunder Bay"


def test_a_ukg_board_is_read_through_its_search_post():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=UKG_PAYLOAD)

    result = _scan(_config({"name": "Yukon Hospitals", "platform": "ukg", "url": YUKON_URL}), handler)

    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == (
        "https://recruiting.ultipro.ca/YUK5001YUHC/JobBoard/"
        "7ed94cc1-3925-495d-a225-c5d6b7c3ad3e/JobBoardView/LoadSearchResults"
    )
    assert json.loads(request.content)["opportunitySearch"]["Skip"] == 0
    posting = result.postings[0]
    assert posting.url.endswith("/OpportunityDetail?opportunityId=8f681c67-d7a8-4265-855a-1a7c17514613")
    assert posting.location == "Whitehorse, Yukon, Canada"
    assert posting.posted == "2026-09-25"


def test_the_page_budget_running_out_is_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["finder"].split("offset=")[1].split(",")[0])
        return httpx.Response(200, json=_oracle_page(range(offset, offset + 200), 736))

    result = _scan(
        _config({"name": "J.D. Irving", "platform": "oracle", "url": JDI_URL}, max_pages=2), handler
    )
    assert len(result.postings) == 400
    assert result.health.truncated
    assert any("raise max_pages" in w and "736" in w for w in result.health.warnings())


def test_an_unreadable_entry_or_a_failed_request_is_an_error_not_an_empty_board():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    result = _scan(
        _config(
            {"name": "Typo", "platform": "workday", "url": JDI_URL},
            {"name": "No site", "platform": "oracle", "url": "https://example.com/careers"},
            {"name": "Down", "platform": "jibe", "url": THUNDER_BAY_URL},
        ),
        handler,
    )
    assert result.postings == []
    assert result.health.errors == 3
    assert any("failed request" in w for w in result.health.warnings())


def test_the_tier_keeps_the_positive_title_filter():
    """J.D. Irving's board is drivers, mechanics and store managers; only the
    title says which of its 736 postings is his work."""
    from job_hunt.services.scan import ScanResult, ScannedJob, _accept_jobs

    jobs = [
        ScannedJob(url="https://x/1", title="Business Systems Analyst", company="J.D. Irving",
                   location="Saint John, NB, Canada", portal="oracle", source="employer_boards"),
        ScannedJob(url="https://x/2", title="Class 5 P&D Courier Driver", company="J.D. Irving",
                   location="Sydney, NS, Canada", portal="oracle", source="employer_boards"),
    ]
    result = ScanResult()
    _accept_jobs(jobs, result, positives=["analyst"], negatives=[], include_non_canada=False,
                 known_urls=set(), known_company_roles=set(), apply=False, count_fetched=True)
    assert [job.url for job in result.jobs] == ["https://x/1"]
