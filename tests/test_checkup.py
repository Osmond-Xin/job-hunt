from __future__ import annotations

import json
from datetime import date

from job_hunt.repositories.tracker_repo import TRACKER_HEADER, TrackerEntry, TrackerRepository
from job_hunt.services.checkup import unrecorded_artifacts


def tracker_with(tmp_path, *entries: TrackerEntry) -> TrackerRepository:
    path = tmp_path / "applications.md"
    path.write_text(TRACKER_HEADER, encoding="utf-8")
    repo = TrackerRepository(path)
    for entry in entries:
        repo.append_entry(entry)
    return repo


def entry(number: int, company: str, role: str, status: str) -> TrackerEntry:
    return TrackerEntry(
        number=number, date="2026-08-28", company=company, role=role,
        score="N/A", status=status, pdf="✅", report="", notes="",
    )


def artifact_dir(tmp_path, name: str, *, marker: dict | None = None):
    directory = tmp_path / "output" / name
    directory.mkdir(parents=True)
    (directory / "resume.pdf").write_bytes(b"%PDF-1.4")
    if marker is not None:
        (directory / ".tracker-row").write_text(json.dumps(marker), encoding="utf-8")
    return directory


SINCE = date(2026, 8, 1)


def test_materials_for_an_employer_with_no_row_are_reported(tmp_path):
    artifact_dir(tmp_path, "2026-08-28-netomi-agentic-ai-engineer")
    check = unrecorded_artifacts(
        since=SINCE, output_dir=tmp_path / "output", tracker=tracker_with(tmp_path)
    )
    assert not check.ok
    assert check.items == ["2026-08-28-netomi-agentic-ai-engineer"]


def test_a_marker_settles_it_even_when_the_slug_abbreviates_the_employer(tmp_path):
    # "ccl" shares no token with "Connor, Clark & Lunn Financial Group".
    artifact_dir(
        tmp_path, "2026-08-28-ccl-ai-solutions-engineer",
        marker={"tracker_row": 781, "status": "Applied"},
    )
    check = unrecorded_artifacts(
        since=SINCE, output_dir=tmp_path / "output",
        tracker=tracker_with(tmp_path, entry(781, "Connor, Clark & Lunn Financial Group",
                                             "AI Solutions Engineer", "Applied")),
    )
    assert check.ok, check.items


def test_a_marker_pointing_at_an_unsent_row_is_still_flagged(tmp_path):
    artifact_dir(
        tmp_path, "2026-08-28-ccl-ai-solutions-engineer",
        marker={"tracker_row": 781, "status": "Evaluated"},
    )
    check = unrecorded_artifacts(
        since=SINCE, output_dir=tmp_path / "output", tracker=tracker_with(tmp_path)
    )
    assert not check.ok
    assert "status Evaluated" in check.items[0]


def test_a_matching_row_that_was_sent_is_quiet(tmp_path):
    artifact_dir(tmp_path, "2026-08-28-netomi-agentic-ai-engineer")
    check = unrecorded_artifacts(
        since=SINCE, output_dir=tmp_path / "output",
        tracker=tracker_with(tmp_path, entry(1, "Netomi", "Staff Agentic AI Engineer", "Applied")),
    )
    assert check.ok, check.items


def test_a_directory_older_than_the_window_is_ignored(tmp_path):
    artifact_dir(tmp_path, "2026-07-01-netomi-agentic-ai-engineer")
    check = unrecorded_artifacts(
        since=SINCE, output_dir=tmp_path / "output", tracker=tracker_with(tmp_path)
    )
    assert check.ok


def test_a_directory_with_no_pdf_is_ignored(tmp_path):
    (tmp_path / "output" / "2026-08-28-netomi-notes").mkdir(parents=True)
    check = unrecorded_artifacts(
        since=SINCE, output_dir=tmp_path / "output", tracker=tracker_with(tmp_path)
    )
    assert check.ok
