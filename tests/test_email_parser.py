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


# Regression cases built from real garbage in data/email-events.jsonl (see
# job_hunt/services/email/message_parser.py's BAD_FRAGMENTS / GENERIC_COMPANIES
# and the leading-connective / comma / sentence-break prose heuristics).


def test_prose_swallowing_the_whole_greeting_nulls_the_role() -> None:
    """Real row: role landed as the PureFacts greeting/receipt sentence, not
    a clean role name — the prose ("your application", "received your")
    is caught and the field is nulled rather than kept as a wrong value."""
    event = classify_email_event(
        parsed(
            subject="PureFacts Financial Solutions",
            sender="Careers <careers@notifications.example.com>",
            body=(
                "Hi Yi,\n\n"
                "Thank you for applying to PureFacts Financial Solutions! "
                "We have received your application and will review it shortly."
            ),
        )
    )
    assert event.company is None
    assert event.role is None
    assert event.needs_review is True


def test_company_in_role_slot_is_left_for_review_not_guessed() -> None:
    """When the role slot holds a clean, prose-free company name and the
    company itself couldn't be derived, we do not try to detect and move it
    (that would require a fuzzy company-name classifier). The role value
    survives validation as-is, and needs_review already covers the gap."""
    event = classify_email_event(
        parsed(
            subject="Application Update",
            sender="Careers Team <careers@notifications.examplecorp.com>",
            body="Hi Yi,\n\nThank you for applying to PureFacts Financial Solutions.",
        )
    )
    assert event.company is None
    assert event.role == "PureFacts Financial Solutions"
    assert event.needs_review is True


def test_your_interest_fragment_nulls_the_role() -> None:
    """Real row: role captured 'your interest in Blue J! ... Hi Yi, Thank
    you so much for your interest in ...' instead of a role name."""
    event = classify_email_event(
        parsed(
            subject="Application Update",
            sender="Blue J <no-reply@notifications.bluej.com>",
            body=(
                "Hi Yi,\n\nThank you for applying to your interest in Blue J! \U0001f426 "
                "Hi Yi, Thank you so much for your interest in our new Senior Software "
                "Developer opportunity."
            ),
        )
    )
    assert event.role is None
    assert event.needs_review is True


def test_adp_vendor_company_and_disclaimer_role_are_both_nulled() -> None:
    """Real row: company='Adp' (the payroll/HR platform, never the employer)
    and role='your application, Yi Disclaimer: This email has been sent by
    the HR team' (prose, not a role name)."""
    event = classify_email_event(
        parsed(
            subject="Application Confirmation",
            sender="HR Team <hr@adp.com>",
            body=(
                "Hi Yi,\n\nThank you for applying to your application, Yi Disclaimer: "
                "This email has been sent by the HR team."
            ),
        )
    )
    assert event.company is None
    assert event.role is None
    assert event.needs_review is True


def test_truncated_cpp_investments_fields_are_both_nulled() -> None:
    """Real row: company='CPP Investments, we are fort[unate...]' and
    role='CPP Investments and for applyi[ng...]' — both mid-sentence
    fragments, not names."""
    event = classify_email_event(
        parsed(
            subject=(
                "Your Application With CPP Investments, we are fortunate to receive "
                "applications - CPP Investments and for applying to Lead Engineer"
            ),
            sender="no-reply@cppinvestments.com",
            body="Hi Yi,\n\nWhat happens next? Your application is being reviewed.",
        )
    )
    assert event.company is None
    assert event.role is None
    assert event.needs_review is True


def test_shl_vendor_company_and_prose_role_are_both_nulled() -> None:
    """Real row: company='Shl' (the assessment vendor; the actual employer,
    CIBC, is only mentioned inside prose) and role is a sentence, not a
    role name."""
    event = classify_email_event(
        parsed(
            subject="Assessment Results",
            sender="SHL <no-reply@shl.com>",
            body=(
                "Hi Yi,\n\nThank you for applying to work with CIBC. This video was "
                "created to share insights we've learned about you."
            ),
        )
    )
    assert event.company is None
    assert event.role is None
    assert event.needs_review is True


def test_precision_ai_company_kept_but_joining_our_team_role_nulled() -> None:
    """Real row: company='Precision AI' is a genuine name and survives;
    role='joining our team' is a stock phrase, not a role name, and is
    nulled even though the company came through clean."""
    event = classify_email_event(
        parsed(
            subject="Your Application With Precision AI - joining our team",
            sender="no-reply@precisionai.com",
            body="Hi Yi,\n\nWe wanted to say thanks.",
        )
    )
    assert event.company == "Precision AI"
    assert event.role is None
    assert event.needs_review is True


def test_company_with_comma_and_ampersand_survives_validation() -> None:
    """A real company name can contain a comma and an ampersand — the
    prose heuristics must not treat every comma as a sentence break."""
    event = classify_email_event(
        parsed(
            subject=(
                "Your Application With Connor, Clark & Lunn Financial Group - "
                "AI Solutions Engineer"
            ),
            sender="Talent <no-reply@cclgroup.com>",
            body=(
                "Hi Yi,\n\n"
                "Thank you for applying for the AI Solutions Engineer role here at "
                "Connor, Clark & Lunn Financial Group.\n\n"
                "While we were impressed with your qualifications, unfortunately we "
                "won't be moving forward with your application."
            ),
        )
    )
    assert event.company == "Connor, Clark & Lunn Financial Group"
    assert event.role == "AI Solutions Engineer"
    assert event.needs_review is False


def test_hyphenated_non_ascii_role_survives_validation() -> None:
    """A real role name can be hyphenated and non-ASCII — neither should
    trip the prose heuristics."""
    event = classify_email_event(
        parsed(
            subject=(
                "Your Application With Difuze inc. - "
                "Développeur·euse Full Stack (Senior)"
            ),
            sender="no-reply@difuze.com",
            body=(
                "Hi Yi,\n\n"
                "Thank you for applying for the Développeur·euse Full Stack "
                "(Senior) role here at Difuze inc.\n\n"
                "While we were impressed with your qualifications, unfortunately we "
                "won't be moving forward with your application."
            ),
        )
    )
    assert event.company == "Difuze inc"
    assert event.role == "Développeur·euse Full Stack (Senior)"
    assert event.needs_review is False
