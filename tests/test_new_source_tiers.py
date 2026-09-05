"""Tests for the quota-free source tiers: gov boards, Workday, Adzuna."""

from __future__ import annotations

import httpx
import pytest

from job_hunt.models.posting import JobPosting, SourceHealth, SourceResult, from_row
from job_hunt.services.adzuna import parse_adzuna_results, scan_adzuna, scan_adzuna_source
from job_hunt.services.gov_boards import parse_gnwt, parse_ns_gov
from job_hunt.services.workday_boards import (
    parse_workday_response,
    resolve_workday_target,
    scan_workday,
    scan_workday_source,
)

GNWT_HTML = """
<div class="view-content">
<div class="views-row">
<span class="field-content"><a href="https://www.gov.nt.ca/careers/en/job/28158"
 class="job-search-result-link"><h3>Territorial Statistician</h3>
<div class="views-field-field-job-salary-range">$159k - $227k</div>
<div class="views-field-field-job-location">Yellowknife</div>
<div class="views-field-field-job-closing-date"><span class="views-label">Closes:</span>
 <span class="date-display-single">Aug 19, 2026</span></div></a></span>
</div></div>
"""

NS_HTML = """
<tr class="data-row">
 <td class="colTitle">
  <span class="jobTitle hidden-phone">
   <a href="/job/HALIFAX-Data-Analyst-NS-B3J-2X8/601773217/" class="jobTitle-link">Data Analyst</a>
  </span>
  <div class="jobdetail-phone visible-phone">
   <span class="jobTitle visible-phone">
    <a class="jobTitle-link" href="/job/HALIFAX-Data-Analyst-NS-B3J-2X8/601773217/">Data Analyst</a>
   </span>
   <span class="jobLocation">HALIFAX, NS, CA, B3J 2X8</span>
  </div>
 </td>
</tr>
"""


def test_gnwt_parses_salary_location_and_closing_date() -> None:
    rows = parse_gnwt(GNWT_HTML)
    assert len(rows) == 1
    assert rows[0]["title"] == "Territorial Statistician"
    assert rows[0]["location"] == "Yellowknife"
    assert rows[0]["salary"] == "$159k - $227k"
    assert rows[0]["closes"] == "Aug 19, 2026"
    assert rows[0]["url"].endswith("/careers/en/job/28158")


def test_ns_gov_dedups_the_duplicated_phone_markup() -> None:
    rows = parse_ns_gov(NS_HTML)
    # The same job appears in both hidden-phone and visible-phone spans.
    assert len(rows) == 1
    assert rows[0]["title"] == "Data Analyst"
    assert rows[0]["location"] == "HALIFAX, NS, CA, B3J 2X8"
    assert rows[0]["url"].startswith("https://jobs.novascotia.ca/job/")


@pytest.mark.parametrize(
    "url,expected",
    [
        (
            "https://olg.wd3.myworkdayjobs.com/en-US/Careers-Students/job/X_R26",
            ("olg", "wd3", "Careers-Students"),
        ),
        (
            "https://tcenergy.wd3.myworkdayjobs.com/en-US/CAREER_SITE_TC",
            ("tcenergy", "wd3", "CAREER_SITE_TC"),
        ),
        # No locale segment.
        (
            "https://olg.wd3.myworkdayjobs.com/Careers/job/Toronto/Analyst_R1",
            ("olg", "wd3", "Careers"),
        ),
        ("https://amgen.wd1.myworkdayjobs.com/en-US/Careers", ("amgen", "wd1", "Careers")),
    ],
)
def test_resolves_workday_triple_from_url(url, expected) -> None:
    assert resolve_workday_target(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://example.com/careers",
        "https://job-boards.greenhouse.io/faire",
        "https://olg.wd3.myworkdayjobs.com/",
    ],
)
def test_rejects_non_workday_urls(url) -> None:
    assert resolve_workday_target(url) is None


def test_workday_builds_absolute_posting_urls() -> None:
    payload = {
        "total": 2,
        "jobPostings": [
            {
                "title": "Data Architect Director",
                "externalPath": "/job/Toronto-Ontario-Canada/Data-Architect_R26_00034",
                "locationsText": "2 Locations",
            },
            {"title": "", "externalPath": "/job/x"},  # dropped: no title
        ],
    }
    rows = parse_workday_response(payload, "olg", "wd3", "Careers")
    assert len(rows) == 1
    assert rows[0]["url"] == (
        "https://olg.wd3.myworkdayjobs.com/en-US/Careers"
        "/job/Toronto-Ontario-Canada/Data-Architect_R26_00034"
    )
    assert rows[0]["location"] == "2 Locations"


