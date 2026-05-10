from __future__ import annotations

from datetime import datetime, timezone

from job_hunt.models.events import ApplicationEvent
from job_hunt.repositories.email_decision_repo import EmailDecisionRepository
from job_hunt.repositories.email_event_repo import EmailEventRepository
from job_hunt.repositories.tracker_repo import TrackerRepository
from job_hunt.services.email.review import approve_email_event, ignore_email_event, list_review_candidates


def review_event(event_id: str = "evt_1") -> ApplicationEvent:
    return ApplicationEvent(
        id=event_id,
        source="gmail",
        source_message_id="msg_1",
        source_thread_id="thread_1",
        event_type="application_received",
        event_time=datetime.now(timezone.utc),
        company=None,
        role="Senior Software Developer",
        sender="no-reply@example.com",
        subject="Application received",
        snippet="",
        evidence=[],
        confidence=0.7,
        needs_review=True,
    )


def test_approve_email_event_imports_tracker_entry_and_hides_candidate(tmp_path) -> None:
    event_repo = EmailEventRepository(tmp_path / "events.jsonl")
    decision_repo = EmailDecisionRepository(tmp_path / "decisions.jsonl")
    tracker = TrackerRepository(tmp_path / "applications.md")
    event_repo.append(review_event())

    before = list_review_candidates(event_repo=event_repo, decision_repo=decision_repo)
    assert len(before) == 1

    result = approve_email_event(
        "evt_",
        company="Blue J",
        role="Senior Software Developer",
        event_repo=event_repo,
        decision_repo=decision_repo,
        tracker=tracker,
    )

    assert result.tracker_entry is not None
    assert result.tracker_entry.company == "Blue J"
    assert list_review_candidates(event_repo=event_repo, decision_repo=decision_repo) == []


def test_ignore_email_event_hides_candidate(tmp_path) -> None:
    event_repo = EmailEventRepository(tmp_path / "events.jsonl")
    decision_repo = EmailDecisionRepository(tmp_path / "decisions.jsonl")
    event_repo.append(review_event())

    ignore_email_event("evt_1", event_repo=event_repo, decision_repo=decision_repo)

    assert list_review_candidates(event_repo=event_repo, decision_repo=decision_repo) == []
