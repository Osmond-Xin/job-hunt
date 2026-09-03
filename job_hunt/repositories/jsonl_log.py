from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

from filelock import FileLock, Timeout
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

# Matches tracker_repo.py's constant: how long a writer waits for the file
# lock before giving up and reporting the contention rather than hanging.
_LOCK_TIMEOUT = 30


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

    @property
    def _lock(self) -> FileLock:
        """Cross-process exclusive lock guarding writes to this file.

        Named-timeout idiom matches tracker_repo.py's TrackerRepository._lock.
        is_singleton=True so that a caller holding lock() across a
        read-modify-write (see services/outreach.py) and an internal call
        this class makes to append()/write_all()/replace_line() resolve to
        the *same* lock object and reacquire it reentrantly, instead of a
        second, distinct FileLock instance blocking on itself.
        """
        return FileLock(str(self.path) + ".lock", timeout=_LOCK_TIMEOUT, is_singleton=True)

    def lock(self) -> FileLock:
        """Context manager for a caller whose read-modify-write spans more than
        one JsonlLog call (e.g. read() then write_all()). Hold it across the
        whole cycle — a lock only taken around the final write still loses the
        other writer's update, and can drop a malformed line appended by
        another process in between. Reentrant with the locking append(),
        write_all(), and replace_line() already do internally.
        """
        return self._lock

    def _atomic_write(self, text: str) -> None:
        """Replace the file's contents with `text` without ever exposing a
        truncated file to a reader or a crash.

        Path.write_text() truncates the file and then writes; a crash, a full
        disk, or a kill signal between those two steps leaves the file
        truncated or empty — losing the whole log, not just the record being
        changed. Writing to a temp file in the same directory and calling
        os.replace() into position means a reader always sees either the old
        content or the new content, never a partial write, and an interrupted
        write leaves the previous version on disk untouched. Same directory
        matters: os.replace() is only atomic within one filesystem.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.remove(tmp_name)
            raise

    def _acquire_lock(self) -> FileLock:
        try:
            lock = self._lock
            lock.acquire()
            return lock
        except Timeout:
            raise RuntimeError(
                f"Could not acquire lock on {self.path} within {_LOCK_TIMEOUT}s"
            ) from None

    def append(self, record: T) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self._acquire_lock()
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(record.model_dump_json() + "\n")
        finally:
            lock.release()

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
        a hand-written bad line already in the file is never lost. Holds the
        file lock for the duration and writes atomically (see _atomic_write) so
        a crash mid-write can never truncate the file.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(record.model_dump_json() for record in records)
        # Preserve malformed lines that existed in the file, appending them at the end.
        if malformed:
            if payload:
                payload += "\n"
            payload += "\n".join(m.raw for m in malformed)
        payload = payload + ("\n" if payload else "")
        lock = self._acquire_lock()
        try:
            self._atomic_write(payload)
        finally:
            lock.release()

    def replace_line(self, line_number: int, record: T) -> None:
        """Rewrite one line in place. Used to repair a malformed row.

        `line_number` is 1-indexed, matching MalformedLine.line_number. The
        lock is held across the read and the write, so this always replaces
        whichever line is actually at that position under the lock — not one
        that another locked writer moved or removed in between.

        A `line_number` outside the file's current line range raises
        IndexError rather than silently corrupting a row: without this check,
        a stale `line_number` of 0 or negative would land on Python's
        negative-index wraparound and quietly overwrite an unrelated line
        from the end of the file instead of failing loudly. A `line_number`
        that is in range but was read before a since-applied change (e.g.
        malformed() was called, the file changed, then this was called with
        the old number) still overwrites whatever now occupies that
        position — callers that must guard against that race should call
        lock() themselves and do their own read under it before calling this.
        """
        lock = self._acquire_lock()
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            if line_number < 1 or line_number > len(lines):
                raise IndexError(
                    f"replace_line: line {line_number} does not exist in {self.path} "
                    f"({len(lines)} lines)"
                )
            lines[line_number - 1] = record.model_dump_json()
            self._atomic_write("\n".join(lines) + "\n")
        finally:
            lock.release()
