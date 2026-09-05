from __future__ import annotations

from pathlib import Path

from job_hunt.models.events import ApplicationEvent
from job_hunt.repositories.jsonl_log import JsonlLog, MalformedLine


class EmailEventRepository:
    def __init__(self, path: Path = Path("data/email-events.jsonl")):
        self.path = path
        self._log = JsonlLog(path, ApplicationEvent)

    def append(self, event: ApplicationEvent) -> None:
        self._log.append(event)

    def malformed(self) -> list[MalformedLine]:
        """Lines the reader had to skip. Empty when the file is healthy."""
        return self._log.malformed()

    def list(self, limit: int = 50, needs_review: bool = False) -> list[ApplicationEvent]:
        events, _ = self._log.read()
        if needs_review:
            events = [event for event in events if event.needs_review]
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

    def replace_line(self, line_number: int, event: ApplicationEvent) -> None:
        """Rewrite one line in place. Used to repair a malformed row."""
        self._log.replace_line(line_number, event)
