"""Tier 10: the Hacker News "Who is hiring?" thread."""

from __future__ import annotations

import httpx

from job_hunt.services.hn_hiring import parse_comment, scan_hn_hiring_source

STORY = 49522897


def _hit(object_id: str, text: str, parent: int = STORY) -> dict:
    return {"objectID": object_id, "comment_text": text, "parent_id": parent, "created_at_i": 1788220800}


def test_the_header_line_becomes_company_role_and_location():
    row = parse_comment(_hit("1", "Acme (YC W24) | Forward Deployed Engineer | Toronto or Remote (Canada) | $120-160k<p>We build things."))
    assert row["company"] == "Acme (YC W24)"
    assert row["title"] == "Forward Deployed Engineer"
    assert "Toronto" in row["location"]
    assert row["url"] == "https://news.ycombinator.com/item?id=1"
    assert row["posted"] == "2026-09-01"


def test_several_roles_are_kept_and_a_header_with_no_role_keeps_its_fields():
    many = parse_comment(_hit("2", "Beta | Backend Engineer | Data Analyst | Vancouver | ONSITE"))
    assert many["title"] == "Backend Engineer / Data Analyst"
    none = parse_comment(_hit("3", "Gamma | Vancouver | ONSITE | https://gamma.test"))
    assert none["title"].startswith("Vancouver")  # the scan's title filter decides, and will say no


def test_a_comment_with_no_header_is_not_a_posting():
    assert parse_comment(_hit("4", "Is anyone hiring in Toronto?")) is None


def test_replies_are_skipped_and_a_posting_found_twice_is_kept_once():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search_by_date"):
            return httpx.Response(200, json={"hits": [{"objectID": str(STORY), "title": "Ask HN: Who is hiring? (September 2026)"}]})
        return httpx.Response(200, json={"hits": [
            _hit("10", "Acme | Software Engineer | Toronto"),
            _hit("11", "Thanks, applied!", parent=10),
        ]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = scan_hn_hiring_source({"enabled": True, "places": ["Canada", "Toronto"], "delay_s": 0}, client=client)
    assert [p.company for p in result.postings] == ["Acme"]
    assert result.health.ok and "September 2026" in result.health.note


def test_it_is_off_unless_the_config_turns_it_on():
    assert scan_hn_hiring_source(None).postings == []
    assert scan_hn_hiring_source({"enabled": False}).postings == []
