"""The guards every new source adapter shares (job_hunt/services/row_guard.py)."""

from __future__ import annotations

import pytest

from job_hunt.services.row_guard import SKIP, absolute_url, guard_rows, safe_total


@pytest.mark.parametrize(
    "value, expected",
    [(1371, 1371), ("1,371", 1371), ("127.0", 127), (127.0, 127), ("0", 0), (0, 0),
     (None, None), ("", None), ("many", None), (-1, None), (True, None), (1.5, None), ([3], None)],
)
def test_a_total_is_read_or_reported_unknown_never_zero(value, expected):
    assert safe_total(value) == expected


@pytest.mark.parametrize(
    "value, base, expected",
    [("https://a.example/job/1", None, "https://a.example/job/1"),
     ("/view/123", "https://jobs.workable.com", "https://jobs.workable.com/view/123"),
     ("/view/123", None, ""), (123, None, ""), ("javascript:void(0)", None, ""),
     ("", None, ""), (None, "https://x.example", "")],
)
def test_only_an_absolute_http_url_is_accepted(value, base, expected):
    assert absolute_url(value, base) == expected


def test_bad_rows_are_counted_as_lost_and_skips_are_not():
    def parse(item):
        if item.get("mirror"):
            return SKIP
        return {"url": item["link"], "title": item.get("title", "")}

    items = [
        {"link": "https://a.example/1", "title": "Analyst"},
        None,                                    # not a dict
        {"title": "no link"},                    # KeyError inside the parser
        {"link": "javascript:void(0)", "title": "bad link"},
        {"link": "https://a.example/2", "title": ""},
        {"mirror": True},                        # dropped on purpose
        {"link": "/rel/3", "title": "Relative"},
    ]
    rows, lost = guard_rows(items, parse, base="https://a.example")
    assert [r["url"] for r in rows] == ["https://a.example/1", "https://a.example/rel/3"]
    assert lost == 4


def test_a_non_list_is_no_rows_and_no_loss():
    assert guard_rows(None, lambda item: item) == ([], 0)
