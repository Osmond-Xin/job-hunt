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
