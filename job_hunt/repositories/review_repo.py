from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from job_hunt.models.review import ReviewItem


@dataclass(frozen=True)
class MalformedReviewLine:
    """A line in the review queue file that could not be read back as a review item."""

    line_number: int
    reason: str
    raw: str


class ReviewRepository:
    def __init__(self, path: Path = Path("data/review-queue.jsonl")):
        self.path = path

    def append(self, item: ReviewItem) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(item.model_dump_json() + "\n")

    def _read(self) -> tuple[list[ReviewItem], list[MalformedReviewLine]]:
        if not self.path.exists():
            return [], []
        items: list[ReviewItem] = []
        malformed: list[MalformedReviewLine] = []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                items.append(ReviewItem.model_validate(json.loads(line)))
            except Exception as exc:  # malformed JSON or a value outside the schema
                malformed.append(
                    MalformedReviewLine(
                        line_number=number,
                        reason=type(exc).__name__ + ": " + str(exc).split("\n")[0],
                        raw=line,
                    )
                )
        return items, malformed

    def malformed(self) -> list[MalformedReviewLine]:
        """Lines the reader had to skip. Empty when the file is healthy."""
        return self._read()[1]

    def list(self, limit: int = 50, status: str = "open") -> list[ReviewItem]:
        items, _ = self._read()
        if status != "all":
            items = [item for item in items if item.status == status]
        return items[-limit:]

