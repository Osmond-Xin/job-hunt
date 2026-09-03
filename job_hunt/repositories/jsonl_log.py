from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class MalformedLine:
    """A line in a JSONL log that could not be read back as a record."""

    line_number: int
    reason: str
    raw: str


class JsonlLog(Generic[T]):
    """Append-only log of `model` records, one JSON object per line.

    Invariant: read() and list() never raise on file content. Every line that
    could not be parsed is retrievable from malformed() with its line number.
    A schema change degrades one row, never a whole command — one hand-written
    line in an event log once took down every inbound command for 19 days.
    """

    def __init__(self, path: Path, model: type[T]) -> None:
        self.path = path
        self.model = model

    def append(self, record: T) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")

    def read(self) -> tuple[list[T], list[MalformedLine]]:
        if not self.path.exists():
            return [], []
        records: list[T] = []
        malformed: list[MalformedLine] = []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                records.append(self.model.model_validate(json.loads(line)))
            except Exception as exc:  # malformed JSON or a value outside the schema
                malformed.append(
                    MalformedLine(
                        line_number=number,
                        reason=type(exc).__name__ + ": " + str(exc).split("\n")[0],
                        raw=line,
                    )
                )
        return records, malformed

    def list(self, limit: int = 50) -> list[T]:
        records, _ = self.read()
        return records[-limit:]

    def malformed(self) -> list[MalformedLine]:
        """Lines the reader had to skip. Empty when the file is healthy."""
        return self.read()[1]

    def write_all(self, records: list[T], malformed: list[MalformedLine] | None = None) -> None:
        """Rewrite the whole file: good records first, then malformed lines verbatim.

        For a caller that must read-modify-write (rather than just append), so
        a hand-written bad line already in the file is never lost.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(record.model_dump_json() for record in records)
        # Preserve malformed lines that existed in the file, appending them at the end.
        if malformed:
            if payload:
                payload += "\n"
            payload += "\n".join(m.raw for m in malformed)
        self.path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")

    def replace_line(self, line_number: int, record: T) -> None:
        """Rewrite one line in place. Used to repair a malformed row."""
        lines = self.path.read_text(encoding="utf-8").splitlines()
        lines[line_number - 1] = record.model_dump_json()
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
