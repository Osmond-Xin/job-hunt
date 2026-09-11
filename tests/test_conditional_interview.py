"""An acknowledgement promising an interview is not an interview.

Two tracker rows sat at `Interview` for four months on the strength of one
sentence pattern, and were counted as real progress when the operator was asked
how his search was going. The sentences below are verbatim from the mailbox.
"""

from __future__ import annotations

from datetime import datetime

from job_hunt.services.email.message_parser import ParsedEmail, classify_email_event


def email(body: str, subject: str = "Your application") -> ParsedEmail:
    return ParsedEmail(
        message_id="m1",
        thread_id="t1",
        sender="Talent Team <no-reply@example.com>",
        subject=subject,
        date=datetime(2026, 9, 11, 2, 56),
        snippet=body[:120],
        body=body,
    )


JOBBER_ACK = (
    "Hi Yi, You did it! We've successfully received your application for the "
    "Intermediate Software Engineer position with Jobber. So what happens next? "
    "Well, you've done your part. We have your application and we're looking "
    "forward to reviewing it in detail. If it aligns with what we're looking for, "
    "we'll reach out to schedule an interview."
)

LUMERATE_ACK = (
    "Hi XinYi, Thank you for your interest in the Senior Frontend Developer role "
    "with us at Lumerate. Your application has been successfully received! Our team "
    "is reviewing applications on an ongoing basis. We will contact you via email if "
    "your qualifications and experience align with the requirements of the role and "
    "we wish to schedule an interview."
)


def test_jobber_acknowledgement_is_not_an_interview():
    event = classify_email_event(email(JOBBER_ACK))
    assert event.event_type != "interview"
    assert event.event_type == "application_received"


def test_lumerate_acknowledgement_is_not_an_interview():
    event = classify_email_event(email(LUMERATE_ACK))
    assert event.event_type != "interview"


def test_a_real_invitation_still_classifies_as_interview():
    """The CGS mail that was missed — a person arranging an actual time."""
    body = (
        "My name is Gerandy Pineda, Senior Talent Acquisition Specialist with CGS Inc. "
        "You recently applied to one of our positions. I would like to schedule a "
        "e-Interview meeting to discuss the role. Interview details: Location: Teams "
        "Meeting (Video Call - Online). Microsoft Teams meeting Join: "
        "https://teams.microsoft.com/meet/217713734519631"
    )
    assert classify_email_event(email(body)).event_type == "interview"


def test_a_booking_link_beats_the_hedge():
    """Some mails hedge and still book a slot. The booking wins."""
    body = (
        "Thanks for applying. If your background is a fit we would love to talk — "
        "please schedule an interview using my Calendly below."
    )
    assert classify_email_event(email(body)).event_type == "interview"


def test_an_unhedged_invitation_is_an_interview():
    body = "We would like to schedule an interview with you next week. Are you free Tuesday?"
    assert classify_email_event(email(body)).event_type == "interview"


def test_the_hedge_only_counts_when_it_is_nearby():
    """A conditional far earlier in a long mail must not suppress a real invite."""
    body = (
        "If you have questions about parking, reply to this note. "
        + "We are excited about your background. " * 12
        + "We would like to schedule an interview with you on Thursday."
    )
    assert classify_email_event(email(body)).event_type == "interview"
