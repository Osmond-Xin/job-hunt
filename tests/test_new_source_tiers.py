"""Tests for the quota-free source tiers: gov boards, Workday, Adzuna."""

from __future__ import annotations

import httpx
import pytest

from job_hunt.services.adzuna import parse_adzuna_results, scan_adzuna
from job_hunt.services.gov_boards import parse_gnwt, parse_ns_gov
from job_hunt.services.workday_boards import (
    parse_workday_response,
    resolve_workday_target,
    scan_workday,
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


def test_a_board_sweep_that_raises_is_reported_not_swallowed() -> None:
    """`except Exception: return []` made a broken tier look like an empty one."""
    import job_hunt.services.scan as scan_mod

    def boom(*_a, **_k):
        raise RuntimeError("parser blew up")

    original = scan_mod.scan_regional_boards
    scan_mod.scan_regional_boards = boom
    try:
        warnings: list[str] = []
        jobs = scan_mod._regional_board_scanned_jobs({"enabled": True}, warnings)
    finally:
        scan_mod.scan_regional_boards = original

    assert jobs == []
    assert warnings and "parser blew up" in warnings[0]


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
