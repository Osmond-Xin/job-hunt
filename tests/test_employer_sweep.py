"""The careers-page sweep keeps only anchors that were on the page — tested through ``collect``."""

from __future__ import annotations

import pytest

from job_hunt.services import employer_sweep as es

TOWN = es.Employer(employer="Town of X", community="Hay River", province="NT",
                   careers_url="https://town.ca/careers")

PAGE = """
<a href="/careers/it-analyst-2026">IT Analyst (Permanent)</a>
<a href="/careers/cook">Cook II</a>
<a href="mailto:hr@town.ca">Email HR</a>
<a href="#top">Back to top</a>
<a href="https://town.bamboohr.com/careers"><span>View all openings</span></a>
<a href="/careers/it-analyst-2026">IT Analyst (Permanent)</a>
"""
BOARD = '<a href="https://town.bamboohr.com/careers/7">Systems Administrator</a>'


def _by_text(*wanted, follow=()):
    """A fake model: pick links whose text is in ``wanted``; follow those in ``follow``."""
    def ask(employer, links):
        return ([i for i, link in enumerate(links, 1) if link.text in wanted],
                [i for i, link in enumerate(links, 1) if link.text in follow], "")
    return ask


def _pages(pages):
    def fetch(url):
        if url not in pages:
            raise TimeoutError(url)
        return pages[url], url
    return fetch


def test_postings_on_the_page_and_one_hop_away_are_kept_with_the_community() -> None:
    report = es.collect(
        [TOWN], known_urls=set(), render=None,
        fetch=_pages({"https://town.ca/careers": PAGE, "https://town.bamboohr.com/careers": BOARD}),
        ask=_by_text("IT Analyst (Permanent)", "Systems Administrator", follow=("View all openings",)),
    )
    assert [(r.title, r.url, r.location) for r in report.rows] == [
        ("IT Analyst (Permanent)", "https://town.ca/careers/it-analyst-2026", "Hay River, NT"),
        ("Systems Administrator", "https://town.bamboohr.com/careers/7", "Hay River, NT"),
    ]
    assert report.unreadable == []


def test_indices_the_model_invents_are_dropped_and_known_or_refused_urls_are_not_returned() -> None:
    def ask(employer, links):
        return [1, 2, 99], [], ""

    report = es.collect(
        [TOWN], known_urls={"https://town.ca/careers/cook"}, render=None,
        fetch=_pages({"https://town.ca/careers": PAGE}), ask=ask,
        keep=lambda url, title: "Cook" not in title,
    )
    assert [r.title for r in report.rows] == ["IT Analyst (Permanent)"]


def test_a_garbled_model_answer_is_unreadable_not_no_jobs() -> None:
    assert es._parse_answer("not JSON", 3) is None
    assert es._parse_answer('ok {"jobs": [1, 9, "2"], "follow": [3, 4, 5, 6]}', 3) == ([1, 2], [3])
    # a stray brace after the JSON is not a garbled answer (OpenCode round 4b)
    assert es._parse_answer('{"jobs": [1, 2], "follow": []} — {both are technical}', 3) == ([1, 2], [])

    report = es.collect([TOWN], known_urls=set(), render=None, fetch=_pages({"https://town.ca/careers": PAGE}),
                        ask=lambda e, links: ([], [], "model answer was not the JSON asked for"))
    assert report.rows == [] and "not the JSON" in report.unreadable[0].error


def test_a_failed_page_is_reported_not_raised() -> None:
    report = es.collect([TOWN], known_urls=set(), render=None, fetch=_pages({}), ask=_by_text())
    assert report.rows == [] and "TimeoutError" in report.unreadable[0].error


def test_a_javascript_job_list_under_a_static_nav_bar_is_rendered() -> None:
    static = "".join(f'<a href="/nav{i}">Nav {i}</a>' for i in range(5)) + '<div id="jobs"></div>'
    rendered = static + '<a href="#/jobs/1">Data Analyst</a>'
    calls = []

    def render(url):
        calls.append(url)
        return rendered, url

    report = es.collect([TOWN], known_urls=set(), fetch=_pages({"https://town.ca/careers": static}),
                        render=render, ask=_by_text("Data Analyst"))
    assert calls == ["https://town.ca/careers"]
    assert [r.url for r in report.rows] == ["https://town.ca/careers#/jobs/1"]


