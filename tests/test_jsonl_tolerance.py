from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import filelock

from job_hunt.models.review import ReviewItem
from job_hunt.repositories.email_decision_repo import EmailDecisionRepository, EmailEventDecision
from job_hunt.repositories.jsonl_log import JsonlLog
from job_hunt.repositories.review_repo import ReviewRepository
from job_hunt.services.activity import ActivityEvent, activity_malformed, read_activity
from job_hunt.services.outreach import Contact, OutreachEvent, add_contact, add_event, list_contacts, list_events, malformed_contacts, malformed_events, update_event


# EmailDecisionRepository test data and fixtures
DECISION_GOOD = '{"id":"dec_good","event_id":"evt_1","decision":"approved","created_at":"2026-08-20T00:00:00Z","note":""}'
DECISION_BAD = '{"id":"dec_bad","event_id":"evt_2","decision":"rejected_bad_value","created_at":"2026-08-20T00:00:00Z","note":""}'


def decision_repo_with(tmp_path, *lines) -> EmailDecisionRepository:
    path = tmp_path / "decisions.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return EmailDecisionRepository(path)


def test_email_decision_one_bad_row_does_not_hide_good_ones(tmp_path):
    repo = decision_repo_with(tmp_path, DECISION_GOOD, DECISION_BAD, DECISION_GOOD.replace("dec_good", "dec_good2"))
    assert [d.id for d in repo.list(limit=100)] == ["dec_good", "dec_good2"]


def test_email_decision_malformed_rows_are_reported_with_line_number(tmp_path):
    repo = decision_repo_with(tmp_path, DECISION_GOOD, DECISION_BAD)
    malformed = repo.malformed()
    assert len(malformed) == 1
    assert malformed[0].line_number == 2
    assert "dec_bad" in malformed[0].raw


def test_email_decision_healthy_file_reports_nothing_malformed(tmp_path):
    repo = decision_repo_with(tmp_path, DECISION_GOOD)
    assert repo.malformed() == []


# ReviewRepository test data and fixtures
REVIEW_GOOD = '{"id":"rev_good","type":"email_match_low_confidence","status":"open","priority":"normal","created_at":"2026-08-20T00:00:00Z","summary":"Test review","proposed_action":{},"evidence":[]}'
REVIEW_BAD = '{"id":"rev_bad","type":"invalid_type","status":"open","priority":"normal","created_at":"2026-08-20T00:00:00Z","summary":"Bad review","proposed_action":{},"evidence":[]}'


def review_repo_with(tmp_path, *lines) -> ReviewRepository:
    path = tmp_path / "reviews.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ReviewRepository(path)


def test_review_one_bad_row_does_not_hide_good_ones(tmp_path):
    repo = review_repo_with(tmp_path, REVIEW_GOOD, REVIEW_BAD, REVIEW_GOOD.replace("rev_good", "rev_good2"))
    assert [r.id for r in repo.list(limit=100, status="all")] == ["rev_good", "rev_good2"]


def test_review_malformed_rows_are_reported_with_line_number(tmp_path):
    repo = review_repo_with(tmp_path, REVIEW_GOOD, REVIEW_BAD)
    malformed = repo.malformed()
    assert len(malformed) == 1
    assert malformed[0].line_number == 2
    assert "rev_bad" in malformed[0].raw


def test_review_healthy_file_reports_nothing_malformed(tmp_path):
    repo = review_repo_with(tmp_path, REVIEW_GOOD)
    assert repo.malformed() == []


# Activity test data and fixtures
ACTIVITY_GOOD = '{"id":"act_good","type":"application.created","level":"info","ts":"2026-08-20T00:00:00Z","summary":"Created","application_id":1,"run_id":"run_1","mode":"full","payload":{}}'
ACTIVITY_BAD = '{"id":"act_bad","type":"application.created","level":"invalid_level","ts":"2026-08-20T00:00:00Z","summary":"Bad","application_id":1,"run_id":"run_1","mode":"full","payload":{}}'