def test_workday_scan_resolves_url_and_pages() -> None:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url)})
        # One short page -> loop stops after the first request.
        return httpx.Response(
            200,
            json={
                "total": 1,
                "jobPostings": [{"title": "Analyst", "externalPath": "/job/a"}],
            },
            request=request,
        )

    rows = scan_workday(
        {
            "enabled": True,
            "page_size": 20,
            "employers": [
                {"name": "OLG", "url": "https://olg.wd3.myworkdayjobs.com/en-US/Careers"}
            ],
        },
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
    )

    assert len(calls) == 1
    assert "/wday/cxs/olg/Careers/jobs" in calls[0]["url"]
    assert rows[0]["company"] == "OLG"


def test_adzuna_parses_nested_company_and_location() -> None:
    payload = {
        "results": [
            {
                "title": "Data Analyst",
                "redirect_url": "https://www.adzuna.ca/details/1",
                "company": {"display_name": "Saint Mary's University"},
                "location": {"display_name": "Halifax, Halifax region"},
                "created": "2026-08-01T00:00:00Z",
            },
            {"title": "No URL"},  # dropped
        ]
    }
    rows = parse_adzuna_results(payload)
    assert len(rows) == 1
    assert rows[0]["company"] == "Saint Mary's University"
    assert rows[0]["location"] == "Halifax, Halifax region"


def test_adzuna_needs_credentials_and_roles() -> None:
    class Cfg:
        enabled = True

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not hit the network")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert scan_adzuna(Cfg(), ["AI Engineer"], app_id="", app_key="k", client=client) == []
    assert scan_adzuna(Cfg(), [], app_id="i", app_key="k", client=client) == []


def test_adzuna_stops_paging_on_empty_page() -> None:
    class Cfg:
        enabled = True
        country = "ca"
        results_per_page = 50
        max_pages = 3
        max_days_old = 30
        delay_s = 0
        timeout_s = 5

    pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        pages.append(request.url.path)
        body = (
            {"results": [{"title": "T", "redirect_url": "https://a/1"}]}
            if request.url.path.endswith("/1")
            else {"results": []}
        )
        return httpx.Response(200, json=body, request=request)

    rows = scan_adzuna(
        Cfg(),
        ["AI Engineer"],
        app_id="i",
        app_key="k",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
    )
    assert len(rows) == 1
    assert len(pages) == 2  # page 1 had data, page 2 was empty -> stop


# ----- occupation-based screening instead of title matching (2026-08-06) -----


def test_adzuna_category_screen_drops_off_target_postings() -> None:
    payload = {
        "results": [
            {
                "title": "AI Engineer",
                "redirect_url": "https://a/1",
                "category": {"tag": "it-jobs"},
                "description": "Build LLM pipelines",
            },
            {
                "title": "HubSpot Data Analyst",
                "redirect_url": "https://a/2",
                "category": {"tag": "sales-jobs"},
            },
            # engineering-jobs must survive: real AI roles land there.
            {
                "title": "AI Engineer",
                "redirect_url": "https://a/3",
                "category": {"tag": "engineering-jobs"},
            },
        ]
    }
    rows = parse_adzuna_results(payload, category_exclude=["sales-jobs"])
    assert [r["url"] for r in rows] == ["https://a/1", "https://a/3"]
    assert rows[0]["description"] == "Build LLM pipelines"


def test_adzuna_keeps_paginating_when_a_whole_page_is_filtered_out() -> None:
    class Cfg:
        enabled = True
        country = "ca"
        results_per_page = 50
        max_pages = 3
        max_days_old = 30
        delay_s = 0
        timeout_s = 5
        category_exclude = ["sales-jobs"]

    pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        pages.append(request.url.path)
        if request.url.path.endswith("/1"):
            # Entire page is off-target: filtered to zero, but not the end.
            body = {
                "results": [
                    {
                        "title": "Sales Rep",
                        "redirect_url": "https://a/s",
                        "category": {"tag": "sales-jobs"},
                    }
                ]
            }
        elif request.url.path.endswith("/2"):
            body = {
                "results": [
                    {
                        "title": "AI Engineer",
                        "redirect_url": "https://a/k",
                        "category": {"tag": "it-jobs"},
                    }
                ]
            }
        else:
            body = {"results": []}
        return httpx.Response(200, json=body, request=request)

    rows = scan_adzuna(
        Cfg(),
        ["AI Engineer"],
        app_id="i",
        app_key="k",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
    )

    assert len(pages) == 3, "a fully-filtered page must not stop pagination"
    assert [r["url"] for r in rows] == ["https://a/k"]


def test_negative_only_mode_keeps_noc_verified_titles() -> None:
    from job_hunt.services.scan import _title_matches

    positives = ["data analyst", "software engineer"]
    negatives = [".net", "nurse", "cook"]
    # These are real Job Bank titles fetched by NOC code that the positive
    # list rejected: occupation is already established, so they must pass.
    for title in [
        "information technology (IT) analyst",
        "devops engineer",
        "data architect",
        "database analyst (DBA)",
        "Territorial Statistician",
    ]:
        assert _title_matches(title, positives, negatives, require_positive=False)
        assert not _title_matches(title, positives, negatives)


