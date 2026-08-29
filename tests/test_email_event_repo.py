from __future__ import annotations

from datetime import datetime, timezone

from job_hunt.models.events import ApplicationEvent
from job_hunt.repositories.email_event_repo import EmailEventRepository

GOOD = (
    '{"id":"evt_good","source":"gmail","event_type":"rejection",'
    '"event_time":"2026-08-20T00:00:00Z","company":"Acme","role":"Engineer"}'
)
# The shape that bricked every inbound command: values outside the schema.
BAD = (
    '{"id":"evt_bad","source":"email","event_type":"application_acknowledged",'
    '"event_time":"2026-08-10T00:00:00Z","company":"Delta","role":"Developer"}'
)


def repo_with(tmp_path, *lines) -> EmailEventRepository:
    path = tmp_path / "email-events.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return EmailEventRepository(path)


def test_one_bad_row_does_not_hide_the_good_ones(tmp_path):
    repo = repo_with(tmp_path, GOOD, BAD, GOOD.replace("evt_good", "evt_good2"))
    assert [event.id for event in repo.list(limit=100)] == ["evt_good", "evt_good2"]


def test_malformed_rows_are_reported_with_their_line_number(tmp_path):
    repo = repo_with(tmp_path, GOOD, BAD)
    malformed = repo.malformed()
    assert len(malformed) == 1
    assert malformed[0].line_number == 2
    assert "evt_bad" in malformed[0].raw


def test_healthy_file_reports_nothing_malformed(tmp_path):
    repo = repo_with(tmp_path, GOOD)
    assert repo.malformed() == []


def test_seen_message_ids_survives_a_bad_row(tmp_path):
    seen = GOOD.replace('"id":"evt_good"', '"id":"evt_seen","source_message_id":"m1"')
    repo = repo_with(tmp_path, BAD, seen)
    assert repo.seen_message_ids() == {"m1"}


def test_replace_line_repairs_a_malformed_row(tmp_path):
    repo = repo_with(tmp_path, GOOD, BAD)
    repo.replace_line(
        2,
        ApplicationEvent(
            id="evt_bad",
            source="manual",
            event_type="application_received",
            event_time=datetime(2026, 8, 10, tzinfo=timezone.utc),
            company="Delta",
            role="Developer",
        ),
    )
    assert repo.malformed() == []
    assert [event.id for event in repo.list(limit=100)] == ["evt_good", "evt_bad"]
