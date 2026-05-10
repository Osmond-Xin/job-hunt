from __future__ import annotations

from pydantic import BaseModel

from job_hunt.config.models import Settings
from job_hunt.models.events import ApplicationEvent
from job_hunt.repositories.email_event_repo import EmailEventRepository
from job_hunt.services.email.gmail_client import GmailClient
from job_hunt.services.email.message_parser import classify_email_event


class EmailPollResult(BaseModel):
    scanned: int = 0
    skipped_seen: int = 0
    events_created: int = 0
    events: list[ApplicationEvent] = []


def poll_gmail(settings: Settings, max_results: int = 25, include_unknown: bool = False) -> EmailPollResult:
    repo = EmailEventRepository()
    seen = repo.seen_message_ids()
    client = GmailClient(
        token_path=settings.email_ingest.token_path,
        credentials_path=settings.email_ingest.credentials_path,
        auth_mode=settings.email_ingest.auth_mode,
    )
    messages = client.list_messages(
        query=settings.email_ingest.query,
        label_ids=settings.email_ingest.label_ids,
        max_results=max_results,
    )
    result = EmailPollResult(scanned=len(messages))
    for item in messages:
        message_id = item.get("id")
        if not message_id:
            continue
        if message_id in seen:
            result.skipped_seen += 1
            continue
        raw = client.get_message(message_id)
        parsed = client.parse_message(raw)
        event = classify_email_event(parsed)
        if event.event_type == "unknown" and not include_unknown:
            result.skipped_seen += 1
            continue
        repo.append(event)
        result.events_created += 1
        result.events.append(event)
    return result
