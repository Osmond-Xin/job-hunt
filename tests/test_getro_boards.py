"""Tier 9: the Getro-powered accelerator and VC portfolio boards.

These boards are where early-stage companies post, which is the lane the
operator asked for on 2026-09-17. They share one JSON API, so the tier is one
reader configured with a list of network ids.
"""

from __future__ import annotations

import json

import httpx
import pytest

from job_hunt.services.getro_boards import GETRO_SEARCH, scan_getro_boards_source


def _job(title: str, company: str, url: str, *, locations=("Toronto, ON, Canada",), created_at=1789600000):
    return {
        "title": title,
        "url": url,
        "created_at": created_at,
        "searchable_locations": list(locations),
        "locations": list(locations),
        "organization": {"name": company},
        "work_mode": "on_site",
    }


def _transport(pages: dict[tuple[int, str, int], dict], calls: list | None = None):
    """Answer the search API from a {(network_id, query, page): payload} map."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        network = int(str(request.url).rstrip("/").split("/")[-3])
        key = (network, body.get("query", ""), body.get("page", 0))
        if calls is not None:
            calls.append(key)
        payload = pages.get(key)
        if payload is None:
            return httpx.Response(200, json={"results": {"jobs": [], "count": 0}})
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def _config(**overrides):
    config = {
        "enabled": True,
        "delay_s": 0,
        "queries": ["ai engineer"],
        "locations": ["Canada"],
        "max_pages": 2,
        "boards": [{"id": "communitech", "network_id": 8936, "label": "Communitech", "enabled": True}],
    }
    config.update(overrides)
    return config


def _client(transport):
    return httpx.Client(transport=transport)


def test_a_disabled_tier_returns_nothing_and_stays_healthy():
    result = scan_getro_boards_source({"enabled": False, "boards": [{"id": "x", "network_id": 1}]})
    assert result.postings == []
    assert result.health.ok and result.health.errors == 0


def test_a_posting_carries_employer_location_and_posted_date():
    pages = {
        (8936, "ai engineer", 0): {
            "results": {
                "count": 1,
                "jobs": [_job("Machine Learning Developer", "Kindred", "https://boards.example/ml-1")],
            }
        }
    }
    result = scan_getro_boards_source(_config(), client=_client(_transport(pages)), sleep=lambda _s: None)

    assert len(result.postings) == 1
    posting = result.postings[0]
    assert posting.title == "Machine Learning Developer"
    assert posting.company == "Kindred"
    assert posting.location == "Toronto, ON, Canada"
    assert posting.url == "https://boards.example/ml-1"
    assert posting.portal == "getro"
    assert posting.source == "getro:communitech"
    assert posting.posted == "2026-09-16"  # created_at is a unix timestamp, read in UTC


def test_the_same_posting_found_by_two_queries_is_returned_once():
    listing = {"results": {"count": 1, "jobs": [_job("AI Engineer", "Axon", "https://boards.example/a")]}}
    pages = {(8936, "ai engineer", 0): listing, (8936, "machine learning", 0): listing}
    result = scan_getro_boards_source(
        _config(queries=["ai engineer", "machine learning"]),
        client=_client(_transport(pages)),
        sleep=lambda _s: None,
    )
    assert [p.url for p in result.postings] == ["https://boards.example/a"]


def test_paging_stops_at_the_page_budget_and_says_so():
    """The API returns 20 rows a page whatever hitsPerPage asks for, so a broad
    query is capped rather than paged to the end."""
    def page_of(page: int) -> dict:
        first = page * 20
        return {"results": {"count": 400, "jobs": [
            _job(f"AI Engineer {i}", "Co", f"https://b.example/{i}") for i in range(first, first + 20)]}}

    pages = {(8936, "ai engineer", page): page_of(page) for page in range(10)}
    calls: list = []
    result = scan_getro_boards_source(
        _config(max_pages=2), client=_client(_transport(pages, calls)), sleep=lambda _s: None
    )

    assert len({call[2] for call in calls}) == 2  # pages 0 and 1 only
    assert len(result.postings) == 40
    assert result.health.truncated
    assert any("raise max_pages" in w for w in result.health.warnings())


def test_paging_stops_early_when_the_board_runs_out():
    pages = {
        (8936, "ai engineer", 0): {
            "results": {"count": 1, "jobs": [_job("AI Engineer", "Co", "https://b.example/1")]}
        }
    }
    calls: list = []
    result = scan_getro_boards_source(
        _config(max_pages=5), client=_client(_transport(pages, calls)), sleep=lambda _s: None
    )
    assert len(calls) == 1
    assert not result.health.truncated


def test_a_board_that_errors_is_counted_not_silently_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    result = scan_getro_boards_source(
        _config(), client=_client(httpx.MockTransport(handler)), sleep=lambda _s: None
    )
    assert result.postings == []
    assert result.health.errors == 1
    assert any("failed request" in w for w in result.health.warnings())


def test_a_disabled_board_is_not_requested():
    calls: list = []
    config = _config(
        boards=[
            {"id": "communitech", "network_id": 8936, "enabled": False},
            {"id": "mars", "network_id": 383, "enabled": True},
        ]
    )
    scan_getro_boards_source(config, client=_client(_transport({}, calls)), sleep=lambda _s: None)
    assert {call[0] for call in calls} == {383}


def test_a_row_without_a_url_or_title_is_dropped_rather_than_half_recorded():
    pages = {
        (8936, "ai engineer", 0): {
            "results": {
                "count": 3,
                "jobs": [
                    _job("", "Co", "https://b.example/no-title"),
                    _job("AI Engineer", "Co", ""),
                    _job("AI Engineer", "Co", "https://b.example/ok"),
                ],
            }
        }
    }
    result = scan_getro_boards_source(_config(), client=_client(_transport(pages)), sleep=lambda _s: None)
    assert [p.url for p in result.postings] == ["https://b.example/ok"]


def test_the_request_is_the_documented_search_call():
    """The contract this tier depends on, recorded so a change is visible."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"results": {"jobs": [], "count": 0}})

    scan_getro_boards_source(_config(), client=_client(httpx.MockTransport(handler)), sleep=lambda _s: None)

    request = seen[0]
    assert str(request.url) == GETRO_SEARCH.format(network_id=8936)
    assert request.method == "POST"
    assert request.headers["accept"] == "application/json"
    body = json.loads(request.content)
    assert body["query"] == "ai engineer"
    assert body["filters"] == {"searchable_locations": ["Canada"]}
    assert body["page"] == 0


