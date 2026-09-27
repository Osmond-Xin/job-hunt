"""Tests for the direct Job Bank search adapter (tier 4).

The fixture below is trimmed from a real jobbank.gc.ca results page, keeping
the markup quirks that broke the first implementation: a screen-reader
``wb-inv`` label inside the location cell, a relocation tooltip, a nested
recruiter badge inside the title span, and a ``;jsessionid=`` in the href.
"""

from __future__ import annotations

import httpx
import pytest

from job_hunt.services.jobbank import (
    parse_jobbank_results,
    scan_jobbank,
)

RESULTS_HTML = """
<div class="results-jobs">
<article id="article-50025107" class="action-buttons">
<a href="/jobsearch/jobposting/50025107;jsessionid=580B25FA1686BB01?source=searchresults" class="resultJobItem">
  <h3 class="title">
    <span class="flag"><span class="new">New</span><span class="telework">Hybrid</span></span>
    <span class="noctitle"> software developer </span>
  </h3>
  <ul class="list-unstyled">
    <li class="date">August 06, 2026</li>
    <li class="business">Marine Thinking</li>
    <li class="location"><span class="fas fa-map-marker-alt" aria-hidden="true"></span>
      <span class="wb-inv">Location</span>
      Halifax (NS)
    </li>
    <li class="salary"><span class="fa fa-dollar" aria-hidden="true"></span>
      Salary
      $42.00 hourly</li>
  </ul></a>
</article>
<article id="article-50020173">
<a href="/jobsearch/jobposting/50020173" class="resultJobItem">
  <span class="noctitle">Information systems technician</span>
  <ul class="list-unstyled">
    <li class="date">June 25, 2026</li>
    <li class="business">Canadian Armed Forces</li>
    <li class="location"><span class="wb-inv">Location</span>
      Undetermined location <span class="description"><span class="fa fa-info-circle"></span>
      The work location for this job may vary.</span></span>
    </li>
    <li class="salary">Salary $4,337.00 monthly</li>
  </ul></a>
</article>
</div>
"""


def test_parses_rows_with_all_fields() -> None:
    rows = parse_jobbank_results(RESULTS_HTML)

    assert len(rows) == 2
    first = rows[0]
    assert first["title"] == "software developer"
    assert first["company"] == "Marine Thinking"
    assert first["location"] == "Halifax (NS)"
    assert first["salary"] == "$42.00 hourly"
    assert first["date"] == "August 06, 2026"


def test_strips_jsessionid_and_tracking_query() -> None:
    rows = parse_jobbank_results(RESULTS_HTML)
    assert rows[0]["url"] == "https://www.jobbank.gc.ca/jobsearch/jobposting/50025107"
    assert rows[1]["url"] == "https://www.jobbank.gc.ca/jobsearch/jobposting/50020173"


def test_collapses_undetermined_location_tooltip() -> None:
    rows = parse_jobbank_results(RESULTS_HTML)
    assert rows[1]["location"] == "Undetermined location"


def test_parse_tolerates_empty_and_garbage() -> None:
    assert parse_jobbank_results("") == []
    assert parse_jobbank_results("<html><body>no articles</body></html>") == []


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_scan_queries_every_noc_province_pair_and_dedups() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(200, text=RESULTS_HTML, request=request)

    rows = scan_jobbank(
        {"enabled": True, "noc_codes": ["21232", "21221"], "provinces": ["NS", "NT"]},
        client=_client(handler),
        sleep=lambda s: None,
    )

    assert [(p["fn21"], p["fprov"]) for p in seen] == [
        ("21232", "NS"),
        ("21232", "NT"),
        ("21221", "NS"),
        ("21221", "NT"),
    ]
    assert all(p["sort"] == "D" for p in seen)
    # Same two postings on every page — deduplicated by URL.
    assert len(rows) == 2
    assert rows[0]["noc"] == "21232"


def test_scan_without_provinces_runs_one_national_query_per_code() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(200, text="", request=request)

    scan_jobbank(
        {"enabled": True, "noc_codes": ["21232"]},
        client=_client(handler),
        sleep=lambda s: None,
    )

    assert len(seen) == 1
    assert "fprov" not in seen[0]


@pytest.mark.parametrize(
    "config",
    [None, {}, {"enabled": False, "noc_codes": ["21232"]}, {"enabled": True}],
)
def test_scan_disabled_or_unconfigured_yields_nothing(config) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not hit the network")

    assert scan_jobbank(config, client=_client(handler), sleep=lambda s: None) == []


def test_scan_survives_transport_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    rows = scan_jobbank(
        {"enabled": True, "noc_codes": ["21232"], "provinces": ["NS"]},
        client=_client(handler),
        sleep=lambda s: None,
    )
    assert rows == []