def test_a_long_page_is_read_in_batches_not_cut() -> None:
    page = "".join(f'<a href="/n{i}">Nav {i}</a>' for i in range(300)) + '<a href="/j/it">IT Analyst</a>'
    batches = []

    def ask(employer, links):
        batches.append(len(links))
        return [i for i, link in enumerate(links, 1) if link.text == "IT Analyst"], [], ""

    report = es.collect([TOWN], known_urls=set(), render=None, fetch=_pages({"https://town.ca/careers": page}),
                        ask=ask)
    assert batches == [es.BATCH, 301 - es.BATCH]
    assert [r.title for r in report.rows] == ["IT Analyst"]


def test_linkedin_is_never_fetched_offered_or_returned() -> None:
    fetched = []
    page = ('<a href="/j/1">Data Analyst</a>'
            '<a href="https://ca.linkedin.com/jobs/view/1">Data Engineer</a>'
            '<a href="https://www.linkedin.com/company/x/jobs">All our jobs</a>')

    def fetch(url):
        fetched.append(url)
        return page, url

    offered = []

    def ask(employer, links):
        offered.extend(link.url for link in links)
        return list(range(1, len(links) + 1)), list(range(1, len(links) + 1)), ""

    report = es.collect([TOWN], known_urls=set(), render=None, fetch=fetch, ask=ask)
    assert [r.title for r in report.rows] == ["Data Analyst"]
    assert not any("linkedin" in url for url in fetched + offered)


def test_a_page_that_redirects_to_linkedin_is_not_read() -> None:
    report = es.collect([TOWN], known_urls=set(), render=None,
                        fetch=lambda url: ('<a href="/j/1">Data Analyst</a>', "https://www.linkedin.com/company/x"),
                        ask=_by_text("Data Analyst"))
    assert report.rows == [] and "LinkedIn" in report.unreadable[0].error


def test_without_mmx_nothing_is_fetched(monkeypatch) -> None:
    monkeypatch.setattr(es.shutil, "which", lambda name: None)
    with pytest.raises(es.ModelUnavailable):
        es.collect([TOWN], known_urls=set(), fetch=lambda url: pytest.fail("fetched"))


def test_a_posting_on_a_large_employers_hiring_system_gets_no_community() -> None:
    assert es.location_of(TOWN, "https://town.ca/careers/it-analyst") == "Hay River, NT"
    assert es.location_of(TOWN, "https://nasdaq.wd1.myworkdayjobs.com/x/job/USA---New-York/1") == ""
    assert es.location_of(TOWN, "https://town.wd3.myworkdayjobs.com/x/job/Hay-River-NT/IT_1") == "Hay River, NT"
    assert es.location_of(TOWN, "https://careers.microsoft.com/job/1") == ""
    assert es.location_of(TOWN, "https://town.icims.com/jobs/1") == "Hay River, NT"
    # A tenant named after the town says nothing about where the posting is (OpenCode round 4b)
    assert es.location_of(TOWN, "https://hayriver.wd1.myworkdayjobs.com/x/job/Toronto-ON/IT_1") == ""


