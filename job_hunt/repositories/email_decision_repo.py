from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class EmailEventDecision(BaseModel):
    id: str = Field(default_factory=lambda: f"decision_{uuid.uuid4().hex}")
    event_id: str
    decision: Literal["approved", "ignored"]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    note: str = ""


@dataclass(frozen=True)
class MalformedDecisionLine:
    """A line in the decisions file that could not be read back as a decision."""

    line_number: int
    reason: str
    raw: str


class EmailDecisionRepository:
    def __init__(self, path: Path = Path("data/email-event-decisions.jsonl")):
        self.path = path

    def append(self, decision: EmailEventDecision) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(decision.model_dump_json() + "\n")

    def _read(self) -> tuple[list[EmailEventDecision], list[MalformedDecisionLine]]:
        if not self.path.exists():
            return [], []
        decisions: list[EmailEventDecision] = []
        malformed: list[MalformedDecisionLine] = []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                decisions.append(EmailEventDecision.model_validate(json.loads(line)))
            except Exception as exc:  # malformed JSON or a value outside the schema
                malformed.append(
                    MalformedDecisionLine(
                        line_number=number,
                        reason=type(exc).__name__ + ": " + str(exc).split("\n")[0],
                        raw=line,
                    )
                )
        return decisions, malformed

    def malformed(self) -> list[MalformedDecisionLine]:
        """Lines the reader had to skip. Empty when the file is healthy."""
        return self._read()[1]

    def list(self, limit: int = 100000) -> list[EmailEventDecision]:
        decisions, _ = self._read()
        return decisions[-limit:]

    def decided_event_ids(self) -> set[str]:
        return {decision.event_id for decision in self.list()}
