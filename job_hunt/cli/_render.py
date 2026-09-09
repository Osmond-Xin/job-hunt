from __future__ import annotations

from rich.console import Console

from job_hunt.services.text import _short

console = Console()

__all__ = ["console", "_short", "RichReporter"]


class RichReporter:
    """The session's reporter, wired to this module's console.

    The counterpart to services/web/reporter.Reporter: the session decides what
    to say, and this decides how it looks. Colour lives here and nowhere in
    services/.
    """

    def info(self, message: str) -> None:
        console.print(message)

    def good(self, message: str) -> None:
        console.print(f"[green]{message}[/green]")

    def warn(self, message: str) -> None:
        console.print(f"[yellow]{message}[/yellow]")

    def error(self, message: str) -> None:
        console.print(f"[red]{message}[/red]")

    def bullets(self, title: str, items: list[str], limit: int | None = None) -> None:
        if not items:
            return
        console.print(title)
        for item in items[:limit] if limit else items:
            console.print(f"- {item}")
