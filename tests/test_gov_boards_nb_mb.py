"""New Brunswick (Oracle REST) and Manitoba (RSS) public-sector board parsers.

Both were added because aggregators cover provincial governments badly: GNB
competition 16958 reached Adzuna only under the wrong employer name.
"""

from __future__ import annotations

from job_hunt.services.gov_boards import (
    NB_JOB_URL,
    nb_total,
    parse_mb_gov,
    parse_nb_gov,
    scan_gov_boards,
)

NB_PAYLOAD = {
    "items": [
        {
            "TotalJobsCount": 27,
            "requisitionList": [
                {
                    "Id": "16958",
                    "Title": "AI Integration and Automation Specialist",
                    "PrimaryLocation": "NB, Canada",
                    "PostedDate": "2026-08-07",
                },
                {
                    "Id": "17098",
                    "Title": "Operations Worker I",
                    "PrimaryLocation": "Minto, NB, Canada",
                    "PostedDate": "2026-08-12",
                    "PostingEndDate": "2026-08-25",
                },
                {"Id": "", "Title": "no id, must be dropped"},
                {"Id": "999", "Title": "   "},
            ],
        }
    ]
}

MB_FEED = """<?xml version="1.0" encoding="ISO-8859-1"?><rss version="2.0"><channel>
<title>Manitoba Government Job Opportunities - Information Technology</title>
<item>
<title>Data Engineer&#13;</title>
<link>http://jobsearch.gov.mb.ca/jow/advancedResult.action?postingType=0&amp;adNo=45511</link>
<description>&lt;strong&gt;Ad No&lt;/strong&gt;: 45511&lt;br /&gt;&lt;strong&gt;Job Type(s)&lt;/strong&gt;: Regular/full-time&lt;br /&gt;&lt;strong&gt;Location(s)&lt;/strong&gt;: Winnipeg MB&lt;br /&gt;&lt;strong&gt;Salary(s)&lt;/strong&gt;: $75,000&lt;br /&gt;&lt;strong&gt;Closing Date&lt;/strong&gt;: 2026-08-20&lt;br /&gt;</description>
</item>
<item><title></title><link>http://example.invalid/blank</link></item>
</channel></rss>"""


def test_nb_rows_carry_a_reachable_posting_url():
    rows = parse_nb_gov(NB_PAYLOAD)
    assert [r["title"] for r in rows] == [
        "AI Integration and Automation Specialist",
        "Operations Worker I",
    ]
    assert rows[0]["url"] == NB_JOB_URL.format(job_id="16958")
    assert rows[0]["company"] == "Government of New Brunswick"
    assert rows[0]["location"] == "NB, Canada"


def test_nb_competitions_without_an_end_date_are_normal():
    # Most GNB competitions run "until filled"; an empty closes field is not a defect.
    rows = parse_nb_gov(NB_PAYLOAD)
    assert rows[0]["closes"] == ""
    assert rows[1]["closes"] == "2026-08-25"


def test_nb_total_and_empty_payloads():
    assert nb_total(NB_PAYLOAD) == 27
    assert nb_total({}) == 0
    assert nb_total(None) == 0
    assert parse_nb_gov(None) == []
    assert parse_nb_gov({"items": []}) == []


def test_mb_pulls_the_labelled_fields_out_of_the_item_body():
    rows = parse_mb_gov(MB_FEED)
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "Data Engineer"
    assert row["company"] == "Government of Manitoba"
    assert row["location"] == "Winnipeg MB"
    assert row["salary"] == "$75,000"
    assert row["closes"] == "2026-08-20"
    # The feed escapes its own query string; a row is useless if that survives.
    assert "&amp;" not in row["url"]
    assert row["url"].endswith("adNo=45511")


def test_mb_handles_an_empty_feed():
    assert parse_mb_gov("") == []
    assert parse_mb_gov("<rss><channel></channel></rss>") == []


