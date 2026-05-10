from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from filelock import FileLock, Timeout
from pydantic import BaseModel
from rapidfuzz import fuzz

_LOCK_TIMEOUT = 30


TRACKER_HEADER = """# Applications Tracker

| # | Date | Company | Role | Score | Status | PDF | Report | Notes |
|---|------|---------|------|-------|--------|-----|--------|-------|
"""


class TrackerEntry(BaseModel):
    number: int
    date: str
    company: str
    role: str
    score: str
    status: str
    pdf: str
    report: str
    notes: str = ""


class TrackerRepository:
    def __init__(self, path: Path = Path("data/applications.md")):
        self.path = path

    @property
    def _lock(self) -> FileLock:
        return FileLock(str(self.path) + ".lock", timeout=_LOCK_TIMEOUT)

    def ensure_exists(self) -> None:
        if self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(TRACKER_HEADER, encoding="utf-8")

    def parse(self) -> list[TrackerEntry]:
        if not self.path.exists():
            return []
        entries: list[TrackerEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            entry = parse_tracker_line(line)
            if entry:
                entries.append(entry)
        return entries

    def stats(self) -> dict:
        entries = self.parse()
        by_status = Counter(entry.status for entry in entries)
        with_pdf = sum(1 for entry in entries if "✅" in entry.pdf)
        return {"total": len(entries), "by_status": dict(by_status), "with_pdf": with_pdf}

    def add_imported_email_entry(
        self,
        *,
        company: str,
        role: str,
        status: str,
        email_ref: str,
        note: str,
        pdf_attached: bool = False,
    ) -> TrackerEntry:
        """Append a new tracker row.

        ``pdf_attached`` flips the ``pdf`` column to ``✅`` so manual-submit rows
        (`apply --confirmed` with ``--pdf``) record a complete audit trail
        instead of the historical default ``❌``.

        NOTE — this writes to ``applications.md`` synchronously rather than
        going through the TSV staging path in
        ``job_hunt.services.tracker_ops``. Callers in
        ``services/email/review.py`` and ``services/email/reconcile.py`` rely
        on the returned ``TrackerEntry.number`` being the *real* row number
        immediately (e.g. ``EmailEventDecision.note = f"Imported tracker row
        #{entry.number}"``); a staged TSV would only have a tentative
        pre-merge number that may shift during ``tracker_ops.merge``.
        Switching to staging here requires a coordinated change to those
        callers + the email_event_decisions schema, so it is left for a
        future migration. Until then, ``tracker_ops.stage_addition`` is the
        opt-in API for deferred imports that can tolerate deferred numbers.
        """
        self.ensure_exists()
        try:
            with self._lock:
                existing = self.parse()
                next_number = max((entry.number for entry in existing), default=0) + 1
                entry = TrackerEntry(
                    number=next_number,
                    date=__import__("datetime").date.today().isoformat(),
                    company=company,
                    role=role,
                    score="N/A",
                    status=status,
                    pdf="✅" if pdf_attached else "❌",
                    report=email_ref,
                    notes=note,
                )
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(format_tracker_entry(entry) + "\n")
        except Timeout:
            raise RuntimeError(f"Could not acquire tracker lock within {_LOCK_TIMEOUT}s") from None
        return entry

    def append_entry(self, entry: TrackerEntry) -> None:
        """Append a pre-built TrackerEntry row with file locking."""
        self.ensure_exists()
        try:
            with self._lock:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(format_tracker_entry(entry) + "\n")
        except Timeout:
            raise RuntimeError(f"Could not acquire tracker lock within {_LOCK_TIMEOUT}s") from None

    def update_entry(self, updated: TrackerEntry) -> bool:
        """Replace the row matching updated.number in-place.  Returns True if found."""
        if not self.path.exists():
            return False
        try:
            with self._lock:
                lines = self.path.read_text(encoding="utf-8").splitlines(keepends=True)
                replaced = False
                new_lines: list[str] = []
                for line in lines:
                    entry = parse_tracker_line(line.rstrip())
                    if entry and entry.number == updated.number:
                        new_lines.append(format_tracker_entry(updated) + "\n")
                        replaced = True
                    else:
                        new_lines.append(line)
                if replaced:
                    self.path.write_text("".join(new_lines), encoding="utf-8")
        except Timeout:
            raise RuntimeError(f"Could not acquire tracker lock within {_LOCK_TIMEOUT}s") from None
        return replaced

    def find_match(self, *, company: str | None, role: str | None) -> tuple[TrackerEntry | None, float]:
        if not company:
            return None, 0.0
        entries = self.parse()
        best: TrackerEntry | None = None
        best_score = 0.0
        company_norm = normalize(company)
        role_norm = normalize(role or "")
        for entry in entries:
            company_score = 1.0 if normalize(entry.company) == company_norm else fuzz.ratio(normalize(entry.company), company_norm) / 100
            if role_norm:
                role_score = 1.0 if normalize(entry.role) == role_norm else fuzz.token_sort_ratio(entry.role, role or "") / 100
            else:
                role_score = 0.0
            # When the company is an exact match, require strong role similarity to
            # avoid conflating two different roles at the same company.
            if company_score == 1.0 and role_score < 0.85:
                continue
            score = company_score * 0.65 + role_score * 0.35
            if score > best_score:
                best_score = score
                best = entry
        return best, best_score


def parse_tracker_line(line: str) -> TrackerEntry | None:
    line = line.strip()
    if not line.startswith("|") or "---" in line or line.lower().startswith("| #"):
        return None
    parts = [part.strip() for part in line.strip("|").split("|")]
    if len(parts) < 8:
        return None
    try:
        number = int(parts[0])
    except ValueError:
        return None
    return TrackerEntry(
        number=number,
        date=parts[1],
        company=parts[2],
        role=parts[3],
        score=parts[4],
        status=parts[5],
        pdf=parts[6],
        report=parts[7],
        notes=parts[8] if len(parts) > 8 else "",
    )


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def format_tracker_entry(entry: TrackerEntry) -> str:
    return (
        f"| {entry.number} | {entry.date} | {entry.company} | {entry.role} | "
        f"{entry.score} | {entry.status} | {entry.pdf} | {entry.report} | {entry.notes} |"
    )