@pytest.mark.parametrize(
    "locations, expected",
    [
        (["Toronto, ON, Canada", "Ontario, Canada", "Canada"], "Toronto, ON, Canada"),
        (["Canada"], "Canada"),
        ([], ""),
    ],
)
def test_the_most_specific_location_is_the_one_kept(locations, expected):
    pages = {
        (8936, "ai engineer", 0): {
            "results": {"count": 1, "jobs": [_job("AI Engineer", "Co", "https://b.example/x", locations=locations)]}
        }
    }
    result = scan_getro_boards_source(_config(), client=_client(_transport(pages)), sleep=lambda _s: None)
    assert result.postings[0].location == expected


def test_the_tier_keeps_the_positive_title_filter_unlike_the_curated_boards():
    """A portfolio board is a general board: Communitech carried EY tax-litigation
    roles beside its ML ones on 2026-09-17. Tiers 4-8 waive the positive title
    filter because their source already establishes the occupation; this one
    must not, or the inbox fills with tax lawyers.
    """
    from job_hunt.services.scan import ScanResult, ScannedJob, _accept_jobs

    jobs = [
        ScannedJob(url="https://b.example/ml", title="Machine Learning Developer", company="Kindred",
                   location="Toronto, ON, Canada", portal="getro", source="getro:communitech"),
        ScannedJob(url="https://b.example/tax", title="Tax Law - Tax Litigation, Senior Associate",
                   company="EY", location="Toronto, ON, Canada", portal="getro", source="getro:communitech"),
    ]
    result = ScanResult()
    _accept_jobs(jobs, result, positives=["machine learning", "developer"], negatives=[],
                 include_non_canada=False, known_urls=set(), known_company_roles=set(),
                 apply=False, count_fetched=True)

    assert result.new_jobs == 1
    assert [job.url for job in result.jobs] == ["https://b.example/ml"]