def test_negative_only_mode_still_screens_off_target_occupations() -> None:
    from job_hunt.services.scan import _title_matches

    negatives = ["nurse", "cook", "teacher"]
    for title in ["Community Health Nurse", "Lead Cook", "Grade 1-3 Teacher"]:
        assert not _title_matches(title, [], negatives, require_positive=False)


def test_gov_boards_query_by_keyword_at_the_source() -> None:
    """Keyword mode issues one query per term against the board's own search."""
    from job_hunt.services.gov_boards import scan_gov_boards

    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(200, text=GNWT_HTML, request=request)

    rows = scan_gov_boards(
        {
            "enabled": True,
            "boards": {
                "gnwt": {"enabled": True, "max_pages": 1, "keywords": ["data", "analyst"]}
            },
        },
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
        sleep=lambda s: None,
    )

    assert [p.get("keywords") for p in seen] == ["data", "analyst"]
    # Same posting returned for both keywords -> deduplicated by URL.
    assert len(rows) == 1
    assert rows[0]["matched_keyword"] == "data"


def test_gov_boards_walk_whole_board_when_no_keywords() -> None:
    from job_hunt.services.gov_boards import scan_gov_boards

    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        # Empty second page stops the walk.
        body = GNWT_HTML if not request.url.params.get("page") else ""
        return httpx.Response(200, text=body, request=request)

    scan_gov_boards(
        {"enabled": True, "boards": {"gnwt": {"enabled": True, "max_pages": 3}}},
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
        sleep=lambda s: None,
    )

    assert "keywords" not in seen[0]
    assert seen[1]["page"] == "1"


def test_workday_clamps_page_size_to_the_api_cap() -> None:
    """limit>20 returns HTTP 400 from Workday and would zero out the board."""
    from job_hunt.services.workday_boards import scan_workday

    limits: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        limits.append(_json.loads(request.content)["limit"])
        return httpx.Response(200, json={"total": 0, "jobPostings": []}, request=request)

    scan_workday(
        {
            "enabled": True,
            "page_size": 50,
            "employers": [{"name": "X", "tenant": "t", "site": "s"}],
        },
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
    )
    assert limits == [20]


def test_workday_sends_a_browser_user_agent() -> None:
    """Some tenants reject httpx's default UA and fail silently."""
    from job_hunt.services.workday_boards import scan_workday

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("user-agent", ""))
        return httpx.Response(200, json={"total": 0, "jobPostings": []}, request=request)

    # No client passed -> the adapter builds its own, which is the path that
    # carries the header.
    import job_hunt.services.workday_boards as wb

    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    wb.httpx.Client = fake_client
    try:
        scan_workday(
            {"enabled": True, "employers": [{"name": "X", "tenant": "t", "site": "s"}]},
            sleep=lambda s: None,
        )
    finally:
        wb.httpx.Client = real_client

    assert seen and seen[0].startswith("Mozilla/")


def test_an_incomplete_board_sweep_becomes_an_operator_warning() -> None:
    """The adapters always measured coverage; nothing ever read the measurement.

    Digital Nova Scotia returned 30 of its 120 postings for a day and the scan
    summary was indistinguishable from a complete one.
    """
    from job_hunt.services.scan import _board_coverage_warnings

    warnings = _board_coverage_warnings(
        {
            "digital_nova_scotia": {"collected": 30, "errors": 0, "truncated": True},
            "gnwt": {"collected": 40, "advertised": 92, "truncated": True, "errors": 0},
            "ns_gov": {"collected": 65, "advertised": None, "truncated": False, "errors": 2},
            "mb_gov": {"collected": 59, "advertised": None, "truncated": False, "errors": 0},
        }
    )
    joined = "\n".join(warnings)
    assert "digital_nova_scotia" in joined and "raise max_pages" in joined
    assert "board advertises 92" in joined
    assert "ns_gov: 2 failed request(s)" in joined
    # A board that was read to the end without errors stays silent.
    assert "mb_gov" not in joined


