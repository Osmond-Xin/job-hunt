"""The Canada filter's wrong drops of 2026-11-03, and the ledger that makes drops visible.

That day the filter dropped 4,976 rows, recording only the status. A re-run
with the locations captured found 457 that were Canadian or open to Canada.
Every location below is one of them, verbatim.
"""

from __future__ import annotations

import pytest

from job_hunt.services import scan as scan_module
from job_hunt.services.scan import (
    CANADA_ONLY_PORTALS,
    ScannedJob,
    ScanResult,
    _accept_jobs,
    _passes_canada_filter,
)


def _job(location: str, portal: str, url: str = "https://x.test/1") -> ScannedJob:
    return ScannedJob(title="AI Engineer", url=url, company="Acme", location=location, portal=portal, source=portal)


def _accept(jobs: list[ScannedJob], *, apply: bool = False) -> ScanResult:
    result = ScanResult()
    _accept_jobs(
        jobs, result, positives=["engineer"], negatives=[], include_non_canada=False,
        known_urls=set(), known_company_roles=set(), apply=apply,
    )
    return result


# Adzuna writes "<city>, <census division>"; GNWT writes the community alone.
@pytest.mark.parametrize(
    "location, portal",
    [("Victoria, Capital", "adzuna"), ("Laval, Laval region", "adzuna"),
     ("London, Middlesex", "adzuna"), ("Kamloops, Thompson-Nicola", "adzuna"),
     ("Fort Simpson", "gnwt"), ("Across the NWT", "gnwt"), ("Délįne", "gnwt")],
)
def test_a_canada_only_board_is_never_dropped_for_its_location(location, portal) -> None:
    assert portal in CANADA_ONLY_PORTALS
    assert _passes_canada_filter(location) is False, "case no longer exercises the portal exemption"
    result = _accept([_job(location, portal)])
    assert result.new_jobs == 1 and result.skipped_filtered == 0


def test_the_same_location_on_a_company_board_is_still_screened() -> None:
    result = _accept([_job("London, Middlesex", "greenhouse")])
    assert result.skipped_filtered == 1 and result.location_skips == ["London, Middlesex"]


@pytest.mark.parametrize(
    "location",
    ["2 Locations", "52 Locations", "Remote", "Remote, Global", "Remote, AMER", "Anywhere",
     "Worldwide", "Remote - Americas"],
)
def test_a_location_that_names_no_place_is_unknown_and_kept(location) -> None:
    assert _passes_canada_filter(location) is True


@pytest.mark.parametrize(
    "location", ["Remote US", "Remote - US", "Remote UK", "Remote, APAC", "Remote (West Coast)",
                 "Remote - Estonia", "Remote Poland"],
)
def test_a_remote_posting_that_names_a_foreign_region_is_still_dropped(location) -> None:
    assert _passes_canada_filter(location) is False


def test_a_row_once_dropped_for_location_is_not_a_known_url(tmp_path, monkeypatch) -> None:
    # Otherwise a fixed filter can never admit it: the next run calls it a duplicate.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data/scan-history.tsv").write_text(
        "url\tfirst_seen\tportal\ttitle\tcompany\tstatus\r\n"
        "https://a.test/dropped\t2026-11-03\tadzuna\tAI Engineer\tAcme\tskipped_location\r\n"
        "https://a.test/added\t2026-11-03\tadzuna\tAI Engineer\tAcme\tnew\r\n"
        "https://a.test/dup\t2026-11-03\tadzuna\tAI Engineer\tAcme\tskipped_duplicate\n",
        encoding="utf-8",
    )
    known = scan_module._known_urls()
    assert "https://a.test/dropped" not in known
    assert {"https://a.test/added", "https://a.test/dup"} <= known


def test_dropped_rows_are_written_to_the_ledger_with_their_location(tmp_path, monkeypatch) -> None:
    ledger = tmp_path / "scan-location-skips.tsv"
    monkeypatch.setattr(scan_module, "LOCATION_SKIP_PATH", ledger)
    monkeypatch.setattr(scan_module, "_location_skip_urls", None)
    monkeypatch.setattr(scan_module, "_append_scan_history", lambda job: None)
    job = _job("San Francisco, CA", "greenhouse")
    _accept([job], apply=True)
    _accept([job], apply=True)
    lines = ledger.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2 and lines[0].startswith("url\t")
    assert lines[1].endswith("\tSan Francisco, CA")
