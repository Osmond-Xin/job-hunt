from __future__ import annotations

import json
from pathlib import Path

from job_hunt.models.events import ApplicationEvent


class EmailEventRepository:
    def __init__(self, path: Path = Path("data/email-events.jsonl")):
        self.path = path

    def append(self, event: ApplicationEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")

    def list(self, limit: int = 50, needs_review: bool = False) -> list[ApplicationEvent]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        events: list[ApplicationEvent] = []
        for line in lines:
            if not line.strip():
                continue
            event = ApplicationEvent.model_validate(json.loads(line))
            if needs_review and not event.needs_review:
                continue
            events.append(event)
        return events[-limit:]

    def get(self, event_id: str) -> ApplicationEvent | None:
        for event in self.list(limit=100000):
            if event.id == event_id:
                return event
        return None

    def find_by_prefix(self, event_id_or_prefix: str) -> ApplicationEvent | None:
        events = [event for event in self.list(limit=100000) if event.id.startswith(event_id_or_prefix)]
        if len(events) == 1:
            return events[0]
        return self.get(event_id_or_prefix)

    def seen_message_ids(self) -> set[str]:
        ids: set[str] = set()
        for event in self.list(limit=100000):
            if event.source_message_id:
                ids.add(event.source_message_id)
        return ids