@pytest.mark.parametrize(
    "attr, call",
    [
        (
            "scan_jobbank",
            lambda scan_mod, warnings: scan_mod._jobbank_scanned_jobs(
                {"enabled": True}, warnings
            ),
        ),
        (
            "scan_gov_boards",
            lambda scan_mod, warnings: scan_mod._gov_board_scanned_jobs(
                {"enabled": True}, warnings
            ),
        ),
        (
            "scan_regional_boards",
            lambda scan_mod, warnings: scan_mod._regional_board_scanned_jobs(
                {"enabled": True}, warnings
            ),
        ),
        (
            "scan_workday_source",
            lambda scan_mod, warnings: scan_mod._workday_scanned_jobs(
                {"enabled": True}, warnings
            ),
        ),
    ],
)
def test_a_board_sweep_that_raises_is_reported_not_swallowed(attr, call) -> None:
    """`except Exception: return []` made a broken tier look like an empty one.

    Covers all five tier mappers except adzuna, which needs credentials wired
    first — see ``test_adzuna_sweep_that_raises_is_reported_not_swallowed``.
    """
    import job_hunt.services.scan as scan_mod

    def boom(*_a, **_k):
        raise RuntimeError("parser blew up")

    original = getattr(scan_mod, attr)
    setattr(scan_mod, attr, boom)
    try:
        warnings: list[str] = []
        jobs = call(scan_mod, warnings)
    finally:
        setattr(scan_mod, attr, original)

    assert jobs == []
    assert warnings and "parser blew up" in warnings[0]


def test_adzuna_sweep_that_raises_is_reported_not_swallowed(monkeypatch) -> None:
    """Same guard as above for the adzuna mapper."""
    import job_hunt.services.scan as scan_mod

    monkeypatch.setenv("ADZUNA_APP_ID", "id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "key")

    class Cfg:
        enabled = True

    class Settings:
        adzuna = Cfg()

    def boom(*_a, **_k):
        raise RuntimeError("parser blew up")

    original = scan_mod.scan_adzuna_source
    scan_mod.scan_adzuna_source = boom
    try:
        warnings: list[str] = []
        jobs = scan_mod._adzuna_scanned_jobs(Settings(), ["AI Engineer"], warnings)
    finally:
        scan_mod.scan_adzuna_source = original

    assert jobs == []
    assert warnings and "parser blew up" in warnings[0]


# ----- failure counting (2026-09-03): a 200-with-no-content board looked ----
# ----- exactly like an empty one until these adapters started counting -----


def test_jobbank_scan_counts_failed_requests_into_stats() -> None:
    from job_hunt.services.jobbank import scan_jobbank

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="", request=request)

    stats: dict[str, dict[str, object]] = {}
    rows = scan_jobbank(
        {"enabled": True, "noc_codes": ["21232"], "provinces": ["NS"]},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
        stats=stats,
    )

    assert rows == []
    assert stats["21232"]["errors"] == 1
    assert stats["21232"]["collected"] == 0


def test_workday_scan_counts_failed_requests_into_stats() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={}, request=request)

    stats: dict[str, dict[str, object]] = {}
    rows = scan_workday(
        {"enabled": True, "employers": [{"name": "X", "tenant": "t", "site": "s"}]},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
        stats=stats,
    )

    assert rows == []
    assert stats["X"]["errors"] == 1
    assert stats["X"]["collected"] == 0


def test_adzuna_scan_counts_failed_requests_into_stats() -> None:
    class Cfg:
        enabled = True
        country = "ca"
        results_per_page = 50
        max_pages = 2
        max_days_old = 30
        delay_s = 0
        timeout_s = 5

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={}, request=request)

    stats: dict[str, dict[str, object]] = {}
    rows = scan_adzuna(
        Cfg(),
        ["AI Engineer"],
        app_id="i",
        app_key="k",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
        stats=stats,
    )

    assert rows == []
    assert stats["AI Engineer"]["errors"] == 1
    assert stats["AI Engineer"]["collected"] == 0


# ----- posted-date survival (2026-09-03): three tiers scored as permanently
# ----- undated because scan.py never read the field the adapter emitted.
# ----- The normalizer unit tests live in tests/test_posted_date.py, next to
# ----- the shared job_hunt.services.posted_date module; these are the
# ----- end-to-end checks that scan.py's mappers actually wire it in. -----


def test_jobbank_posted_date_survives_into_scanned_job() -> None:
    import job_hunt.services.scan as scan_mod

    def fake_scan_jobbank(config, **_kwargs):
        return [
            {
                "url": "https://www.jobbank.gc.ca/jobsearch/jobposting/1",
                "title": "Data Analyst",
                "company": "Acme",
                "location": "Halifax (NS)",
                "date": "August 06, 2026",
                "noc": "21232",
            }
        ]

    original = scan_mod.scan_jobbank
    scan_mod.scan_jobbank = fake_scan_jobbank
    try:
        jobs = scan_mod._jobbank_scanned_jobs({"enabled": True})
    finally:
        scan_mod.scan_jobbank = original

    assert jobs[0].posted == "2026-08-06"


