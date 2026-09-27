"""Tier 13: YC companies hiring in Canada.

The directory snippet and jobs-page props are trimmed from ycombinator.com as
served on 2026-09-27 (the key below is a made-up stand-in for the real one).
"""

from __future__ import annotations

import datetime as dt
import html
import json

import httpx
import pytest

from job_hunt.services.yc_companies import (
    algolia_opts,
    canadian_location,
    job_postings,
    scan_yc_companies_source,
)

TODAY = dt.date(2026, 9, 27)
DIRECTORY_HTML = (
    '<script>window.AlgoliaOpts = {"app":"45BWZJ1SGC","key":"c2VhcmNoLW9ubHk="};</script>'
)


def _jobs_page(*postings) -> str:
    props = {"component": "WaasShowJobsPage", "props": {"company": {"slug": "bree"}, "jobPostings": list(postings)}}
    return f'<div id="app" data-page="{html.escape(json.dumps(props))}"></div>'


def _job(title, location, created, url="/companies/bree/jobs/x"):
    return {"title": title, "url": url, "location": location, "createdAt": created, "companyName": "Bree"}


BREE = _jobs_page(
    _job("Software Engineer, Product (Co-op)",
         "Toronto, ON, CA / Toronto, Ontario, CA / Remote (CA)", "23 days", "/companies/bree/jobs/a"),
    _job("Machine Learning Engineer, Underwriting", "Remote (US)", "5 months", "/companies/bree/jobs/b"),
    _job("Software Engineer, Mobile", "Toronto, ON, CA / Remote (CA)", "almost 2 years", "/companies/bree/jobs/c"),
    _job("Product Builder", "US / CA / Remote (US; CA)", "about a month", "/companies/bree/jobs/d"),
)


def test_the_search_key_is_read_off_the_directory_page():
    assert algolia_opts(DIRECTORY_HTML) == ("45BWZJ1SGC", "c2VhcmNoLW9ubHk=")
    assert algolia_opts("<html>redesigned</html>") is None


def test_postings_are_read_from_the_escaped_page_props():
    assert [job["title"] for job in job_postings(BREE)][:2] == [
        "Software Engineer, Product (Co-op)", "Machine Learning Engineer, Underwriting"]
    assert job_postings("<html></html>") is None


@pytest.mark.parametrize(
    "location, expected",
    [
        ("Toronto, ON, CA / Toronto, Ontario, CA / Remote (CA)", "Toronto, ON, Canada"),
        ("New York, NY, US / San Francisco, CA, US / Remote (Toronto, ON, CA)", "Toronto, ON, Canada"),
        ("US / CA / Remote (US; CA)", "Canada"),
        ("Calgary, AB / Cape Town, WC, ZA", "Calgary, AB, Canada"),
        ("Remote (US)", None),
        ("IN / Remote (IN)", None),
        ("UK / Remote (US)", None),
        # No country code anywhere: the scan's own Canada filter decides.
        ("Toronto and Open to across Canada / Remote / Remote (US)", ""),
        ("Main Office - Toronto", ""),
        # Review 2026-09-27: a two-letter code that is a US state or a foreign
        # country is foreign, not uncoded and not a province.
        ("Waterloo, IA", None),
        ("Amsterdam, NL", ""),
        ("Bratislava, SK", ""),
        ("Austin, TX", None),
    ],
)
def test_a_location_is_placed_by_its_country_codes(location, expected):
    assert canadian_location(location) == expected


def _handler(calls: list[httpx.Request], jobs_html: str = BREE):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host.endswith("algolia.net"):
            return httpx.Response(200, json={"hits": [{"slug": "bree"}], "nbHits": 1})
        if request.url.path == "/companies":
            return httpx.Response(200, text=DIRECTORY_HTML)
        return httpx.Response(200, text=jobs_html)

    return handler


def _scan(handler, **config):
    return scan_yc_companies_source(
        {"enabled": True, "delay_s": 0, "max_age_days": 120, **config},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _s: None,
        today=TODAY,
    )


def test_only_recent_canadian_postings_are_kept():
    calls: list[httpx.Request] = []
    result = _scan(_handler(calls))

    assert [(p.title, p.location, p.posted) for p in result.postings] == [
        ("Software Engineer, Product (Co-op)", "Toronto, ON, Canada", "2026-09-04"),
        ("Product Builder", "Canada", "2026-08-28"),
    ]
    posting = result.postings[0]
    assert posting.url == "https://www.ycombinator.com/companies/bree/jobs/a"
    assert posting.company == "Bree" and posting.portal == "yc_companies"
    assert result.health.ok


def test_the_company_search_uses_the_scraped_key_and_canada_facets():
    calls: list[httpx.Request] = []
    _scan(_handler(calls))

    search = next(r for r in calls if r.url.host.endswith("algolia.net"))
    assert search.url.host == "45bwzj1sgc-dsn.algolia.net"
    assert search.headers["x-algolia-api-key"] == "c2VhcmNoLW9ubHk="
    assert json.loads(search.content)["facetFilters"] == [["regions:Canada"], ["isHiring:true"]]
    assert calls[-1].url.path == "/companies/bree/jobs"


def test_an_ambiguous_code_is_never_rewritten_to_canada_and_the_gate_rejects_it():
    """"Amsterdam, NL" is deferred rather than rewritten, and the scan's gate
    then drops it — the location is never turned into "..., Canada"."""
    from job_hunt.services.scan import _passes_canada_filter

    for location in ("Amsterdam, NL", "Bratislava, SK"):
        assert canadian_location(location) == ""
        assert _passes_canada_filter(location) is False


def test_an_app_id_that_is_not_an_id_is_not_used_as_a_hostname():
    page = '<script>window.AlgoliaOpts = {"app":"evil.example.com/x","key":"k"};</script>'
    assert algolia_opts(page) is None


def test_an_absolute_posting_url_is_kept_as_it_is():
    calls: list[httpx.Request] = []
    page = _jobs_page(_job("Software Engineer", "Toronto, ON, CA", "2 days",
                           "https://jobs.lever.co/bree/abc"))
    result = _scan(_handler(calls, jobs_html=page))
    assert result.postings[0].url == "https://jobs.lever.co/bree/abc"


def test_a_directory_without_a_key_is_an_error_not_an_empty_source():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>redesigned</html>")

    result = _scan(handler)
    assert result.postings == [] and result.health.errors == 1
    assert "Algolia" in result.health.note


def test_a_jobs_page_that_does_not_parse_is_counted():
    calls: list[httpx.Request] = []
    result = _scan(_handler(calls, jobs_html="<html>no props</html>"))
    assert result.postings == [] and result.health.errors == 1


def test_disabled_does_not_touch_the_network():
    assert scan_yc_companies_source({"enabled": False}).postings == []