def activity_path_with(tmp_path, *lines) -> Path:
    path = tmp_path / "activity.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_activity_one_bad_row_does_not_hide_good_ones(tmp_path):
    path = activity_path_with(tmp_path, ACTIVITY_GOOD, ACTIVITY_BAD, ACTIVITY_GOOD.replace("act_good", "act_good2"))
    assert [a.id for a in read_activity(path, limit=100)] == ["act_good", "act_good2"]


def test_activity_malformed_rows_are_reported_with_line_number(tmp_path):
    path = activity_path_with(tmp_path, ACTIVITY_GOOD, ACTIVITY_BAD)
    malformed = activity_malformed(path)
    assert len(malformed) == 1
    assert malformed[0].line_number == 2
    assert "act_bad" in malformed[0].raw


def test_activity_healthy_file_reports_nothing_malformed(tmp_path):
    path = activity_path_with(tmp_path, ACTIVITY_GOOD)
    assert activity_malformed(path) == []


# Outreach test data and fixtures
CONTACT_GOOD = '{"id":"c_good","company":"Acme","name":"Alice","title":"Engineer","linkedin_url":"","email":"","source":"manual","relationship":"unknown","notes":"","created_at":"2026-08-20T00:00:00"}'
CONTACT_BAD = '{"id":"c_bad","company":"Beta","name":"Bob","title":"Manager","linkedin_url":"","email":"","source":"invalid_source","relationship":"unknown","notes":"","created_at":"2026-08-20T00:00:00"}'


def outreach_contacts_with(tmp_path, *lines) -> Path:
    path = tmp_path / "contacts.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_outreach_contacts_one_bad_row_does_not_hide_good_ones(tmp_path):
    path = outreach_contacts_with(tmp_path, CONTACT_GOOD, CONTACT_BAD, CONTACT_GOOD.replace("c_good", "c_good2"))
    assert [c.id for c in list_contacts(path)] == ["c_good", "c_good2"]


def test_outreach_contacts_malformed_rows_are_reported_with_line_number(tmp_path):
    path = outreach_contacts_with(tmp_path, CONTACT_GOOD, CONTACT_BAD)
    malformed = malformed_contacts(path)
    assert len(malformed) == 1
    assert malformed[0].line_number == 2
    assert "c_bad" in malformed[0].raw


def test_outreach_contacts_healthy_file_reports_nothing_malformed(tmp_path):
    path = outreach_contacts_with(tmp_path, CONTACT_GOOD)
    assert malformed_contacts(path) == []


# Outreach events
EVENT_GOOD = '{"id":"e_good","contact_id":"c_1","application_id":null,"company":"Acme","role":"Engineer","channel":"linkedin","status":"drafted","message_path":"","follow_up_at":"","notes":"","created_at":"2026-08-20T00:00:00","updated_at":"2026-08-20T00:00:00"}'
EVENT_BAD = '{"id":"e_bad","contact_id":"c_2","application_id":null,"company":"Beta","role":"Manager","channel":"invalid_channel","status":"drafted","message_path":"","follow_up_at":"","notes":"","created_at":"2026-08-20T00:00:00","updated_at":"2026-08-20T00:00:00"}'


def outreach_events_with(tmp_path, *lines) -> Path:
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_outreach_events_one_bad_row_does_not_hide_good_ones(tmp_path):
    path = outreach_events_with(tmp_path, EVENT_GOOD, EVENT_BAD, EVENT_GOOD.replace("e_good", "e_good2"))
    assert [e.id for e in list_events(path)] == ["e_good", "e_good2"]


def test_outreach_events_malformed_rows_are_reported_with_line_number(tmp_path):
    path = outreach_events_with(tmp_path, EVENT_GOOD, EVENT_BAD)
    malformed = malformed_events(path)
    assert len(malformed) == 1
    assert malformed[0].line_number == 2
    assert "e_bad" in malformed[0].raw


def test_outreach_events_healthy_file_reports_nothing_malformed(tmp_path):
    path = outreach_events_with(tmp_path, EVENT_GOOD)
    assert malformed_events(path) == []


