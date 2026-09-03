from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel, Field

from job_hunt.repositories.jsonl_log import JsonlLog, MalformedLine


CONTACTS_PATH = Path("data/contacts.jsonl")
EVENTS_PATH = Path("data/outreach-events.jsonl")

T = TypeVar("T", bound=BaseModel)


class Contact(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    company: str
    name: str = ""
    title: str = ""
    linkedin_url: str = ""
    email: str = ""
    source: Literal["manual", "linkedin", "email", "web_search"] = "manual"
    relationship: Literal["recruiter", "hiring_manager", "peer", "referral", "unknown"] = "unknown"
    notes: str = ""
    created_at: str = Field(default_factory=lambda: dt.datetime.now(dt.UTC).isoformat())


class OutreachEvent(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    contact_id: str
    application_id: int | None = None
    company: str = ""
    role: str = ""
    channel: Literal["linkedin", "email", "other"] = "linkedin"
    status: Literal["drafted", "sent", "responded", "follow_up_due", "closed"] = "drafted"
    message_path: str = ""
    follow_up_at: str = ""
    notes: str = ""
    created_at: str = Field(default_factory=lambda: dt.datetime.now(dt.UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: dt.datetime.now(dt.UTC).isoformat())


def _read_jsonl(path: Path, model: type[T]) -> tuple[list[T], list[MalformedLine]]:
    return JsonlLog(path, model).read()


def _write_jsonl(path: Path, model: type[T], items: list[T], malformed: list[MalformedLine] | None = None) -> None:
    # Preserve malformed lines that existed in the file, appending them at the end.
    JsonlLog(path, model).write_all(items, malformed)


def list_contacts(path: Path = CONTACTS_PATH) -> list[Contact]:
    contacts, _ = _read_jsonl(path, Contact)
    return contacts


def malformed_contacts(path: Path = CONTACTS_PATH) -> list[MalformedLine]:
    """Lines in the contacts file that could not be read back. Empty when the file is healthy."""
    _, malformed = _read_jsonl(path, Contact)
    return malformed


def add_contact(contact: Contact, path: Path = CONTACTS_PATH) -> Contact:
    contacts, malformed = _read_jsonl(path, Contact)
    contacts.append(contact)
    _write_jsonl(path, Contact, contacts, malformed)
    return contact


def find_contacts(company: str = "", *, path: Path = CONTACTS_PATH) -> list[Contact]:
    needle = company.lower().strip()
    contacts = list_contacts(path)
    if not needle:
        return contacts
    return [c for c in contacts if needle in c.company.lower()]


def get_contact(contact_id: str, path: Path = CONTACTS_PATH) -> Contact | None:
    for contact in list_contacts(path):
        if contact.id.startswith(contact_id):
            return contact
    return None


def list_events(path: Path = EVENTS_PATH) -> list[OutreachEvent]:
    events, _ = _read_jsonl(path, OutreachEvent)
    return events


def malformed_events(path: Path = EVENTS_PATH) -> list[MalformedLine]:
    """Lines in the events file that could not be read back. Empty when the file is healthy."""
    _, malformed = _read_jsonl(path, OutreachEvent)
    return malformed


def add_event(event: OutreachEvent, path: Path = EVENTS_PATH) -> OutreachEvent:
    events, malformed = _read_jsonl(path, OutreachEvent)
    events.append(event)
    _write_jsonl(path, OutreachEvent, events, malformed)
    return event


def update_event(event_id: str, *, status: str | None = None, follow_up_at: str = "", notes: str = "", path: Path = EVENTS_PATH) -> OutreachEvent | None:
    events, malformed = _read_jsonl(path, OutreachEvent)
    updated: OutreachEvent | None = None
    for index, event in enumerate(events):
        if not event.id.startswith(event_id):
            continue
        data = event.model_dump()
        if status:
            data["status"] = status
        if follow_up_at:
            data["follow_up_at"] = follow_up_at
        if notes:
            data["notes"] = notes
        data["updated_at"] = dt.datetime.now(dt.UTC).isoformat()
        updated = OutreachEvent.model_validate(data)
        events[index] = updated
        break
    if updated:
        _write_jsonl(path, OutreachEvent, events, malformed)
    return updated


def due_events(*, today: dt.date | None = None, path: Path = EVENTS_PATH) -> list[OutreachEvent]:
    today = today or dt.date.today()
    due: list[OutreachEvent] = []
    for event in list_events(path):
        if event.status not in {"sent", "follow_up_due"} or not event.follow_up_at:
            continue
        try:
            follow_up = dt.date.fromisoformat(event.follow_up_at[:10])
        except ValueError:
            continue
        if follow_up <= today:
            due.append(event)
    return due