def test_workday_posted_date_survives_into_scanned_job() -> None:
    """The date normalisation itself is covered end-to-end (real HTTP mock)
    by ``test_workday_source_returns_postings_and_health`` above; this checks
    the other half — that ``_workday_scanned_jobs`` unwraps the
    ``SourceResult`` from ``scan_workday_source`` without dropping the field
    on the way into ``ScannedJob``.
    """
    import job_hunt.services.scan as scan_mod
    import datetime as _dt

    expected = (_dt.date.today() - _dt.timedelta(days=3)).isoformat()

    def fake_scan_workday_source(config, **_kwargs):
        return SourceResult(
            postings=[
                JobPosting(
                    url="https://olg.wd3.myworkdayjobs.com/en-US/Careers/job/a",
                    title="Analyst",
                    company="OLG",
                    location="Toronto",
                    portal="workday",
                    source="workday OLG",
                    source_id="workday",
                    posted=expected,
                )
            ],
            health=SourceHealth(source_id="workday", ok=True, collected=1),
        )

    original = scan_mod.scan_workday_source
    scan_mod.scan_workday_source = fake_scan_workday_source
    try:
        jobs = scan_mod._workday_scanned_jobs({"enabled": True})
    finally:
        scan_mod.scan_workday_source = original

    assert jobs[0].posted == expected