def test_outreach_add_event_preserves_malformed_lines(tmp_path):
    """Malformed lines are never lost when add_event() is called."""
    path = outreach_events_with(tmp_path, EVENT_GOOD, EVENT_BAD)
    # Verify the bad line is there initially
    assert len(malformed_events(path)) == 1

    # Add a new event (should preserve the malformed line)
    new_event = OutreachEvent(contact_id="c_3", company="Gamma", role="Developer")
    add_event(new_event, path)

    # Verify: good events increased, malformed line still present
    events = list_events(path)
    assert len(events) == 2  # EVENT_GOOD + new_event
    malformed = malformed_events(path)
    assert len(malformed) == 1
    assert "e_bad" in malformed[0].raw


def test_outreach_add_contact_preserves_malformed_lines(tmp_path):
    """Malformed lines are never lost when add_contact() is called."""
    path = outreach_contacts_with(tmp_path, CONTACT_GOOD, CONTACT_BAD)
    # Verify the bad line is there initially
    assert len(malformed_contacts(path)) == 1

    # Add a new contact (should preserve the malformed line)
    new_contact = Contact(company="Gamma", name="Charlie")
    add_contact(new_contact, path)

    # Verify: good contacts increased, malformed line still present
    contacts = list_contacts(path)
    assert len(contacts) == 2  # CONTACT_GOOD + new_contact
    malformed = malformed_contacts(path)
    assert len(malformed) == 1
    assert "c_bad" in malformed[0].raw


def test_outreach_two_sequential_add_contact_calls_do_not_lose_the_first(tmp_path):
    """Two back-to-back read-modify-write cycles must not clobber each other."""
    path = tmp_path / "contacts.jsonl"
    add_contact(Contact(company="Acme", name="Alice"), path)
    add_contact(Contact(company="Beta", name="Bob"), path)
    assert [c.name for c in list_contacts(path)] == ["Alice", "Bob"]


def test_outreach_two_sequential_add_event_calls_do_not_lose_the_first(tmp_path):
    path = tmp_path / "events.jsonl"
    add_event(OutreachEvent(contact_id="c_1", company="Acme"), path)
    add_event(OutreachEvent(contact_id="c_2", company="Beta"), path)
    assert [e.company for e in list_events(path)] == ["Acme", "Beta"]


def _assert_lock_held_across_the_call(tmp_path_file: Path, call) -> None:
    """A true multi-process concurrency test isn't reliably deterministic, so
    the lock's presence and scope is verified by probing instead: from inside
    JsonlLog.read() -- the first step of the read-modify-write -- try to grab
    an independent lock on the same file. If the read-modify-write's own lock
    only covered the final write (the bug this guards against), the probe
    would succeed; since it must cover the whole cycle, the probe must fail.
    """
    lock_path = str(tmp_path_file) + ".lock"
    observed: list[bool] = []
    real_read = JsonlLog.read

    def spy_read(self):
        if str(self.path) == str(tmp_path_file):
            probe = filelock.FileLock(lock_path, timeout=0)
            try:
                probe.acquire()
                observed.append(True)  # lock was NOT held -- would be a bug
                probe.release()
            except filelock.Timeout:
                observed.append(False)  # lock was held throughout -- correct
        return real_read(self)

    with patch.object(JsonlLog, "read", spy_read):
        call()

    assert observed == [False]


def test_outreach_add_contact_holds_lock_across_the_whole_read_modify_write(tmp_path):
    path = tmp_path / "contacts.jsonl"
    _assert_lock_held_across_the_call(path, lambda: add_contact(Contact(company="Acme"), path))


def test_outreach_add_event_holds_lock_across_the_whole_read_modify_write(tmp_path):
    path = tmp_path / "events.jsonl"
    _assert_lock_held_across_the_call(path, lambda: add_event(OutreachEvent(contact_id="c_1", company="Acme"), path))


def test_outreach_update_event_holds_lock_across_the_whole_read_modify_write(tmp_path):
    path = tmp_path / "events.jsonl"
    event = add_event(OutreachEvent(contact_id="c_1", company="Acme"), path)
    _assert_lock_held_across_the_call(path, lambda: update_event(event.id, status="sent", path=path))