def test_scan_paces_requests_between_pages() -> None:
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="", request=request)

    scan_jobbank(
        {"enabled": True, "noc_codes": ["1", "2", "3"], "delay_s": 2.5},
        client=_client(handler),
        sleep=slept.append,
    )

    # No pause before the first request; one between each subsequent pair.
    assert slept == [2.5, 2.5]


# --- pagination -------------------------------------------------------------
# Only page 1 was ever read: Ontario / 21222 advertised 353 results on
# 2026-09-27 and the sweep returned 25 of them.


def _page(ids, total: int | None = None) -> str:
    count = f'<h2><span class="found" id="results-count">{total}</span> results</h2>' if total else ""
    articles = "".join(
        f'<article id="article-{i}"><a href="/jobsearch/jobposting/{i}">'
        f'<span class="noctitle">developer {i}</span></a></article>'
        for i in ids
    )
    return count + articles


def _paged_handler(pages: dict[int, str], seen: list[dict[str, str]]):
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        return httpx.Response(200, text=pages.get(int(params.get("page", 1)), ""), request=request)

    return handler


def test_scan_walks_later_pages_until_the_advertised_count() -> None:
    pages = {1: _page(range(0, 25), total=40), 2: _page(range(25, 40))}
    seen: list[dict[str, str]] = []
    stats: dict = {}
    rows = scan_jobbank(
        {"enabled": True, "noc_codes": ["21222"], "provinces": ["ON"]},
        client=_client(_paged_handler(pages, seen)),
        sleep=lambda s: None,
        stats=stats,
    )

    assert len(rows) == 40
    assert "page" not in seen[0] and seen[1]["page"] == "2"
    assert len(seen) == 2  # a short page is the last one
    assert stats["21222"] == {
        "collected": 40, "errors": 0, "advertised": 40, "truncated": False, "incomplete": 0}


def test_an_unparseable_article_does_not_make_a_page_look_like_the_last() -> None:
    """Review 2026-09-27: the short-page test counted parsed rows, so one
    article in unexpected markup ended the walk with nothing reported."""
    broken = '<article id="article-999"><p>redesigned</p></article>'
    pages = {1: _page(range(0, 24), total=49) + broken, 2: _page(range(24, 48))}
    seen: list[dict[str, str]] = []
    stats: dict = {}
    rows = scan_jobbank(
        {"enabled": True, "noc_codes": ["21222"], "provinces": ["ON"]},
        client=_client(_paged_handler(pages, seen)),
        sleep=lambda s: None,
        stats=stats,
    )
    assert len(seen) == 2  # page 1 still had 25 articles, so page 2 was read
    assert len(rows) == 48
    # One posting never parsed: the pair fell short of its own count.
    assert stats["21222"]["incomplete"] == 1


def test_a_premature_empty_page_is_reported_as_incomplete() -> None:
    from job_hunt.services.scan import _board_coverage_warnings

    pages = {1: _page(range(0, 25), total=60)}  # page 2 comes back empty
    stats: dict = {}
    scan_jobbank(
        {"enabled": True, "noc_codes": ["21222"], "provinces": ["ON"]},
        client=_client(_paged_handler(pages, [])),
        sleep=lambda s: None,
        stats=stats,
    )
    assert stats["21222"]["incomplete"] == 1 and stats["21222"]["truncated"] is False
    assert "ended short of the board's own count" in "\n".join(_board_coverage_warnings(stats))


def test_a_page_that_repeats_an_earlier_one_ends_the_walk() -> None:
    same = _page(range(0, 25))
    seen: list[dict[str, str]] = []
    stats: dict = {}
    rows = scan_jobbank(
        {"enabled": True, "noc_codes": ["21222"], "provinces": ["ON"], "max_pages": 5},
        client=_client(_paged_handler({1: same, 2: same, 3: same}, seen)),
        sleep=lambda s: None,
        stats=stats,
    )
    assert len(rows) == 25
    assert len(seen) == 2
    assert stats["21222"]["truncated"] is False


def test_the_page_budget_running_out_is_recorded_and_warned() -> None:
    from job_hunt.services.scan import _board_coverage_warnings

    pages = {n: _page(range((n - 1) * 25, n * 25), total=353) for n in range(1, 16)}
    seen: list[dict[str, str]] = []
    stats: dict = {}
    rows = scan_jobbank(
        {"enabled": True, "noc_codes": ["21222"], "provinces": ["ON"], "max_pages": 3},
        client=_client(_paged_handler(pages, seen)),
        sleep=lambda s: None,
        stats=stats,
    )

    assert len(rows) == 75 and len(seen) == 3
    assert stats["21222"]["truncated"] is True
    warning = "\n".join(_board_coverage_warnings(stats))
    assert "21222" in warning and "raise max_pages" in warning and "advertises 353" in warning


def test_tier4_rows_become_scanned_jobs() -> None:
    from job_hunt.services.scan import _jobbank_scanned_jobs

    jobs = _jobbank_scanned_jobs(
        {"enabled": False}
    )  # disabled -> no network, no jobs
    assert jobs == []
