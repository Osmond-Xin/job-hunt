"""The 2026-09-27 tiers reach scan_portals, each on the right side of the
positive title filter: SaskJobs is NOC-filtered like Job Bank and waives it;
the employer boards, WorkPEI and YC are general boards and keep it.
"""

from __future__ import annotations

from pathlib import Path

import yaml

import job_hunt.services.scan as scan_module
from job_hunt.models.posting import JobPosting, SourceHealth, SourceResult


def _source(source_id: str, *titles: str, errors: int = 0):
    def scan(config, *_args, **_kwargs):
        postings = [
            JobPosting(url=f"https://{source_id}.example/{i}", title=title, company="Co",
                       portal=source_id, source=source_id, source_id=source_id,
                       location="Regina, Saskatchewan")
            for i, title in enumerate(titles)
        ]
        return SourceResult(postings=postings, health=SourceHealth(source_id=source_id, ok=not errors, errors=errors))

    return scan


def test_new_tiers_are_scanned_with_the_right_title_filter(tmp_path: Path, monkeypatch) -> None:
    portals = tmp_path / "portals.yml"
    portals.write_text(
        yaml.safe_dump({"title_filter": {"positive": ["analyst"], "negative": []}}), encoding="utf-8"
    )
    monkeypatch.setattr(scan_module, "_known_urls", lambda: set())
    monkeypatch.setattr(scan_module, "_known_company_roles", lambda: set())
    # "Programmer" is not on the positive list: only the NOC-filtered tier keeps it.
    monkeypatch.setattr(scan_module, "scan_saskjobs_source", _source("saskjobs", "Programmer"))
    monkeypatch.setattr(scan_module, "scan_employer_boards_source",
                        _source("employer_boards", "Programmer", "Systems Analyst"))
    monkeypatch.setattr(scan_module, "scan_workpei_source", _source("workpei", "Programmer", errors=2))
    monkeypatch.setattr(scan_module, "scan_yc_companies_source", _source("yc_companies", "Data Analyst"))

    result = scan_module.scan_portals(config_path=portals, mode="full")

    assert sorted((job.portal, job.title) for job in result.jobs) == [
        ("employer_boards", "Systems Analyst"),
        ("saskjobs", "Programmer"),
        ("yc_companies", "Data Analyst"),
    ]
    assert any(w.startswith("workpei: 2 failed request(s)") for w in result.errors)


def test_a_province_code_field_places_a_posting_in_canada() -> None:
    """The Alberta board writes "Taber, AB" and bare "AB"; neither names a
    place the Canada gate knew, so a third of the board was dropped."""
    from job_hunt.services.scan import _passes_canada_filter

    for location in ("Taber, AB", "AB", "Lethbridge County, AB", "Truro, NS, Canada"):
        assert _passes_canada_filter(location) is True
    for location in ("Seattle, WA", "Austin, TX, US", "Remote, on-site in Boston"):
        assert _passes_canada_filter(location) is False


def test_a_province_code_that_is_also_foreign_does_not_place_a_posting() -> None:
    """Review 2026-09-27: the bare-code rule accepted these."""
    from job_hunt.services.scan import _passes_canada_filter

    for location in ("Darwin, NT, Australia", "Monterrey, NL, Mexico", "Amsterdam, NL",
                     "Lima, PE", "Bratislava, SK", "Munich, BC, Germany"):
        assert _passes_canada_filter(location) is False, location
    # A code followed by Canada, or a capital placed by name, still passes.
    for location in ("Taber, AB, CA", "Moncton, NB, Canada", "Whitehorse, YT", "Regina, SK"):
        assert _passes_canada_filter(location) is True, location


def test_a_province_code_followed_by_a_postal_code_places_a_posting() -> None:
    """Second review 2026-09-27: "Taber, AB T1G 2C9" did not match the rule and
    Taber is in no place list, so the posting was dropped."""
    from job_hunt.services.scan import _passes_canada_filter

    assert _passes_canada_filter("Taber, AB T1G 2C9") is True
    assert _passes_canada_filter("Lethbridge County, AB T1J4L1, Canada") is True
    # A Canadian city name the blocked list shares with London, England.
    assert _passes_canada_filter("London, ON") is True


def test_saskjobs_is_filtered_by_job_banks_noc_codes(tmp_path: Path, monkeypatch) -> None:
    portals = tmp_path / "portals.yml"
    portals.write_text(
        yaml.safe_dump({"jobbank_direct": {"enabled": False, "noc_codes": ["21221"]},
                        "saskjobs": {"enabled": True}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(scan_module, "_known_urls", lambda: set())
    monkeypatch.setattr(scan_module, "_known_company_roles", lambda: set())
    received: list = []

    def fake(config, noc_codes):
        received.append((config, noc_codes))
        return SourceResult(postings=[], health=SourceHealth(source_id="saskjobs", ok=True))

    monkeypatch.setattr(scan_module, "scan_saskjobs_source", fake)
    scan_module.scan_portals(config_path=portals, mode="full")
    assert received == [({"enabled": True}, ["21221"])]
