from __future__ import annotations

import datetime as dt
from pathlib import Path

from job_hunt.services.outreach import (
    Contact,
    OutreachEvent,
    add_contact,
    add_event,
    due_events,
    find_contacts,
    get_contact,
    update_event,
)


def test_contact_roundtrip_and_company_search(tmp_path: Path) -> None:
    path = tmp_path / "contacts.jsonl"
    contact = add_contact(
        Contact(company="Anthropic", name="Ada", relationship="recruiter"),
        path=path,
    )

    assert get_contact(contact.id[:6], path=path).name == "Ada"
    assert find_contacts("anth", path=path)[0].id == contact.id
    assert find_contacts("cohere", path=path) == []


def test_outreach_event_mark_sent_and_due(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    event = add_event(
        OutreachEvent(contact_id="c1", company="Anthropic", role="AI Engineer"),
        path=path,
    )

    updated = update_event(
        event.id[:6],
        status="sent",
        follow_up_at="2026-05-01",
        notes="Sent LinkedIn request.",
        path=path,
    )

    assert updated is not None
    assert updated.status == "sent"
    assert due_events(today=dt.date(2026, 5, 9), path=path)[0].id == event.id
