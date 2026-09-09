"""Recognising a Workday page.

Replaces `"myworkdayjobs.com" in page.url` written out thirteen times. The new
version is deliberately *stricter* than the substring test it replaces, and the
cases below pin that difference so it stays a decision rather than a surprise.
"""

from __future__ import annotations

import pytest

from job_hunt.services.workday.detect import is_workday_page, is_workday_url


class _Page:
    def __init__(self, url: str) -> None:
        self.url = url


@pytest.mark.parametrize(
    "url",
    [
        "https://acme.wd5.myworkdayjobs.com/en-US/careers/job/Engineer_R-1",
        "https://acme.wd1.myworkdayjobs.com/careers",
        "https://acme.myworkdayjobs.com/careers",
        "HTTPS://ACME.WD3.MYWORKDAYJOBS.COM/careers",
    ],
)
def test_real_workday_hosts(url: str) -> None:
    assert is_workday_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://boards.greenhouse.io/acme/jobs/1",
        "https://www.linkedin.com/jobs/view/123",
        "https://acme.com/careers",
    ],
)
def test_other_hosts(url: str) -> None:
    assert is_workday_url(url) is False


def test_a_url_that_merely_mentions_workday_is_not_workday() -> None:
    """The substring test this replaced said yes to both of these. An aggregator
    link carrying a Workday URL as a query parameter is a page on the
    aggregator, and running the Workday step machine against it would drive a
    form that is not there."""
    assert is_workday_url("https://jobs.example.com/out?to=https://acme.myworkdayjobs.com/x") is False
    assert is_workday_url("https://myworkdayjobs.com.phishing.example/careers") is False


def test_a_lookalike_suffix_is_not_a_tenant() -> None:
    """`endswith` on the raw string would accept this; the dot matters."""
    assert is_workday_url("https://notmyworkdayjobs.com/careers") is False


def test_the_page_probe_reads_the_current_url_not_the_requested_one() -> None:
    """The reason there are two functions: an employer vanity domain that
    redirects into a tenant is a Workday page even though the URL the operator
    pasted was not."""
    assert is_workday_url("https://careers.acme.com/job/1") is False
    assert is_workday_page(_Page("https://acme.wd5.myworkdayjobs.com/job/1")) is True


def test_the_page_probe_survives_a_page_with_no_url() -> None:
    assert is_workday_page(_Page("")) is False
    assert is_workday_page(object()) is False
