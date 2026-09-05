"""Regional tech-association boards — the priority discovery tier."""

from __future__ import annotations

import pytest

from job_hunt.services.regional_boards import (
    _employer_from_apply_url,
    enrich_dns_rows,
    parse_digital_nova_scotia,
    parse_dns_detail,
    parse_tech_manitoba,
    scan_regional_boards,
)

DNS_HTML = """
<a href="https://digitalnovascotia.com/job-posts/senior-ai-engineer-platform-engineering-2/">x</a>
<a href="https://digitalnovascotia.com/job-posts/data-analyst-29/">x</a>
<a href="https://digitalnovascotia.com/job-posts/page/2/">2</a>
<a href="https://digitalnovascotia.com/job-posts/feed/">Feed</a>
<a href="https://digitalnovascotia.com/job-posts/">index</a>
"""

TECHMB_HTML = """
<div class="card gz-jobs-card gz-no-logo">
  <div class="card-header">
    <a href="https://members.techmanitoba.ca/jobs/info/supervisor-business-analysis-2876">
      <span class="gz-img-placeholder">Johnston Group Inc.</span>
    </a>
  </div>
  <div class="card-body gz-card-top gz-jobs-card-body">
    <small class="text-muted gz-jobs-date">Posted 07/31/2026</small>
    <h5 class="card-title gz-card-title">
      <a href="https://members.techmanitoba.ca/jobs/info/supervisor-business-analysis-2876">Supervisor, Business Analysis</a>
    </h5>
  </div>
</div>
<div class="card gz-jobs-card">
  <div class="card-header"><a href="/jobs/info/itsm-analyst-2875"><span class="gz-img-placeholder">Payworks</span></a></div>
  <div class="card-body">
    <h5 class="card-title gz-card-title"><a href="/jobs/info/itsm-analyst-2875">ITSM &amp; Automation Analyst</a></h5>
  </div>
</div>
"""


def test_wordpress_pagination_and_feeds_are_not_jobs():
    rows = parse_digital_nova_scotia(DNS_HTML)
    titles = [row["title"] for row in rows]
    assert titles == ["Senior Ai Engineer Platform Engineering", "Data Analyst"]
    assert all(row["location"] == "Nova Scotia" for row in rows)


def test_the_employer_is_left_blank_rather_than_guessed():
    # Digital Nova Scotia's listing markup has no employer; inventing one from
    # the slug would put a wrong company name into the tracker.
    assert parse_digital_nova_scotia(DNS_HTML)[0]["company"] == ""


def test_growthzone_card_yields_title_company_and_iso_date():
    rows = parse_tech_manitoba(TECHMB_HTML)
    assert len(rows) == 2
    first = rows[0]
    # The card header anchor holds the employer; a looser regex returned it as
    # the title, which is the bug this asserts against.
    assert first["title"] == "Supervisor, Business Analysis"
    assert first["company"] == "Johnston Group Inc."
    assert first["posted"] == "2026-07-31"
    assert rows[1]["title"] == "ITSM & Automation Analyst"
    assert rows[1]["url"].startswith("https://members.techmanitoba.ca/")


def test_a_disabled_board_is_never_fetched():
    class Client:
        def get(self, *_a, **_k):  # pragma: no cover - must not run
            raise AssertionError("disabled board fetched")

        def close(self):
            pass

    out = scan_regional_boards(
        {"enabled": True, "boards": {"tech_manitoba": {"enabled": False}}},
        client=Client(),
        sleep=lambda _s: None,
    )
    assert out == []


def test_a_failed_fetch_is_counted_not_silently_empty():
    import httpx

    class Client:
        def get(self, *_a, **_k):
            raise httpx.ConnectError("down")

        def close(self):
            pass

    stats: dict = {}
    out = scan_regional_boards(
        {"enabled": True, "delay_s": 0, "boards": {"tech_manitoba": {"enabled": True}}},
        client=Client(),
        sleep=lambda _s: None,
        stats=stats,
    )
    assert out == []
    assert stats["tech_manitoba"]["errors"] == 1


def _dns_page(slugs: list[str]) -> str:
    return "\n".join(
        f'<a href="https://digitalnovascotia.com/job-posts/{slug}/">x</a>' for slug in slugs
    )


def test_the_wordpress_board_is_read_past_its_first_page(monkeypatch):
    # The live board carries 120 postings over 4 pages; reading only the first
    # returned 30 and reported no problem at all.
    pages = {
        "https://digitalnovascotia.com/job-posts/": _dns_page(["a", "b"]),
        "https://digitalnovascotia.com/job-posts/page/2/": _dns_page(["c", "d"]),
        "https://digitalnovascotia.com/job-posts/page/3/": _dns_page(["e"]),
    }
    fetched: list[str] = []

    def fake_curl(url: str, _timeout: float):
        fetched.append(url)
        # Past the last page WordPress 404s, which the walk reads as the end.
        return pages.get(url, ""), url not in pages

    monkeypatch.setattr("job_hunt.services.regional_boards._fetch_via_curl", fake_curl)
    stats: dict = {}
    out = scan_regional_boards(
        {"enabled": True, "delay_s": 0, "boards": {"digital_nova_scotia": {"enabled": True, "enrich": False}}},
        sleep=lambda _s: None,
        stats=stats,
    )
    assert [row["title"] for row in out] == ["A", "B", "C", "D", "E"]
    assert len(fetched) == 4  # three real pages, then the 404 that ends the walk
    assert stats["digital_nova_scotia"] == {"collected": 5, "errors": 1, "truncated": False}