def test_disabled_boards_are_not_fetched():
    def explode(*_args, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError("a disabled board was fetched")

    config = {
        "enabled": True,
        "boards": {"nb_gov": {"enabled": False}, "mb_gov": {"enabled": False}},
    }

    class Client:
        get = staticmethod(explode)

        def close(self):
            pass

    assert scan_gov_boards(config, client=Client(), sleep=lambda _s: None) == []


# --- coverage accounting --------------------------------------------------
# The bug these guard against: a keyword-scoped or page-limited sweep returns
# rows, looks healthy, and silently omits most of the board. Manitoba lost 49
# of 64 postings that way and nothing in the output said so.


def test_truncation_is_reported_when_the_page_budget_runs_out(monkeypatch):
    import job_hunt.services.gov_boards as mod

    page = [{"url": f"u{i}", "title": f"job {i}"} for i in range(25)]

    # Every page is full: the walk ends because the budget ran out, not
    # because the board did, and the board advertises far more than it gave.
    monkeypatch.setattr(mod, "parse_gnwt", lambda body: page if body == "PAGE" else [])
    monkeypatch.setattr(mod, "gnwt_total", lambda body: 500)
    monkeypatch.setattr(mod, "_get", lambda client, url, params: "PAGE")

    stats: dict = {}
    rows = mod.scan_gov_boards(
        {
            "enabled": True,
            "delay_s": 0,
            "boards": {"gnwt": {"enabled": True, "max_pages": 2}},
        },
        sleep=lambda _s: None,
        stats=stats,
    )
    assert len(rows) == 25  # de-duplicated by url across both pages
    assert stats["gnwt"]["truncated"] is True
    assert stats["gnwt"]["advertised"] == 500
    assert stats["gnwt"]["collected"] == 25


def test_a_completed_walk_is_not_flagged(monkeypatch):
    import job_hunt.services.gov_boards as mod

    rows_page = [{"url": "u1", "title": "job"}]
    calls = {"n": 0}

    def get(_client, _url, _params):
        calls["n"] += 1
        return "PAGE" if calls["n"] == 1 else ""

    monkeypatch.setattr(mod, "_get", get)
    monkeypatch.setattr(mod, "parse_gnwt", lambda body: rows_page if body == "PAGE" else [])
    monkeypatch.setattr(mod, "gnwt_total", lambda body: 1)

    stats: dict = {}
    mod.scan_gov_boards(
        {"enabled": True, "delay_s": 0, "boards": {"gnwt": {"enabled": True, "max_pages": 4}}},
        sleep=lambda _s: None,
        stats=stats,
    )
    assert stats["gnwt"]["truncated"] is False
    assert stats["gnwt"]["mode"] == "whole-board"


def test_keyword_mode_is_labelled_so_a_partial_sweep_is_visible(monkeypatch):
    import job_hunt.services.gov_boards as mod

    monkeypatch.setattr(mod, "_get", lambda *_a, **_k: "PAGE")
    monkeypatch.setattr(mod, "parse_gnwt", lambda _b: [{"url": "u", "title": "t"}])
    monkeypatch.setattr(mod, "gnwt_total", lambda _b: 92)

    stats: dict = {}
    mod.scan_gov_boards(
        {
            "enabled": True,
            "delay_s": 0,
            "boards": {"gnwt": {"enabled": True, "max_pages": 2, "keywords": ["data"]}},
        },
        sleep=lambda _s: None,
        stats=stats,
    )
    assert stats["gnwt"]["mode"] == "keyword"


# --- what the red-team pass on this scraper actually found -----------------


def test_french_postings_keep_their_location_and_closing_date():
    # The Manitoba feed is bilingual (59 EN / 5 FR when measured). Keying on the
    # English label alone silently emptied both fields for every French posting.
    feed = """<rss><channel><item>
<title>G&#233;rant(e) bilingue</title>
<link>http://jobsearch.gov.mb.ca/jow/advancedResult.action?adNo=1</link>
<description>&lt;strong&gt;Lieu(s)&lt;/strong&gt;: Winnipeg (Manitoba)&lt;br /&gt;&lt;strong&gt;Date de cl&#244;ture&lt;/strong&gt;: 2026-08-19&lt;br /&gt;</description>
</item></channel></rss>"""
    row = parse_mb_gov(feed)[0]
    assert row["location"] == "Winnipeg (Manitoba)"
    assert row["closes"] == "2026-08-19"


def test_a_gnwt_row_without_an_h3_title_is_still_returned():
    # A posting the parser cannot title disappears with no trace, and the
    # heading tag is the likeliest casualty of a Drupal upgrade.
    from job_hunt.services.gov_boards import parse_gnwt

    page = (
        '<a href="https://www.gov.nt.ca/careers/en/job/28121" '
        'class="job-search-result-link"><h2>Project Manager</h2></a>'
    )
    rows = parse_gnwt(page)
    assert len(rows) == 1
    assert "Project Manager" in rows[0]["title"]


def test_truncation_comes_from_the_boards_own_count_not_the_page_budget(monkeypatch):
    import job_hunt.services.gov_boards as mod

    page = [{"url": f"u{i}", "title": f"job {i}"} for i in range(25)]
    monkeypatch.setattr(mod, "_get", lambda *_a, **_k: "PAGE")
    monkeypatch.setattr(mod, "parse_gnwt", lambda body: page if body == "PAGE" else [])
    # The board says it has 500 postings; the sweep saw 25 distinct ones.
    monkeypatch.setattr(mod, "gnwt_total", lambda _b: 500)

    stats: dict = {}
    mod.scan_gov_boards(
        {"enabled": True, "delay_s": 0, "boards": {"gnwt": {"enabled": True, "max_pages": 6}}},
        sleep=lambda _s: None,
        stats=stats,
    )
    assert stats["gnwt"]["truncated"] is True
    assert stats["gnwt"]["collected"] == 25
    assert stats["gnwt"]["advertised"] == 500


def test_a_board_that_is_down_is_not_reported_as_a_board_that_is_empty(monkeypatch):
    import job_hunt.services.gov_boards as mod

    # _get collapses transport errors and non-200s into "", which is exactly
    # what an empty board returns. The error count is the only thing that
    # separates "nothing to find" from "we never reached it".
    monkeypatch.setattr(mod, "_get", lambda *_a, **_k: "")

    stats: dict = {}
    rows = mod.scan_gov_boards(
        {"enabled": True, "delay_s": 0, "boards": {"ns_gov": {"enabled": True, "max_pages": 3}}},
        sleep=lambda _s: None,
        stats=stats,
    )
    assert rows == []
    assert stats["ns_gov"]["collected"] == 0
    assert stats["ns_gov"]["errors"] >= 1


def test_gnwt_relative_age_becomes_a_date() -> None:
    """GNWT prints "17 hours ago", not a date, so freshness was always blank."""
    import datetime as dt

    from job_hunt.services.gov_boards import _age_to_iso

    today = dt.date(2026, 8, 13)
    assert _age_to_iso("Posted 50 min ago", today) == "2026-08-13"
    assert _age_to_iso("Posted 17 hours ago", today) == "2026-08-13"
    assert _age_to_iso("Posted 3 days ago", today) == "2026-08-10"
    assert _age_to_iso("Posted 2 weeks ago", today) == "2026-07-30"
    # Anything that is not an interval stays empty rather than becoming today.
    assert _age_to_iso("", today) == ""
    assert _age_to_iso("Posted recently", today) == ""


def test_manitoba_carries_both_of_its_published_dates() -> None:
    from job_hunt.services.gov_boards import parse_mb_gov

    feed = (
        "<item><title>Access Analyst</title>"
        "<link>http://jobsearch.gov.mb.ca/jow/x?ID=1</link>"
        "<description>&lt;strong&gt;Location(s)&lt;/strong&gt;: Winnipeg MB&lt;br /&gt;"
        "&lt;strong&gt;Closing Date&lt;/strong&gt;: 2026-08-25&lt;br /&gt;"
        "&lt;strong&gt;Posted Date&lt;/strong&gt;: 2026-07-30&lt;br /&gt;</description></item>"
    )
    row = parse_mb_gov(feed)[0]
    assert row["closes"] == "2026-08-25"
    assert row["posted"] == "2026-07-30"


def test_new_brunswick_carries_the_posted_date_the_list_api_does_publish() -> None:
    """PostingEndDate is always null in the list response; PostedDate is not."""
    from job_hunt.services.gov_boards import parse_nb_gov

    payload = {
        "items": [
            {
                "requisitionList": [
                    {"Id": "17104", "Title": "Senior Web Developer",
                     "PrimaryLocation": "Fredericton, NB, Canada",
                     "PostedDate": "2026-08-10", "PostingEndDate": None}
                ]
            }
        ]
    }
    row = parse_nb_gov(payload)[0]
    assert row["posted"] == "2026-08-10"
    assert row["closes"] == ""
