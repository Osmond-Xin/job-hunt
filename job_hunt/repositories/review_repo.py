from __future__ import annotations

import json
from pathlib import Path

from job_hunt.models.review import ReviewItem


class ReviewRepository:
    def __init__(self, path: Path = Path("data/review-queue.jsonl")):
        self.path = path

    def append(self, item: ReviewItem) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(item.model_dump_json() + "\n")

    def list(self, limit: int = 50, status: str = "open") -> list[ReviewItem]:
        if not self.path.exists():
            return []
        items: list[ReviewItem] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = ReviewItem.model_validate(json.loads(line))
            if status != "all" and item.status != status:
                continue
            items.append(item)
        return items[-limit:]

