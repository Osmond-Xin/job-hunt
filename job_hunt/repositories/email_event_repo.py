from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from job_hunt.models.events import ApplicationEvent


@dataclass(frozen=True)
class MalformedEventLine:
    """A line in the events file that could not be read back as an event."""

    line_number: int
    reason: str
    raw: str


class EmailEventRepository:
    def __init__(self, path: Path = Path("data/email-events.jsonl")):
        self.path = path

    def append(self, event: ApplicationEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")

    def _read(self) -> tuple[list[ApplicationEvent], list[MalformedEventLine]]:
        if not self.path.exists():
            return [], []
        events: list[ApplicationEvent] = []
        malformed: list[MalformedEventLine] = []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                events.append(ApplicationEvent.model_validate(json.loads(line)))
            except Exception as exc:  # malformed JSON or a value outside the schema
                malformed.append(
                    MalformedEventLine(
                        line_number=number,
                        reason=type(exc).__name__ + ": " + str(exc).split("\n")[0],
                        raw=line,
                    )
                )
        return events, malformed

    def malformed(self) -> list[MalformedEventLine]:
        """Lines the reader had to skip. Empty when the file is healthy."""
        return self._read()[1]

    def list(self, limit: int = 50, needs_review: bool = False) -> list[ApplicationEvent]:
        events, _ = self._read()
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
        lines = self.path.read_text(encoding="utf-8").splitlines()
        lines[line_number - 1] = event.model_dump_json()
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