def test_a_repeated_page_ends_the_walk_rather_than_duplicating(monkeypatch):
    # WordPress serves the last page for any overshoot, so an unbounded walk
    # would otherwise re-collect the same postings until max_pages ran out.
    monkeypatch.setattr(
        "job_hunt.services.regional_boards._fetch_via_curl",
        lambda _url, _timeout: (_dns_page(["a", "b"]), False),
    )
    stats: dict = {}
    out = scan_regional_boards(
        {"enabled": True, "delay_s": 0, "boards": {"digital_nova_scotia": {"enabled": True, "enrich": False}}},
        sleep=lambda _s: None,
        stats=stats,
    )
    assert len(out) == 2
    assert stats["digital_nova_scotia"]["collected"] == 2


def test_a_single_page_board_is_never_reported_as_truncated(monkeypatch):
    # Tech Manitoba renders every card into one response. Flagging it as
    # truncated would train the operator to ignore the warning that matters.
    class Client:
        def get(self, *_a, **_k):
            return type("R", (), {"status_code": 200, "text": TECHMB_HTML})()

        def close(self):
            pass

    stats: dict = {}
    scan_regional_boards(
        {"enabled": True, "delay_s": 0, "boards": {"tech_manitoba": {"enabled": True}}},
        client=Client(),
        sleep=lambda _s: None,
        stats=stats,
    )
    assert stats["tech_manitoba"]["truncated"] is False


DNS_DETAIL = """
<title>Forward Deployed Developer (Golang/Next.js) &#8211; Digital Nova Scotia &#8211; Leading Digital Industry</title>
<a href="https://cgi.njoyn.com/CORP/xweb/xweb.asp?clid=21001&amp;Jobid=J0726-1945" class="button">Apply For Job</a>
"""


def test_the_detail_page_recovers_the_real_title_and_the_employer():
    # The slug gives "Forward Deployed Developer Golang Next Js" and no
    # employer at all; both cost the operator a manual click per posting.
    detail = parse_dns_detail(DNS_DETAIL)
    assert detail["title"] == "Forward Deployed Developer (Golang/Next.js)"
    assert detail["company"] == "cgi"
    assert detail["apply_url"].startswith("https://cgi.njoyn.com/")


@pytest.mark.parametrize(
    "apply_url,expected",
    [
        # Shared ATS hosts: the tenant is the employer, the domain is the vendor.
        ("https://cgi.njoyn.com/CORP/xweb/xweb.asp?clid=1", "cgi"),
        ("https://jobs.dayforcehcm.com/en-US/mariner/CANDIDATEPORTAL/jobs/2690", "mariner"),
        ("https://boards.greenhouse.io/acmecorp/jobs/12", "acmecorp"),
        ("https://jobs.lever.co/some-startup/abc", "some startup"),
        ("https://bigco.wd3.myworkdayjobs.com/en-US/Careers", "bigco"),
        # An employer's own careers host.
        ("https://jobs.rbc.com/ca/en/job/R-0000173716/Senior-Data-Engineer", "rbc"),
        # A recruitment agency, which triage can only filter once it has a name.
        ("https://meridiarecruitment.ca/Career/17848940493500000008rcq", "meridiarecruitment"),
        # Vendor hosts that would otherwise read as the employer itself.
        ("https://compugen-pc.my.salesforce-sites.com/recruit/fRecruit__ApplyJob?vacancyNo=VN9735", "compugen pc"),
        ("https://apply.workable.com/redspace/j/43B2564ABE/", "redspace"),
        ("https://www.careerbeacon.com/en/job/2235715/caa-atlantic/content-and-social/saint-john-nb", "caa atlantic"),
        # No employer anywhere in this CareerBeacon form; unresolved beats guessed.
        ("https://jobs.careerbeacon.com/details/systems-analyst-data/2234441", "careerbeacon"),
        ("", ""),
    ],
)
def test_the_employer_comes_from_where_the_apply_button_goes(apply_url, expected):
    assert _employer_from_apply_url(apply_url) == expected


def test_enrichment_fetches_each_posting_once_and_then_reads_its_cache(tmp_path, monkeypatch):
    calls: list[str] = []

    def fake_curl(url: str, _timeout: float):
        calls.append(url)
        return DNS_DETAIL, False

    monkeypatch.setattr("job_hunt.services.regional_boards._fetch_via_curl", fake_curl)
    cache = tmp_path / "dns.json"
    rows = [{"url": "https://digitalnovascotia.com/job-posts/fde/", "title": "Fde", "company": ""}]

    assert enrich_dns_rows(rows, timeout=5, sleep=lambda _s: None, cache_path=cache) == 1
    assert rows[0]["company"] == "cgi"
    assert rows[0]["title"] == "Forward Deployed Developer (Golang/Next.js)"

    # A posting's employer does not change, so the second sweep spends nothing.
    again = [{"url": "https://digitalnovascotia.com/job-posts/fde/", "title": "Fde", "company": ""}]
    assert enrich_dns_rows(again, timeout=5, sleep=lambda _s: None, cache_path=cache) == 0
    assert again[0]["company"] == "cgi"
    assert len(calls) == 1


def test_a_failed_detail_fetch_leaves_the_row_alone(tmp_path, monkeypatch):
    # A blank company is honest; a half-parsed one would poison the tracker.
    monkeypatch.setattr(
        "job_hunt.services.regional_boards._fetch_via_curl", lambda _u, _t: ("", True)
    )
    rows = [{"url": "https://digitalnovascotia.com/job-posts/x/", "title": "X", "company": ""}]
    enrich_dns_rows(rows, timeout=5, sleep=lambda _s: None, cache_path=tmp_path / "c.json")
    assert rows[0] == {"url": "https://digitalnovascotia.com/job-posts/x/", "title": "X", "company": ""}
