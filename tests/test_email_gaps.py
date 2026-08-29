from __future__ import annotations

import json
from pathlib import Path

from job_hunt.repositories.tracker_repo import TRACKER_HEADER, TrackerEntry, TrackerRepository
from job_hunt.services.email.gaps import find_gaps


def summary(**overrides) -> dict:
    row = {
        "message_id": "m1",
        "date": "2026-08-28T12:00:00+00:00",
        "sender": "no-reply@example.com",
        "subject": "Thanks for applying",
        "job_related": True,
        "category": "application_ack",
        "company": "Acme",
        "role": "Forward Deployed Engineer",
    }
    row.update(overrides)
    return row


def entry(number: int, company: str, role: str, status: str = "Applied") -> TrackerEntry:
    return TrackerEntry(
        number=number,
        date="2026-08-28",
        company=company,
        role=role,
        score="N/A",
        status=status,
        pdf="✅",
        report="",
        notes="",
    )


def build(tmp_path: Path, summaries: list[dict], entries: list[TrackerEntry]):
    summary_path = tmp_path / "email-summaries.jsonl"
    summary_path.write_text(
        "".join(json.dumps(row) + "\n" for row in summaries), encoding="utf-8"
    )
    tracker_path = tmp_path / "applications.md"
    tracker_path.write_text(TRACKER_HEADER, encoding="utf-8")
    tracker = TrackerRepository(tracker_path)
    for item in entries:
        tracker.append_entry(item)
    return summary_path, tracker


def run(tmp_path, summaries, entries, since="2026-08-01"):
    summary_path, tracker = build(tmp_path, summaries, entries)
    return find_gaps(since=since, summary_path=summary_path, tracker=tracker)


def test_ack_without_a_tracker_row_is_reported(tmp_path):
    gaps = run(tmp_path, [summary()], [])
    assert [(gap.kind, gap.company) for gap in gaps] == [("untracked", "Acme")]


def test_ack_with_a_matching_row_is_not_reported(tmp_path):
    gaps = run(tmp_path, [summary()], [entry(1, "Acme", "Forward Deployed Engineer")])
    assert gaps == []


def test_company_only_ack_matches_despite_the_legal_suffix(tmp_path):
    # Receipts say "Clariti Cloud Inc."; the tracker says "Clariti".
    gaps = run(
        tmp_path,
        [summary(company="Clariti Cloud Inc.", role=None)],
        [entry(1, "Clariti", "Forward Deployed Engineer")],
    )
    assert gaps == []


def test_company_only_ack_for_an_unknown_employer_is_still_reported(tmp_path):
    gaps = run(
        tmp_path,
        [summary(company="Cohere", role=None)],
        [entry(1, "Clariti", "Forward Deployed Engineer")],
    )
    assert [gap.company for gap in gaps] == ["Cohere"]


def test_rejection_against_a_row_still_marked_applied_is_stale(tmp_path):
    gaps = run(
        tmp_path,
        [summary(category="rejection")],
        [entry(1, "Acme", "Forward Deployed Engineer", status="Applied")],
    )
    assert [(gap.kind, gap.entry.number) for gap in gaps] == [("stale_status", 1)]


def test_rejection_against_a_settled_row_is_quiet(tmp_path):
    gaps = run(
        tmp_path,
        [summary(category="rejection")],
        [entry(1, "Acme", "Forward Deployed Engineer", status="Rejected")],
    )
    assert gaps == []


def test_noise_categories_and_old_mail_are_ignored(tmp_path):
    rows = [
        summary(category="other_job_related"),
        summary(job_related=False),
        summary(company=None),
        summary(date="2026-07-01T12:00:00+00:00", company="Old Corp"),
    ]
    assert run(tmp_path, rows, [], since="2026-08-01") == []


def test_repeat_acknowledgements_collapse_to_one_row(tmp_path):
    rows = [summary(message_id="m1"), summary(message_id="m2")]
    assert len(run(tmp_path, rows, [])) == 1
