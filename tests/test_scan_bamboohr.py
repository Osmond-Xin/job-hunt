"""BambooHR board discovery.

Added 2026-09-01. Vendasta and Hiveway are the only two employers in this
campaign that have produced a real human interview, and both post on BambooHR —
a platform no tier of the scan could read, so neither board was ever watched for
a second opening. These tests pin the three things that made it invisible: the
per-employer subdomain, the id-only feed, and the spelled-out province.
"""

from __future__ import annotations

from job_hunt.services.scan import (
    _bamboohr_slug,
    _infer_api_url,
    _parse_bamboohr,
    _passes_canada_filter,
    _supports_direct_fetch,
)

# Shape copied from the live Vendasta feed, 2026-09-01.
FEED = {
    "meta": {"totalCount": 3},
    "result": [
        {
            "id": "788",
            "jobOpeningName": "VP, AI Customer Delivery",
            "location": {"city": "Saskatoon", "state": "Saskatchewan"},
            "atsLocation": {"country": None, "state": None, "province": None, "city": None},
        },
        {
            "id": "792",
            "jobOpeningName": "Operations Specialist",
            "location": {"city": "Chennai", "state": "Tamil Nadu"},
            "atsLocation": {"country": None, "state": None, "province": None, "city": None},
        },
        # Neither an id nor a title can be missing and still yield a link.
        {"id": "", "jobOpeningName": "Ghost", "location": {}},
        {"id": "999", "jobOpeningName": "", "location": {}},
    ],
}


def test_every_employer_gets_its_own_subdomain():
    assert _bamboohr_slug("vendasta.bamboohr.com") == "vendasta"
    assert _bamboohr_slug("hiveway.bamboohr.com") == "hiveway"
    # The product's own site is not an employer board.
    assert _bamboohr_slug("www.bamboohr.com") == ""
    assert _bamboohr_slug("bamboohr.com") == ""
    # A lookalike host must not be mistaken for one.
    assert _bamboohr_slug("bamboohr.com.evil.example") == ""
    assert _bamboohr_slug("jobs.ashbyhq.com") == ""


def test_a_bamboohr_board_is_fetched_directly_rather_than_searched():
    company = {"name": "Vendasta", "careers_url": "https://vendasta.bamboohr.com/careers"}
    assert _supports_direct_fetch(company) is True
    assert _infer_api_url(company["careers_url"]) == "https://vendasta.bamboohr.com/careers/list"


def test_the_feed_carries_no_url_so_the_posting_link_is_rebuilt_from_the_id():
    """The two applications already on file are /careers/811 and /careers/32."""
    jobs = _parse_bamboohr(FEED, {"name": "Vendasta"}, "vendasta.bamboohr.com")
    assert [job.url for job in jobs] == [
        "https://vendasta.bamboohr.com/careers/788",
        "https://vendasta.bamboohr.com/careers/792",
    ]
    assert all(job.portal == "bamboohr" for job in jobs)
    assert all(job.company == "Vendasta" for job in jobs)


def test_the_spelled_out_province_survives_the_canada_gate():
    """Vendasta's own board mixes Saskatoon with Chennai and Boca Raton."""
    jobs = _parse_bamboohr(FEED, {"name": "Vendasta"}, "vendasta.bamboohr.com")
    saskatoon, chennai = jobs
    assert saskatoon.location == "Saskatoon, Saskatchewan"
    assert _passes_canada_filter(saskatoon.location) is True
    assert chennai.location == "Chennai, Tamil Nadu"
    assert _passes_canada_filter(chennai.location) is False


def test_a_board_with_no_openings_is_not_an_error():
    assert _parse_bamboohr({"meta": {"totalCount": 0}, "result": []}, {"name": "Hiveway"}, "h.bamboohr.com") == []
    assert _parse_bamboohr({}, {"name": "Hiveway"}, "h.bamboohr.com") == []


def test_the_posting_page_is_a_js_shell_so_extraction_uses_the_detail_feed():
    """Without this the JD reaches the scorer empty and scores 0.0/SKIP —
    indistinguishable from a genuinely bad posting, which is exactly how two
    Indeed rows read on 2026-09-01."""
    from job_hunt.services.web_extract import _bamboohr_company, _bamboohr_detail_api_url

    assert _bamboohr_detail_api_url("https://vendasta.bamboohr.com/careers/829") == (
        "https://vendasta.bamboohr.com/careers/829/detail"
    )
    assert _bamboohr_company("https://vendasta.bamboohr.com/careers/829") == "Vendasta"
    # The board index is not a posting.
    assert _bamboohr_detail_api_url("https://vendasta.bamboohr.com/careers") == ""
    assert _bamboohr_detail_api_url("https://vendasta.bamboohr.com/careers/list") == ""
    assert _bamboohr_detail_api_url("https://jobs.ashbyhq.com/vendasta/829") == ""
