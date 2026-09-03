from __future__ import annotations

from pathlib import Path

from job_hunt.models.review import ReviewItem
from job_hunt.repositories.jsonl_log import JsonlLog, MalformedLine


class ReviewRepository:
    def __init__(self, path: Path = Path("data/review-queue.jsonl")):
        self.path = path
        self._log = JsonlLog(path, ReviewItem)

    def append(self, item: ReviewItem) -> None:
        self._log.append(item)

    def malformed(self) -> list[MalformedLine]:
        """Lines the reader had to skip. Empty when the file is healthy."""
        return self._log.malformed()

    def list(self, limit: int = 50, status: str = "open") -> list[ReviewItem]:
        items, _ = self._log.read()
        if status != "all":
            items = [item for item in items if item.status == status]
        return items[-limit:]
