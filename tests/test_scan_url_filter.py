"""Tests for the job-posting URL filter on search-derived scan hits.

Motivated by a measured national scan: 294 jobbank.gc.ca hits contained only
10 real postings — the rest were occupation-profile, search-result and
outlook-report pages. See scan.py's module comment.
"""

from __future__ import annotations

import pytest

from job_hunt.services.scan import is_job_posting_url


@pytest.mark.parametrize(
    "url",
    [
        # Job Bank: only the posting detail page counts.
        "https://www.jobbank.gc.ca/jobsearch/jobposting/49766397",
        "https://on.jobbank.gc.ca/jobsearch/jobposting/49709908",
        # Indeed detail pages.
        "https://ca.indeed.com/viewjob?jk=b3c33813c78b78e2",
        # Canadian LinkedIn postings.
        "https://ca.linkedin.com/jobs/view/ai-engineer-at-acme-4393599552",
        "https://www.linkedin.com/jobs/view/4406188128/",
        # Employer career domains on unknown hosts stay allowed.
        "https://jobs.scotiabank.com/job/Toronto-Data-Analyst-ON-M5C2W1/12345",
        "https://olg.wd3.myworkdayjobs.com/en-US/external/job/Sault-Ste-Marie/Analyst",
        "https://careers.hsnsudbury.ca/job/Sudbury-Manager-Financial-Planning",
    ],
)
def test_accepts_real_postings(url: str) -> None:
    assert is_job_posting_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        # Job Bank reference pages — the dominant noise class.
        "https://www.jobbank.gc.ca/marketreport/requirements/23214/ca",
        "https://www.jobbank.gc.ca/marketreport/summary-occupation/5581/ca",
        "https://www.jobbank.gc.ca/jobsearch/jobsearch?searchstring=developer",
        "https://www.jobbank.gc.ca/outlookreport/occupation/23214",
        # Indeed category / search / company pages.
        "https://ca.indeed.com/q-it-l-northwest-territories-jobs.html",
        "https://ca.indeed.com/jobs?q=data+analyst",
        "https://ca.indeed.com/cmp/Northwestel",
        # Non-Canadian LinkedIn locales.
        "https://uk.linkedin.com/jobs/view/ai-engineer-at-acme-123",
        "https://in.linkedin.com/jobs/view/ai-engineer-at-acme-123",
        # Reference / salary / forum / stock sites.
        "https://en.wikipedia.org/wiki/Premier_of_the_Northwest_Territories",
        "https://www.levels.fyi/companies/shopify/salaries",
        "https://github.com/speedyapply/2026-SWE-College-Jobs",
        "https://www.teamblind.com/post/whatever",
        "https://simplywall.st/stocks/ca/some-co",
        "https://www.tipranks.com/stocks/abc",
        "https://www.salaryexpert.com/salary/job/gis-analyst/canada",
        # Press wires, data brokers, review sites.
        "https://www.globenewswire.com/news-release/2026/07/07/3323391/mariner",
        "https://rocketreach.co/dash-hudson-profile_b5f3a8f6f42d34bc",
        "https://research.com/software/reviews/benevity",
        # Marketing pages on unknown hosts.
        "https://www.getjobber.com/faq/",
        "https://www.commercient.com/product/google-ads-jobber/",
        "https://zenvanriel.com/job/ai-engineer-salary-toronto/",
        # A bare site root is never a posting.
        "https://www.intern-list.com/",
        "https://example.com",
    ],
)
def test_rejects_non_postings(url: str) -> None:
    assert is_job_posting_url(url) is False


@pytest.mark.parametrize("url", ["", "   ", "not-a-url", "ftp://example.com/job/1"])
def test_rejects_malformed_urls(url: str) -> None:
    assert is_job_posting_url(url) is False


def test_discovery_channel_drops_reference_pages() -> None:
    """The filter is wired into the tier-3 channel, not just available."""
    from job_hunt.services.scan import scan_discovery_channels

    class Hit:
        def __init__(self, url: str, title: str) -> None:
            self.url = url
            self.title = title
            self.description = ""

    class Stub:
        def search(self, query, **kwargs):
            return [
                Hit(
                    "https://www.jobbank.gc.ca/marketreport/requirements/23214/ca",
                    "Software Developer in Yukon | Job requirements - Job Bank",
                ),
                Hit(
                    "https://www.jobbank.gc.ca/jobsearch/jobposting/49766397",
                    "Software Developer - Marine Thinking",
                ),
            ]

    channels = [
        {
            "id": "jobbank_canada",
            "enabled": True,
            "modes": ["full"],
            "query_template": '"{role}" "{location}" site:jobbank.gc.ca',
            "locations": ["Yukon"],
        }
    ]
    jobs = scan_discovery_channels(channels, Stub(), mode="full")

    assert [j.url for j in jobs] == [
        "https://www.jobbank.gc.ca/jobsearch/jobposting/49766397"
    ]
