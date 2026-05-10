from __future__ import annotations

from datetime import datetime, timezone

from job_hunt.models.events import ApplicationEvent
from job_hunt.repositories.email_event_repo import EmailEventRepository
from job_hunt.repositories.review_repo import ReviewRepository
from job_hunt.repositories.tracker_repo import TRACKER_HEADER, TrackerEntry, TrackerRepository
from job_hunt.services.email.reconcile import reconcile_email_events


def event(
    *,
    event_type: str,
    company: str,
    role: str,
    message_id: str,
    confidence: float = 0.9,
    needs_review: bool = False,
) -> ApplicationEvent:
    return ApplicationEvent(
        id=f"evt_{message_id}",
        source="gmail",
        source_message_id=message_id,
        source_thread_id=f"thread_{message_id}",
        event_type=event_type,  # type: ignore[arg-type]
        event_time=datetime.now(timezone.utc),
        company=company,
        role=role,
        sender="no-reply@example.com",
        subject=f"{company}: {role}",
        snippet="",
        evidence=["fixture"],
        confidence=confidence,
        needs_review=needs_review,
    )


def tracker_entry(number: int, company: str, role: str, status: str) -> TrackerEntry:
    return TrackerEntry(
        number=number,
        date="2026-04-28",
        company=company,
        role=role,
        score="N/A",
        status=status,
        pdf="❌",
        report="",
        notes="",
    )


def test_new_only_does_not_overwrite_existing_status(tmp_path) -> None:
    tracker_path = tmp_path / "applications.md"
    tracker_path.write_text(TRACKER_HEADER, encoding="utf-8")
    tracker = TrackerRepository(tracker_path)
    tracker.append_entry(tracker_entry(1, "Faire", "Senior Product Analytics Engineer", "Rejected"))

    event_repo = EmailEventRepository(tmp_path / "email-events.jsonl")
    event_repo.append(
        event(
            event_type="application_received",
            company="Faire",
            role="Senior Product Analytics Engineer",
            message_id="faire_received",
        )
    )

    result = reconcile_email_events(
        apply=True,
        import_new=True,
        update_existing=False,
        create_review=False,
        limit=100,
        event_repo=event_repo,
        tracker=tracker,
        review_repo=ReviewRepository(tmp_path / "review.jsonl"),
    )

    assert result.matched == 1
    assert result.updated == 0
    entries = tracker.parse()
    assert len(entries) == 1
    assert entries[0].status == "Rejected"


def test_same_company_different_role_imports_separate_tracker_row(tmp_path) -> None:
    tracker_path = tmp_path / "applications.md"
    tracker_path.write_text(TRACKER_HEADER, encoding="utf-8")
    tracker = TrackerRepository(tracker_path)
    tracker.append_entry(tracker_entry(1, "I Tech Enterprise Inc", "Gen AI Developer", "Rejected"))

    event_repo = EmailEventRepository(tmp_path / "email-events.jsonl")
    event_repo.append(
        event(
            event_type="rejection",
            company="I Tech Enterprise Inc",
            role="Java Backend Developer",
            message_id="itech_java",
        )
    )

    result = reconcile_email_events(
        apply=True,
        import_new=True,
        update_existing=False,
        create_review=False,
        limit=100,
        event_repo=event_repo,
        tracker=tracker,
        review_repo=ReviewRepository(tmp_path / "review.jsonl"),
    )

    assert result.matched == 0
    assert result.imported == 1
    entries = tracker.parse()
    assert [(entry.company, entry.role, entry.status) for entry in entries] == [
        ("I Tech Enterprise Inc", "Gen AI Developer", "Rejected"),
        ("I Tech Enterprise Inc", "Java Backend Developer", "Rejected"),
    ]


def test_low_confidence_event_can_be_skipped_without_review_file(tmp_path) -> None:
    event_repo = EmailEventRepository(tmp_path / "email-events.jsonl")
    event_repo.append(
        event(
            event_type="application_received",
            company="Unknown",
            role="Maybe Role",
            message_id="low_conf",
            confidence=0.5,
            needs_review=True,
        )
    )

    review_path = tmp_path / "review.jsonl"
    result = reconcile_email_events(
        apply=True,
        import_new=True,
        update_existing=False,
        create_review=False,
        limit=100,
        event_repo=event_repo,
        tracker=TrackerRepository(tmp_path / "applications.md"),
        review_repo=ReviewRepository(review_path),
    )

    assert result.review_created == 0
    assert result.skipped == 1
    assert not review_path.exists()