def test_adzuna_posted_date_survives_into_scanned_job(monkeypatch) -> None:
    """See the docstring on the Workday version above: date normalisation
    itself is covered end-to-end by ``test_adzuna_source_returns_postings_and_health``;
    this checks that ``_adzuna_scanned_jobs`` preserves the field when
    unwrapping the ``SourceResult``.
    """
    import job_hunt.services.scan as scan_mod

    monkeypatch.setenv("ADZUNA_APP_ID", "id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "key")

    class Cfg:
        enabled = True

    class Settings:
        adzuna = Cfg()

    def fake_scan_adzuna_source(config, roles, **_kwargs):
        return SourceResult(
            postings=[
                JobPosting(
                    url="https://www.adzuna.ca/details/1",
                    title="Data Analyst",
                    company="Acme",
                    location="Halifax",
                    portal="adzuna",
                    source="adzuna data analyst",
                    source_id="adzuna",
                    posted="2026-08-01",
                )
            ],
            health=SourceHealth(source_id="adzuna", ok=True, collected=1),
        )

    original = scan_mod.scan_adzuna_source
    scan_mod.scan_adzuna_source = fake_scan_adzuna_source
    try:
        jobs = scan_mod._adzuna_scanned_jobs(Settings(), ["data analyst"])
    finally:
        scan_mod.scan_adzuna_source = original

    assert jobs[0].posted == "2026-08-01"


# --- SuccessFactors tenants beyond the Nova Scotia public service -----------
#
# Nova Scotia Health is the province's largest employer and had no coverage at
# all: three applications were made there by hand in Aug 2026. It runs the same
# SuccessFactors platform as jobs.novascotia.ca, but with two differences that
# each silently produced zero rows before this was fixed — a site segment in
# the path (``/nsha/job/...``) and the reversed anchor attribute order.

NSHA_HTML = """
<tr class="data-row">
  <td class="colTitle">
    <span class="jobTitle hidden-phone">
      <a href="/nsha/job/Halifax-Systems-Analyst-Nova-B3K-4N1/605009217/"
         class="jobTitle-link">Systems Analyst - Information Management</a>
    </span>
  </td>
  <span class="jobLocation">Halifax, Nova Scotia, CA</span>
</tr>
<tr class="data-row">
  <td class="colTitle">
    <a class="jobTitle-link"
       href="/nsha/job/Truro-Power-Engineer-Nova-B2N/605111111/">4th Class Power Engineer</a>
    <span class="jobLocation">Truro, Nova Scotia, CA</span>
  </td>
</tr>
"""


def test_successfactors_parser_handles_site_segment_and_attribute_order() -> None:
    from job_hunt.services.gov_boards import parse_successfactors

    rows = parse_successfactors(
        NSHA_HTML, base="https://jobs.nshealth.ca", company="Nova Scotia Health"
    )

    assert len(rows) == 2
    assert rows[0]["title"] == "Systems Analyst - Information Management"
    assert rows[0]["company"] == "Nova Scotia Health"
    # The base must come from the tenant. Resolving against the hardcoded
    # provincial base produced live-looking rows that 404 on click.
    assert rows[0]["url"].startswith("https://jobs.nshealth.ca/nsha/job/")
    # Second row proves class-before-href is parsed too.
    assert rows[1]["title"] == "4th Class Power Engineer"


def test_ns_gov_parser_still_resolves_against_the_provincial_base() -> None:
    """The original entry point keeps its behaviour after generalisation."""
    from job_hunt.services.gov_boards import parse_ns_gov

    rows = parse_ns_gov(NS_HTML)
    assert rows[0]["company"] == "Government of Nova Scotia"
    assert rows[0]["url"].startswith("https://jobs.novascotia.ca/")


def test_gov_board_title_include_gates_noisy_whole_organisation_boards() -> None:
    """A health authority's body search returns clinical roles; titles gate them.

    Measured on the real boards: NSHA ``q=data`` returns Radiation Therapist,
    WRHA ``q=engineer`` returns "Engineer 5th Class". Tiers 4-7 skip the
    positive title filter downstream, so the board has to gate its own titles.
    """
    from job_hunt.services.gov_boards import scan_gov_boards

    def handler(request: httpx.Request) -> httpx.Response:
        body = NSHA_HTML if not request.url.params.get("startrow") else ""
        return httpx.Response(200, text=body, request=request)

    stats: dict[str, dict[str, object]] = {}
    rows = scan_gov_boards(
        {
            "enabled": True,
            "boards": {
                "nsha": {
                    "enabled": True,
                    "max_pages": 2,
                    "keywords": ["data"],
                    "title_include": ["analyst"],
                }
            },
        },
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
        sleep=lambda s: None,
        stats=stats,
    )

    assert [r["title"] for r in rows] == ["Systems Analyst - Information Management"]
    # The dropped count is reported, so a mis-tuned list shows up as a number
    # rather than as a board that quietly looks empty.
    assert stats["nsha"]["title_filtered"] == 1
    assert stats["nsha"]["collected"] == 2


def test_title_screen_keeps_everything_when_the_model_is_unavailable() -> None:
    """A board that silently returns nothing looks identical to one with no jobs.

    Measured 2026-08-15: the model gate is not deterministic across runs, so it
    is a recall aid, not an authority. When it cannot answer, every row goes
    through and the downstream negative list and human do the filtering.
    """
    from job_hunt.services.gov_boards import scan_gov_boards

    def handler(request: httpx.Request) -> httpx.Response:
        body = NSHA_HTML if not request.url.params.get("startrow") else ""
        return httpx.Response(200, text=body, request=request)

    rows = scan_gov_boards(
        {
            "enabled": True,
            "boards": {"nsha": {"enabled": True, "max_pages": 2, "title_screen": True}},
        },
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
        sleep=lambda s: None,
        title_screener=lambda titles: None,  # model unavailable
    )

    assert len(rows) == 2, "no answer must mean keep, never mean drop"


def test_title_screen_drops_what_the_model_rejects() -> None:
    from job_hunt.services.gov_boards import scan_gov_boards

    def handler(request: httpx.Request) -> httpx.Response:
        body = NSHA_HTML if not request.url.params.get("startrow") else ""
        return httpx.Response(200, text=body, request=request)

    stats: dict[str, dict[str, object]] = {}
    rows = scan_gov_boards(
        {
            "enabled": True,
            "boards": {"nsha": {"enabled": True, "max_pages": 2, "title_screen": True}},
        },
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
        sleep=lambda s: None,
        stats=stats,
        title_screener=lambda titles: {"Systems Analyst - Information Management"},
    )

    assert [r["title"] for r in rows] == ["Systems Analyst - Information Management"]
    assert stats["nsha"]["title_screen"] == "model"
    assert stats["nsha"]["title_filtered"] == 1


# ----- JobPosting.from_row (2026-09-03): the empty-title/missing-URL guard --
# ----- used to be copy-pasted at four call sites in scan.py, with a fifth ---
# ----- variant (jobbank's) that checked only the title. One function now. --


def test_from_row_drops_a_row_with_no_title() -> None:
    row = {"url": "https://example.com/job/1", "title": "  "}
    assert from_row(row, source_id="workday", portal="workday") is None


def test_from_row_drops_a_row_with_no_url() -> None:
    row = {"url": "", "title": "Data Analyst"}
    assert from_row(row, source_id="workday", portal="workday") is None
    assert from_row({"title": "Data Analyst"}, source_id="workday", portal="workday") is None


def test_from_row_builds_a_posting() -> None:
    row = {
        "url": "https://example.com/job/1",
        "title": "Data Analyst",
        "company": "Acme",
        "location": "Halifax, NS",
        "source": "workday Acme",
        "closes": "2026-09-30",
        "posted": "2026-08-06",
    }
    posting = from_row(row, source_id="workday", portal="workday")
    assert posting == JobPosting(
        url="https://example.com/job/1",
        title="Data Analyst",
        company="Acme",
        location="Halifax, NS",
        portal="workday",
        source="workday Acme",
        source_id="workday",
        closes="2026-09-30",
        posted="2026-08-06",
    )


def test_from_row_defaults_a_blank_company_to_unknown() -> None:
    posting = from_row(
        {"url": "https://example.com/job/1", "title": "Data Analyst", "company": "  "},
        source_id="jobbank",
        portal="jobbank",
    )
    assert posting is not None
    assert posting.company == "Unknown"


def test_from_row_leaves_a_source_specific_company_fallback_alone() -> None:
    """Regional boards default a missing company to a different message than
    the generic "Unknown" — the caller pre-fills the row, so ``from_row``'s
    own fallback never fires."""
    posting = from_row(
        {
            "url": "https://example.com/job/1",
            "title": "Data Analyst",
            "company": "Unknown (see posting)",
        },
        source_id="regional:digital_nova_scotia",
        portal="digital_nova_scotia",
    )
    assert posting is not None
    assert posting.company == "Unknown (see posting)"


# ----- SourceHealth.warnings() -----------------------------------------


def test_source_health_reports_failed_requests() -> None:
    health = SourceHealth(source_id="workday", ok=False, collected=3, errors=2)
    assert health.warnings() == [
        "workday: 2 failed request(s) — 3 postings may be an undercount, not a quiet source"
    ]


def test_source_health_reports_truncation_with_advertised_total() -> None:
    health = SourceHealth(
        source_id="gnwt", ok=True, collected=40, advertised=92, truncated=True
    )
    warnings = health.warnings()
    assert len(warnings) == 1
    assert "raise max_pages" in warnings[0]
    assert "source advertises 92" in warnings[0]


def test_source_health_silent_when_healthy() -> None:
    health = SourceHealth(source_id="mb_gov", ok=True, collected=59)
    assert health.warnings() == []


# ----- the two converted adapters: scan_workday_source / scan_adzuna_source -


def test_workday_source_returns_postings_and_health() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "total": 1,
                "jobPostings": [
                    {
                        "title": "Analyst",
                        "externalPath": "/job/a",
                        "postedOn": "Posted Today",
                    }
                ],
            },
            request=request,
        )

    result = scan_workday_source(
        {
            "enabled": True,
            "employers": [
                {"name": "OLG", "url": "https://olg.wd3.myworkdayjobs.com/en-US/Careers"}
            ],
        },
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
    )

    assert isinstance(result, SourceResult)
    assert len(result.postings) == 1
    posting = result.postings[0]
    assert isinstance(posting, JobPosting)
    assert posting.portal == "workday"
    assert posting.source_id == "workday"
    assert posting.source == "workday OLG"
    assert posting.company == "OLG"
    assert posting.posted  # "Posted Today" normalised to an ISO date
    assert result.health == SourceHealth(
        source_id="workday", ok=True, collected=1, advertised=1, errors=0
    )


