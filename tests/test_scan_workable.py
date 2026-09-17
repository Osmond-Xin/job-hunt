"""Workable board discovery.

Added 2026-09-16. Moomoo (Futu) and CIeNET International — China-linked
employers with Markham / North York roles — post on Workable, which no tier of
the scan could read.
"""

from __future__ import annotations

from job_hunt.services.scan import (
    _infer_api_url,
    _parse_workable,
    _passes_canada_filter,
    _supports_direct_fetch,
)

# Shape copied from the live CIeNET widget feed, 2026-09-16.
FEED = {
    "name": "CIeNET",
    "jobs": [
        {
            "title": "Software Developer - Frontend Enterprise Web Applications Development",
            "url": "https://apply.workable.com/j/AAA111",
            "published_on": "2026-08-18",
            "country": "Canada", "city": "Markham", "state": "Ontario",
            "telecommuting": False,
        },
        {
            "title": "Senior Software Developer - Web Applications",
            "url": "https://apply.workable.com/j/BBB222",
            "published_on": "2026-08-01",
            "country": "United States", "city": "Seattle", "state": "Washington",
            "telecommuting": False,
        },
        {"title": "", "url": "https://apply.workable.com/j/CCC333"},
        {"title": "No link", "url": ""},
    ],
}


def test_a_workable_board_is_fetched_directly_rather_than_searched():
    company = {"name": "CIeNET International", "careers_url": "https://apply.workable.com/cienet/"}
    assert _supports_direct_fetch(company) is True
    assert _infer_api_url(company["careers_url"]) == "https://apply.workable.com/api/v1/widget/accounts/cienet"


def test_rows_carry_url_date_and_a_location_the_canada_gate_can_split():
    jobs = _parse_workable(FEED, {"name": "CIeNET International"})
    assert [job.url for job in jobs] == ["https://apply.workable.com/j/AAA111", "https://apply.workable.com/j/BBB222"]
    markham, seattle = jobs
    assert markham.posted == "2026-08-18"
    assert markham.company == "CIeNET International"
    assert markham.portal == "workable"
    assert _passes_canada_filter(markham.location) is True
    assert _passes_canada_filter(seattle.location) is False


def test_a_remote_posting_says_so_in_its_location():
    feed = {"jobs": [{"title": "Data Analyst", "url": "https://apply.workable.com/j/X", "country": "Canada",
                      "telecommuting": True}]}
    assert _parse_workable(feed, {"name": "Moomoo"})[0].location == "Remote, Canada"
