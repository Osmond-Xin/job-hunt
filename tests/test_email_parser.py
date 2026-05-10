from __future__ import annotations

from datetime import datetime, timezone

from job_hunt.services.email.message_parser import ParsedEmail, classify_email_event


def parsed(subject: str, body: str, sender: str = "no-reply@example.com") -> ParsedEmail:
    return ParsedEmail(
        message_id="msg_1",
        thread_id="thread_1",
        sender=sender,
        subject=subject,
        snippet=body[:240],
        body=body,
        date=datetime.now(timezone.utc),
    )


def test_benevity_rejection_extracts_role_and_company() -> None:
    event = classify_email_event(
        parsed(
            subject="Your Application With Benevity - Senior Staff Developer",
            sender="Benevity Talent <no-reply@benevity.com>",
            body=(
                "Hi Yi,\n\n"
                "Thank you for applying for the Senior Staff Developer role here at Benevity.\n\n"
                "While we were impressed with your qualifications, unfortunately we won’t be "
                "moving forward with your application."
            ),
        )
    )

    assert event.event_type == "rejection"
    assert event.company == "Benevity"
    assert event.role == "Senior Staff Developer"
    assert event.needs_review is False


def test_faire_rejection_does_not_extract_this_time_as_company() -> None:
    event = classify_email_event(
        parsed(
            subject="Thank You For Applying to Faire",
            sender="Faire Recruiting <no-reply@gh-mail.faire.com>",
            body=(
                "Hi Yi,\n\n"
                "Thank you for your interest in Faire. We really appreciate you taking the "
                "time to apply. Unfortunately, we have decided not to proceed with your "
                "candidacy for the current Senior Product Analytics Engineer opening at this time."
            ),
        )
    )

    assert event.event_type == "rejection"
    assert event.company == "Faire"
    assert event.role == "Senior Product Analytics Engineer"
    assert event.needs_review is False


def test_cohere_application_received_is_not_rejection() -> None:
    event = classify_email_event(
        parsed(
            subject="Thanks for applying to Cohere!",
            sender="Cohere Recruiting <no-reply@cohere.com>",
            body=(
                "Hi Yi,\n\n"
                "Thanks for applying to the Applied AI Engineer - Agentic Workflows role at "
                "Cohere! We care about candidate experience and will get back to you no matter "
                "what, even if the answer is unfortunately not the right fit later."
            ),
        )
    )

    assert event.event_type == "application_received"
    assert event.company == "Cohere"
    assert event.role == "Applied AI Engineer - Agentic Workflows"