def test_workday_source_health_counts_errors_across_employers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={}, request=request)

    result = scan_workday_source(
        {"enabled": True, "employers": [{"name": "X", "tenant": "t", "site": "s"}]},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
    )

    assert result.postings == []
    assert result.health.ok is False
    assert result.health.collected == 0
    assert result.health.errors == 1


def test_adzuna_source_returns_postings_and_health() -> None:
    class Cfg:
        enabled = True
        country = "ca"
        results_per_page = 50
        max_pages = 1
        max_days_old = 30
        delay_s = 0
        timeout_s = 5

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Data Analyst",
                        "redirect_url": "https://www.adzuna.ca/details/1",
                        "company": {"display_name": "Acme"},
                        "location": {"display_name": "Halifax"},
                        "created": "2026-08-01T00:00:00Z",
                    }
                ]
            },
            request=request,
        )

    result = scan_adzuna_source(
        Cfg(),
        ["data analyst"],
        app_id="i",
        app_key="k",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
    )

    assert isinstance(result, SourceResult)
    assert len(result.postings) == 1
    posting = result.postings[0]
    assert posting.portal == "adzuna"
    assert posting.source_id == "adzuna"
    assert posting.source == "adzuna data analyst"
    assert posting.posted == "2026-08-01"
    assert result.health == SourceHealth(
        source_id="adzuna", ok=True, collected=1, errors=0
    )


def test_adzuna_source_health_counts_errors_across_roles() -> None:
    class Cfg:
        enabled = True
        country = "ca"
        results_per_page = 50
        max_pages = 2
        max_days_old = 30
        delay_s = 0
        timeout_s = 5

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={}, request=request)

    result = scan_adzuna_source(
        Cfg(),
        ["AI Engineer"],
        app_id="i",
        app_key="k",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
    )

    assert result.postings == []
    assert result.health.ok is False
    assert result.health.collected == 0
    assert result.health.errors == 1


# ----- advertised/truncated (2026-09-03): both adapters measured the page --
# ----- budget running out but never reported it, so the design's own -------
# ----- truncation warning could never fire for these two sources. ----------


def test_workday_truncated_sweep_reports_advertised_and_warns() -> None:
    """Page budget (max_pages=1) runs out with more rows advertised than the
    single full page collected — must be flagged, not read as "done"."""

    def handler(request: httpx.Request) -> httpx.Response:
        postings = [
            {"title": f"Analyst {i}", "externalPath": f"/job/{i}"} for i in range(20)
        ]
        return httpx.Response(200, json={"total": 50, "jobPostings": postings}, request=request)

    stats: dict[str, dict[str, object]] = {}
    rows = scan_workday(
        {
            "enabled": True,
            "max_pages": 1,
            "employers": [{"name": "OLG", "tenant": "olg", "site": "Careers"}],
        },
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
        stats=stats,
    )

    assert len(rows) == 20
    assert stats["OLG"]["collected"] == 20
    assert stats["OLG"]["advertised"] == 50
    assert stats["OLG"]["truncated"] is True

    result = scan_workday_source(
        {
            "enabled": True,
            "max_pages": 1,
            "employers": [{"name": "OLG", "tenant": "olg", "site": "Careers"}],
        },
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
    )
    assert result.health.truncated is True
    assert result.health.advertised == 50
    warnings = result.health.warnings()
    assert any("raise max_pages" in w and "advertises 50" in w for w in warnings)