def test_a_redirect_to_linkedin_is_refused_before_it_is_requested(monkeypatch) -> None:
    import httpx

    requested = []

    def handler(request):
        requested.append(str(request.url))
        if request.url.host == "town.ca":
            return httpx.Response(302, headers={"location": "https://www.linkedin.com/company/town"})
        return httpx.Response(200, text="<a href='/j'>Data Analyst</a>")

    real_client = httpx.Client
    monkeypatch.setattr(es.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(es.LinkedInRedirect):
        es._fetch("https://town.ca/careers")
    assert requested == ["https://town.ca/careers"]


def test_a_wrongly_shaped_model_answer_is_unreadable() -> None:
    for answer in ("{}", '{"error": "model failed"}', '{"jobs": "1", "follow": []}'):
        assert es._parse_answer(answer, 3) is None


def test_a_hash_route_posting_survives_the_production_posting_url_gate() -> None:
    from job_hunt.services.scan import is_job_posting_url

    report = es.collect([TOWN], known_urls=set(), render=None,
                        fetch=_pages({"https://town.ca/careers": '<a href="https://town.ca/#/jobs/1">IT Analyst</a>'}),
                        ask=_by_text("IT Analyst"), keep=lambda url, title: is_job_posting_url(url))
    assert [r.url for r in report.rows] == ["https://town.ca/#/jobs/1"]


def test_a_sweep_that_failed_part_way_is_reported_as_partial() -> None:
    report = es.collect(
        [TOWN], known_urls=set(), render=None,
        fetch=_pages({"https://town.ca/careers": PAGE}),  # the followed ATS board times out
        ask=_by_text("IT Analyst (Permanent)", follow=("View all openings",)),
    )
    assert [r.title for r in report.rows] == ["IT Analyst (Permanent)"]
    assert report.unreadable == [] and "TimeoutError" in report.partial[0].error



def test_follow_may_be_left_out_and_a_pipe_never_reaches_the_pipeline() -> None:
    assert es._parse_answer('{"jobs": [1]}', 2) == ([1], [])
    report = es.collect([TOWN], known_urls=set(), render=None,
                        fetch=_pages({"https://town.ca/careers": '<a href="/j/1">Apply Now | IT Analyst</a>'}),
                        ask=lambda e, links: ([1], [], ""))
    assert report.rows[0].title == "Apply Now - IT Analyst"



def test_linkedin_spelled_another_way_is_still_linkedin() -> None:
    from job_hunt.services.web_extract import _is_proxy_only_host

    for url in ("https://www.linkedin.com./company/x", "https://user:pass@www.linkedin.com/x",
                "https://CA.LinkedIn.com:443/jobs/1"):
        assert _is_proxy_only_host(url), url
    assert not _is_proxy_only_host("https://notlinkedin.com/x")


def test_a_later_batch_failing_keeps_what_earlier_batches_picked() -> None:
    page = '<a href="/j/it">IT Analyst</a>' + "".join(f'<a href="/n{i}">Nav {i}</a>' for i in range(300))
    calls = []

    def ask(employer, links):
        calls.append(1)
        if len(calls) == 2:
            return [], [], "timed out"
        return [i for i, link in enumerate(links, 1) if link.text == "IT Analyst"], [], ""

    report = es.collect([TOWN], known_urls=set(), render=None, fetch=_pages({"https://town.ca/careers": page}),
                        ask=ask)
    assert [r.title for r in report.rows] == ["IT Analyst"]
    assert "timed out" in report.partial[0].error



def test_a_board_with_no_links_is_unreadable_and_a_model_crash_stays_with_its_employer() -> None:
    other = es.Employer(employer="Other", community="Hay River", province="NT", careers_url="https://other.ca/jobs")
    pages = {"https://town.ca/careers": "<div>PeopleSoft</div>", "https://other.ca/jobs": '<a href="/j/1">IT Analyst</a>'}

    def ask(employer, links):
        if employer is other:
            raise OSError("mmx vanished")
        return [], [], ""

    report = es.collect([TOWN, other], known_urls=set(), fetch=_pages(pages), render=lambda url: (pages[url], url),
                        ask=ask)
    errors = {r.employer.employer: r.error for r in report.unreadable}
    assert "no links" in errors["Town of X"] and "OSError" in errors["Other"]



def test_a_broken_anchor_is_skipped_and_a_raising_later_batch_keeps_earlier_picks() -> None:
    assert [link.text for link in es._page_links('<a href="https://[broken">x</a><a href="/j">IT Analyst</a>',
                                           "https://town.ca/")] == ["IT Analyst"]
    page = '<a href="/j/it">IT Analyst</a>' + "".join(f'<a href="/n{i}">Nav {i}</a>' for i in range(300))
    calls = []

    def ask(employer, links):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("mmx died")
        return [i for i, link in enumerate(links, 1) if link.text == "IT Analyst"], [], ""

    report = es.collect([TOWN], known_urls=set(), render=None, fetch=_pages({"https://town.ca/careers": page}),
                        ask=ask)
    assert [r.title for r in report.rows] == ["IT Analyst"] and "OSError" in report.partial[0].error
