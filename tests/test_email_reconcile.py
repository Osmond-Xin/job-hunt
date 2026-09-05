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


def test_alias_resolves_the_mail_side_brand_to_the_tracker_row(tmp_path) -> None:
    # "Seon" (the sender's brand in the mail) shares no distinctive token with
    # "Safe Fleet" (the tracker's employer name) — only the alias connects them.
    tracker_path = tmp_path / "applications.md"
    tracker_path.write_text(TRACKER_HEADER, encoding="utf-8")
    tracker = TrackerRepository(tracker_path)
    tracker.append_entry(tracker_entry(1, "Safe Fleet", "Safety Engineer", "Applied"))

    event_repo = EmailEventRepository(tmp_path / "email-events.jsonl")
    event_repo.append(
        event(
            event_type="rejection",
            company="Seon",
            role="Safety Engineer",
            message_id="seon_rejection",
        )
    )

    result = reconcile_email_events(
        apply=True,
        import_new=True,
        update_existing=True,
        create_review=False,
        limit=100,
        event_repo=event_repo,
        tracker=tracker,
        review_repo=ReviewRepository(tmp_path / "review.jsonl"),
        aliases={"Seon": "Safe Fleet"},
    )

    assert result.matched == 1
    assert result.updated == 1
    assert result.imported == 0
    entries = tracker.parse()
    assert len(entries) == 1
    assert entries[0].status == "Rejected"


def test_without_the_alias_the_brand_name_reads_as_a_new_employer(tmp_path) -> None:
    """Regression guard for the alias test above: without the alias, "Seon"
    and "Safe Fleet" genuinely fail the distinctive-token gate, so the event
    imports as a new row instead of updating #1 — the alias, not a fuzzy
    coincidence, is what makes the match above work."""
    tracker_path = tmp_path / "applications.md"
    tracker_path.write_text(TRACKER_HEADER, encoding="utf-8")
    tracker = TrackerRepository(tracker_path)
    tracker.append_entry(tracker_entry(1, "Safe Fleet", "Safety Engineer", "Applied"))

    event_repo = EmailEventRepository(tmp_path / "email-events.jsonl")
    event_repo.append(
        event(
            event_type="rejection",
            company="Seon",
            role="Safety Engineer",
            message_id="seon_rejection",
        )
    )

    result = reconcile_email_events(
        apply=True,
        import_new=True,
        update_existing=True,
        create_review=False,
        limit=100,
        event_repo=event_repo,
        tracker=tracker,
        review_repo=ReviewRepository(tmp_path / "review.jsonl"),
        aliases={},
    )

    assert result.matched == 0
    assert result.imported == 1
    entries = tracker.parse()
    assert [(e.company, e.status) for e in entries] == [
        ("Safe Fleet", "Applied"),
        ("Seon", "Rejected"),
    ]


def test_backward_transition_does_not_write(tmp_path) -> None:
    """A Responded row (rank 4) must not be demoted by an older "Applied"
    (rank 3) event -- the general backwards case, distinct from the
    Rejected/Discarded regression below."""
    tracker_path = tmp_path / "applications.md"
    tracker_path.write_text(TRACKER_HEADER, encoding="utf-8")
    tracker = TrackerRepository(tracker_path)
    tracker.append_entry(tracker_entry(1, "Faire", "Senior Product Analytics Engineer", "Responded"))

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
        import_new=False,
        update_existing=True,
        create_review=False,
        limit=100,
        event_repo=event_repo,
        tracker=tracker,
        review_repo=ReviewRepository(tmp_path / "review.jsonl"),
    )

    assert result.matched == 1
    assert result.updated == 0
    assert result.regressed == 1
    entries = tracker.parse()
    assert entries[0].status == "Responded"