def test_workday_malformed_200_counts_as_error_not_empty() -> None:
    """A 200 whose body has no ``jobPostings`` key is truthy — it must not be
    read as "this employer has no openings"."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total": 5, "postings": []}, request=request)

    stats: dict[str, dict[str, object]] = {}
    rows = scan_workday(
        {"enabled": True, "employers": [{"name": "X", "tenant": "t", "site": "s"}]},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
        stats=stats,
    )

    assert rows == []
    assert stats["X"]["errors"] == 1
    assert stats["X"]["collected"] == 0


def test_workday_genuinely_empty_board_stays_ok() -> None:
    """A 200 with a real, empty ``jobPostings`` list is not an error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total": 0, "jobPostings": []}, request=request)

    stats: dict[str, dict[str, object]] = {}
    rows = scan_workday(
        {"enabled": True, "employers": [{"name": "X", "tenant": "t", "site": "s"}]},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
        stats=stats,
    )

    assert rows == []
    assert stats["X"]["errors"] == 0
    assert stats["X"]["truncated"] is False

    result = scan_workday_source(
        {"enabled": True, "employers": [{"name": "X", "tenant": "t", "site": "s"}]},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
    )
    assert result.health.ok is True
    assert result.health.warnings() == []


def test_adzuna_truncated_sweep_reports_advertised_and_warns() -> None:
    """Page budget (max_pages=1) runs out with more rows advertised than the
    single full page collected — must be flagged, not read as "done"."""

    class Cfg:
        enabled = True
        country = "ca"
        results_per_page = 50
        max_pages = 1
        max_days_old = 30
        delay_s = 0
        timeout_s = 5

    def handler(request: httpx.Request) -> httpx.Response:
        results = [
            {"title": f"Role {i}", "redirect_url": f"https://a/{i}"} for i in range(50)
        ]
        return httpx.Response(200, json={"results": results, "count": 100}, request=request)

    stats: dict[str, dict[str, object]] = {}
    rows = scan_adzuna(
        Cfg(),
        ["AI Engineer"],
        app_id="i",
        app_key="k",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
        stats=stats,
    )

    assert len(rows) == 50
    assert stats["AI Engineer"]["collected"] == 50
    assert stats["AI Engineer"]["advertised"] == 100
    assert stats["AI Engineer"]["truncated"] is True

    result = scan_adzuna_source(
        Cfg(),
        ["AI Engineer"],
        app_id="i",
        app_key="k",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
    )
    assert result.health.truncated is True
    assert result.health.advertised == 100
    warnings = result.health.warnings()
    assert any("raise max_pages" in w and "advertises 100" in w for w in warnings)


def test_adzuna_malformed_200_counts_as_error_not_empty() -> None:
    """A 200 whose body has no ``results`` key is truthy — it must not be
    read as "no matches for this role"."""

    class Cfg:
        enabled = True
        country = "ca"
        results_per_page = 50
        max_pages = 2
        max_days_old = 30
        delay_s = 0
        timeout_s = 5

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"count": 5, "matches": []}, request=request)

    stats: dict[str, dict[str, object]] = {}
    rows = scan_adzuna(
        Cfg(),
        ["AI Engineer"],
        app_id="i",
        app_key="k",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
        stats=stats,
    )

    assert rows == []
    assert stats["AI Engineer"]["errors"] == 1
    assert stats["AI Engineer"]["collected"] == 0


def test_adzuna_genuinely_empty_role_stays_ok() -> None:
    """A 200 with a real, empty ``results`` list is not an error."""

    class Cfg:
        enabled = True
        country = "ca"
        results_per_page = 50
        max_pages = 2
        max_days_old = 30
        delay_s = 0
        timeout_s = 5

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [], "count": 0}, request=request)

    stats: dict[str, dict[str, object]] = {}
    rows = scan_adzuna(
        Cfg(),
        ["AI Engineer"],
        app_id="i",
        app_key="k",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
        stats=stats,
    )

    assert rows == []
    assert stats["AI Engineer"]["errors"] == 0
    assert stats["AI Engineer"]["truncated"] is False

    result = scan_adzuna_source(
        Cfg(),
        ["AI Engineer"],
        app_id="i",
        app_key="k",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
    )
    assert result.health.ok is True
    assert result.health.warnings() == []
