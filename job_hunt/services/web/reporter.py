"""How a session says what it is doing, without deciding how it looks.

``_open_apply_page`` carried forty-three ``console.print`` calls. Moving them
into ``services/`` would have put Rich markup and terminal formatting in the
work layer -- the exact mixing this refactor exists to undo -- so the session
takes one of these instead. ``cli/`` passes a Rich-backed reporter; tests pass
one that records, and can then assert what the session reported rather than
scraping stdout.

A protocol rather than a bare callable because the calls carry severity, and
several of them print a heading and then a list. Both facts have to survive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class Reporter(Protocol):
    def info(self, message: str) -> None: ...

    def good(self, message: str) -> None: ...

    def warn(self, message: str) -> None: ...

    def error(self, message: str) -> None: ...

    def bullets(self, title: str, items: list[str], limit: int | None = None) -> None: ...


@dataclass
class RecordingReporter:
    """A reporter for tests: keeps what it was told, formats nothing."""

    lines: list[tuple[str, str]] = field(default_factory=list)

    def info(self, message: str) -> None:
        self.lines.append(("info", message))

    def good(self, message: str) -> None:
        self.lines.append(("good", message))

    def warn(self, message: str) -> None:
        self.lines.append(("warn", message))

    def error(self, message: str) -> None:
        self.lines.append(("error", message))

    def bullets(self, title: str, items: list[str], limit: int | None = None) -> None:
        self.lines.append(("bullets", title))
        for item in items[:limit] if limit else items:
            self.lines.append(("bullet", item))

    # -- helpers for assertions -------------------------------------------

    @property
    def text(self) -> str:
        return "\n".join(message for _, message in self.lines)

    def of(self, level: str) -> list[str]:
        return [message for kind, message in self.lines if kind == level]


class NullReporter:
    """Says nothing. For call sites that have no operator watching."""

    def info(self, message: str) -> None: ...

    def good(self, message: str) -> None: ...

    def warn(self, message: str) -> None: ...

    def error(self, message: str) -> None: ...

    def bullets(self, title: str, items: list[str], limit: int | None = None) -> None: ...