def test_rejected_row_stays_rejected_against_an_older_application_received_event(tmp_path) -> None:
    """Regression guard for the live-corpus finding: an older
    "we received your application" event must not reopen a row the tracker
    has already marked Rejected, even though Applied (rank 3) numerically
    outranks Rejected (rank 1) in tracker_ops._STATUS_RANK."""
    tracker_path = tmp_path / "applications.md"
    tracker_path.write_text(TRACKER_HEADER, encoding="utf-8")
    tracker = TrackerRepository(tracker_path)
    tracker.append_entry(tracker_entry(1, "Tactable", "Data Scientist", "Rejected"))

    event_repo = EmailEventRepository(tmp_path / "email-events.jsonl")
    event_repo.append(
        event(
            event_type="application_received",
            company="Tactable",
            role="Data Scientist",
            message_id="tactable_received",
        )
    )

    result = reconcile_email_events(
        apply=True,
        import_new=False,
        update_existing=True,
        create_review=False,
        limit=100,
        event_repo=event_repo,
        tracker=tracker,
        review_repo=ReviewRepository(tmp_path / "review.jsonl"),
    )

    assert result.matched == 1
    assert result.updated == 0
    assert result.regressed == 1
    entries = tracker.parse()
    assert entries[0].status == "Rejected"


def test_forward_transition_writes(tmp_path) -> None:
    tracker_path = tmp_path / "applications.md"
    tracker_path.write_text(TRACKER_HEADER, encoding="utf-8")
    tracker = TrackerRepository(tracker_path)
    tracker.append_entry(tracker_entry(1, "Faire", "Senior Product Analytics Engineer", "Applied"))

    event_repo = EmailEventRepository(tmp_path / "email-events.jsonl")
    event_repo.append(
        event(
            event_type="interview",
            company="Faire",
            role="Senior Product Analytics Engineer",
            message_id="faire_interview",
        )
    )

    result = reconcile_email_events(
        apply=True,
        import_new=False,
        update_existing=True,
        create_review=False,
        limit=100,
        event_repo=event_repo,
        tracker=tracker,
        review_repo=ReviewRepository(tmp_path / "review.jsonl"),
    )

    assert result.matched == 1
    assert result.updated == 1
    assert result.regressed == 0
    entries = tracker.parse()
    assert entries[0].status == "Interview"


def test_dry_run_count_equals_applied_count_for_the_same_input(tmp_path) -> None:
    tracker_path = tmp_path / "applications.md"
    tracker_path.write_text(TRACKER_HEADER, encoding="utf-8")
    tracker = TrackerRepository(tracker_path)
    tracker.append_entry(tracker_entry(1, "Faire", "Senior Product Analytics Engineer", "Applied"))

    event_repo = EmailEventRepository(tmp_path / "email-events.jsonl")
    event_repo.append(
        event(
            event_type="interview",
            company="Faire",
            role="Senior Product Analytics Engineer",
            message_id="faire_interview",
        )
    )

    kwargs = dict(
        import_new=False,
        update_existing=True,
        create_review=False,
        limit=100,
        event_repo=event_repo,
        tracker=tracker,
        review_repo=ReviewRepository(tmp_path / "review.jsonl"),
    )

    dry_run = reconcile_email_events(apply=False, **kwargs)
    assert dry_run.updated == 1
    entries = tracker.parse()
    assert entries[0].status == "Applied"  # dry run must not write

    applied = reconcile_email_events(apply=True, **kwargs)
    assert applied.updated == dry_run.updated == 1
    entries = tracker.parse()
    assert entries[0].status == "Interview"


def test_scan_output_distinguishes_partial_from_complete(tmp_path) -> None:
    event_repo = EmailEventRepository(tmp_path / "email-events.jsonl")
    for i in range(5):
        event_repo.append(
            event(
                event_type="rejection",
                company=f"Company{i}",
                role="Role",
                message_id=f"msg{i}",
            )
        )

    tracker = TrackerRepository(tmp_path / "applications.md")
    review_repo = ReviewRepository(tmp_path / "review.jsonl")

    partial = reconcile_email_events(
        apply=False,
        limit=2,
        import_new=False,
        update_existing=False,
        create_review=False,
        event_repo=event_repo,
        tracker=tracker,
        review_repo=review_repo,
    )
    assert partial.scanned == 2
    assert partial.total_available == 5

    complete = reconcile_email_events(
        apply=False,
        limit=100,
        import_new=False,
        update_existing=False,
        create_review=False,
        event_repo=event_repo,
        tracker=tracker,
        review_repo=review_repo,
    )
    assert complete.scanned == 5
    assert complete.total_available == 5


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
