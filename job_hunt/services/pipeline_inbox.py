"""Pipeline.md URL inbox — Pending/Processed sections + entry tracking.

The inbox file lives at `data/pipeline.md` (gitignored) and uses two top-level
sections:

```
## Pending
- [ ] https://... | Company | Role
- [!] https://... — Error: login required

## Processed
- [x] #143 | https://... | Company | Role | 4.2/5 | PDF ✅
```

Entry markers:
- ``- [ ]`` — pending evaluation
- ``- [!]`` — error during fetch / evaluation; needs manual attention
- ``- [x]`` — successfully processed

Used by ``job-hunt pipeline add / list / process`` CLI subcommands.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


_DEFAULT_PIPELINE_PATH = Path("data/pipeline.md")
_PENDING_SECTION = "## Pending"
_PROCESSED_SECTION = "## Processed"


class EntryStatus(str, Enum):
    PENDING = "pending"
    PROCESSED = "processed"
    ERROR = "error"


@dataclass
class InboxEntry:
    """One row in pipeline.md."""

    url: str
    company: str = ""
    role: str = ""
    status: EntryStatus = EntryStatus.PENDING
    tracker_id: int | None = None  # filled when processed
    score: str = ""  # filled when processed (e.g. "4.2/5")
    pdf_check: str = ""  # ✅ or ❌
    note: str = ""  # error detail when status=ERROR

    def render(self) -> str:
        """Format this entry as a pipeline.md list item."""
        if self.status is EntryStatus.PROCESSED:
            num = f"#{self.tracker_id}" if self.tracker_id else "#?"
            parts = [num, self.url, self.company, self.role]
            if self.score:
                parts.append(self.score)
            if self.pdf_check:
                parts.append(f"PDF {self.pdf_check}")
            return f"- [x] {' | '.join(p for p in parts if p)}"
        if self.status is EntryStatus.ERROR:
            tail = f" — Error: {self.note}" if self.note else " — Error"
            return f"- [!] {self.url}{tail}"
        # pending
        parts = [self.url]
        if self.company:
            parts.append(self.company)
        if self.role:
            parts.append(self.role)
        return f"- [ ] {' | '.join(parts)}"


# Parse markers
_RE_PENDING = re.compile(r"^-\s*\[\s*\]\s*(.+)$")
_RE_PROCESSED = re.compile(r"^-\s*\[\s*x\s*\]\s*(.+)$", re.IGNORECASE)
_RE_ERROR = re.compile(r"^-\s*\[\s*!\s*\]\s*(.+)$")
_RE_TRACKER_ID = re.compile(r"^#(\d+)\b\s*\|\s*(.*)$")


def parse_entry(line: str) -> InboxEntry | None:
    """Parse one pipeline.md list-item line. Returns None when the line is not an entry."""
    pending_match = _RE_PENDING.match(line.strip())
    processed_match = _RE_PROCESSED.match(line.strip())
    error_match = _RE_ERROR.match(line.strip())

    if processed_match:
        body = processed_match.group(1).strip()
        tracker_id, body = _strip_tracker_id(body)
        parts = [p.strip() for p in body.split("|")]
        url = parts[0] if parts else ""
        company = parts[1] if len(parts) > 1 else ""
        role = parts[2] if len(parts) > 2 else ""
        score = parts[3] if len(parts) > 3 else ""
        pdf_check = ""
        for part in parts[3:]:
            if part.startswith("PDF "):
                pdf_check = part[4:].strip()
        return InboxEntry(
            url=url, company=company, role=role,
            status=EntryStatus.PROCESSED, tracker_id=tracker_id,
            score=score if not score.startswith("PDF") else "",
            pdf_check=pdf_check,
        )
    if error_match:
        body = error_match.group(1).strip()
        url, _, note_part = body.partition(" — Error")
        note = note_part.lstrip(":").strip()
        return InboxEntry(url=url.strip(), status=EntryStatus.ERROR, note=note)
    if pending_match:
        body = pending_match.group(1).strip()
        parts = [p.strip() for p in body.split("|")]
        url = parts[0] if parts else ""
        company = parts[1] if len(parts) > 1 else ""
        role = parts[2] if len(parts) > 2 else ""
        return InboxEntry(url=url, company=company, role=role)
    return None


def _strip_tracker_id(body: str) -> tuple[int | None, str]:
    match = _RE_TRACKER_ID.match(body)
    if match:
        return int(match.group(1)), match.group(2)
    return None, body


def ensure_exists(path: Path | None = None) -> Path:
    """Create pipeline.md with the two-section skeleton if missing."""
    p = path or _DEFAULT_PIPELINE_PATH
    if p.exists():
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"# Pipeline — Job Inbox\n\n{_PENDING_SECTION}\n\n{_PROCESSED_SECTION}\n",
        encoding="utf-8",
    )
    return p


def parse(path: Path | None = None) -> list[InboxEntry]:
    """Read all entries from pipeline.md, in source order."""
    p = path or _DEFAULT_PIPELINE_PATH
    if not p.exists():
        return []
    entries: list[InboxEntry] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        entry = parse_entry(line)
        if entry is not None:
            entries.append(entry)
    return entries


def add(
    url: str,
    *,
    company: str = "",
    role: str = "",
    path: Path | None = None,
) -> bool:
    """Append a pending entry. Returns False when URL already present anywhere."""
    p = ensure_exists(path)
    existing = parse(p)
    if any(e.url == url for e in existing):
        return False
    new_entry = InboxEntry(url=url, company=company, role=role)
    lines = p.read_text(encoding="utf-8").splitlines()
    insert_at = _find_section_end(lines, _PENDING_SECTION)
    lines.insert(insert_at, new_entry.render())
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def mark_processed(
    url: str,
    *,
    tracker_id: int,
    score: str,
    pdf_check: str = "",
    company: str = "",
    role: str = "",
    path: Path | None = None,
) -> bool:
    """Move a Pending entry to Processed. Returns False when URL not in Pending."""
    p = ensure_exists(path)
    lines = p.read_text(encoding="utf-8").splitlines()
    pending_start, pending_end = _section_bounds(lines, _PENDING_SECTION)
    target_idx: int | None = None
    pending_entry: InboxEntry | None = None
    for idx in range(pending_start + 1, pending_end):
        entry = parse_entry(lines[idx])
        if entry is not None and entry.url == url and entry.status is EntryStatus.PENDING:
            target_idx = idx
            pending_entry = entry
            break
    if target_idx is None or pending_entry is None:
        return False

    processed_entry = InboxEntry(
        url=url,
        company=company or pending_entry.company,
        role=role or pending_entry.role,
        status=EntryStatus.PROCESSED,
        tracker_id=tracker_id,
        score=score,
        pdf_check=pdf_check,
    )

    del lines[target_idx]
    insert_at = _find_section_end(lines, _PROCESSED_SECTION)
    lines.insert(insert_at, processed_entry.render())
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def mark_error(url: str, *, note: str, path: Path | None = None) -> bool:
    """Mark a Pending entry as error in place. Returns False when URL not Pending."""
    p = ensure_exists(path)
    lines = p.read_text(encoding="utf-8").splitlines()
    pending_start, pending_end = _section_bounds(lines, _PENDING_SECTION)
    for idx in range(pending_start + 1, pending_end):
        entry = parse_entry(lines[idx])
        if entry is not None and entry.url == url and entry.status is EntryStatus.PENDING:
            error_entry = InboxEntry(url=url, status=EntryStatus.ERROR, note=note)
            lines[idx] = error_entry.render()
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True
    return False


def _section_bounds(lines: list[str], heading: str) -> tuple[int, int]:
    """Return (start_index_of_heading, exclusive_end_index)."""
    start = -1
    for i, line in enumerate(lines):
        if line.strip() == heading:
            start = i
            break
    if start < 0:
        # Append heading at end
        lines.append("")
        lines.append(heading)
        start = len(lines) - 1
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## ") and lines[j].strip() != heading:
            end = j
            break
    return start, end


def _find_section_end(lines: list[str], heading: str) -> int:
    """Index where new content for this section should be inserted."""
    _, end = _section_bounds(lines, heading)
    # Skip back over trailing blank lines so we insert *inside* the section
    while end - 1 > 0 and lines[end - 1].strip() == "":
        end -= 1
    return end


def list_entries(
    *, status: EntryStatus | None = None, path: Path | None = None
) -> list[InboxEntry]:
    """Return entries optionally filtered by status."""
    entries = parse(path)
    if status is None:
        return entries
    return [e for e in entries if e.status is status]
