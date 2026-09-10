"""The classifier that should have caught the CGS interview invitation.

Every case here is a real message from the mailbox on 2026-09-10, because the
bug was that plausible-looking rules leaked on real mail: header-only rules
called `no-reply@hire.lever.co` a human, and local-part-only rules called a
GitHub notification a human. Both rules together are what holds.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from job_hunt.repositories.tracker_repo import TrackerEntry
from job_hunt.services.email.human_mail import (
    classify,
    is_automated,
    match_tracker,
    sender_address,
    sender_domain,
)


def entry(number: int, company: str, status: str = "Applied") -> TrackerEntry:
    return TrackerEntry(
        number=number,
        date="2026-09-01",
        company=company,
        role="Some Role",
        score="3.8/5",
        status=status,
        pdf="✅",
        report="",
        notes="",
    )


# --- the two rules, each on mail the other one misses -----------------------


@pytest.mark.parametrize(
    "sender,headers",
    [
        # Rule 1 only: ordinary-looking addresses, bulk headers.
        ("Yi Xin <notifications@github.com>", {"List-Unsubscribe": "<https://github.com/x>"}),
        ("KAYAK <kayak@msg.kayak.com>", {"List-Unsubscribe": "<mailto:u@kayak.com>"}),
        ('"Matt Pocock (AI Hero)" <matt@aihero.dev>', {"List-Unsubscribe": "<https://aihero.dev/u>"}),
        ("Farza <farza@heyclicky.com>", {"Precedence": "bulk"}),
        # Rule 2 only: no bulk headers at all, but nobody is named "no-reply".
        ("CSC Generation <no-reply@hire.lever.co>", {}),
        ("no-reply@coveo.com", {}),
        ("CareerBeacon <no-reply@careerbeacon.com>", {}),
        ("Google Cloud <CloudPlatform-noreply@google.com>", {}),
        ("Indeed <donotreply@jobalert.indeed.com>", {}),
        ("Jobright Job Alert <noreply@jobright.ai>", {}),
        ("Portfolio Monitor <information_pusher@163.com>", {}),
    ],
)
def test_automated_senders_are_filtered(sender, headers):
    assert is_automated(sender, headers) is True


def test_the_cgs_recruiter_is_not_filtered():
    """The message this whole module exists for."""
    assert is_automated("Gerandy Pineda <GPineda@cgsinc.ca>", {}) is False


def test_the_registrar_is_not_filtered():
    assert is_automated("Jason Lennard <jason.lennard@unfc.ca>", {}) is False


def test_header_names_are_matched_case_insensitively():
    """Gmail returns `List-Unsubscribe`; a caller may lower-case it first."""
    assert is_automated("someone@example.com", {"list-unsubscribe": "<x>"}) is True
    assert is_automated("someone@example.com", {"LIST-UNSUBSCRIBE": "<x>"}) is True


def test_a_bulk_header_present_but_empty_does_not_filter():
    """An empty header value is not a bulk signal, and once made everything human."""
    assert is_automated("Gerandy Pineda <GPineda@cgsinc.ca>", {"List-Unsubscribe": ""}) is False


# --- address parsing --------------------------------------------------------


def test_sender_address_and_domain():
    assert sender_address("Gerandy Pineda <GPineda@cgsinc.ca>") == "gpineda@cgsinc.ca"
    assert sender_domain("Gerandy Pineda <GPineda@cgsinc.ca>") == "cgsinc.ca"
    # A bare address with no display name still parses.
    assert sender_domain("no-reply@coveo.com") == "coveo.com"


# --- tracker matching -------------------------------------------------------


def test_recruiter_domain_matches_the_employer_row():
    """`cgsinc.ca` against a row reading "CGS Immersive" — the real pairing."""
    rows = [entry(864, "CGS Immersive"), entry(100, "Unrelated Corp")]
    matched = match_tracker("Gerandy Pineda <GPineda@cgsinc.ca>", rows)
    assert matched is not None and matched.number == 864


def test_public_suffixes_are_not_treated_as_company_names():
    """Without this, every `.ai` domain matches an employer called "AI"."""
    rows = [entry(1, "Ai")]
    assert match_tracker("someone@vendor.ai", rows) is None


def test_a_short_employer_name_still_matches_its_own_domain():
    rows = [entry(1, "IBM")]
    matched = match_tracker("person@ibm.com", rows)
    assert matched is not None and matched.number == 1


def test_legal_suffix_in_the_domain_is_stripped():
    """`protocase-inc.com` against a row reading "Protocase Inc"."""
    rows = [entry(908, "Protocase Inc")]
    matched = match_tracker("hr@protocaseinc.com", rows)
    assert matched is not None and matched.number == 908


def test_hosting_words_do_not_match_an_employer():
    """Without this, every `hire.*` / `mail.*` sender matches a row."""
    rows = [entry(1, "Hire Technologies"), entry(2, "Mailchimp")]
    assert match_tracker("someone@hire.lever.co", rows) is None


def test_unknown_domain_matches_nothing():
    rows = [entry(864, "CGS Immersive")]
    assert match_tracker("friend@gmail.com", rows) is None


# --- what actually gets surfaced -------------------------------------------


def make(sender: str, subject: str, headers: dict | None = None, rows=None):
    return classify(
        message_id="m1",
        thread_id="t1",
        date=datetime(2026, 9, 9, 14, 54),
        sender=sender,
        subject=subject,
        headers=headers or {},
        entries=rows if rows is not None else [entry(864, "CGS Immersive")],
    )


def test_the_lost_invitation_is_surfaced_with_its_row():
    message = make("Gerandy Pineda <GPineda@cgsinc.ca>", "Yi Xin - Job Opportunities / CGS")
    assert message is not None
    assert message.tracker_row == 864
    assert "864" in message.reason


def test_a_stranger_arranging_something_is_surfaced_without_a_row():
    message = make("Someone New <hiring@newco.example>", "Interview availability next week")
    assert message is not None
    assert message.tracker_row is None
    assert message.reason == "a person is trying to arrange something"


def test_a_person_with_no_row_and_no_business_is_not_surfaced():
    """A friend's note is not this check's business."""
    assert make("A Friend <friend@gmail.com>", "dinner saturday?") is None


def test_an_automated_alert_saying_interview_is_still_filtered():
    """The exact failure mode: wording-based checks cannot tell these apart."""
    assert make(
        "Jobright Job Alert <noreply@jobright.ai>",
        "Your interview prep for next steps — 88% match",
    ) is None


def test_an_employer_domain_is_surfaced_even_on_a_dull_subject():
    """Do not require the subject to look important; the domain is enough."""
    message = make("Gerandy Pineda <GPineda@cgsinc.ca>", "quick question")
    assert message is not None and message.tracker_row == 864
    assert message.reason == "employer in tracker #864"


def test_both_reasons_are_reported_together():
    message = make("Gerandy Pineda <GPineda@cgsinc.ca>", "Interview scheduling")
    assert message is not None
    assert "864" in message.reason and "arranging" in message.reason
