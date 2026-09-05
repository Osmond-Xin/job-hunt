from __future__ import annotations

from rich.console import Console

console = Console()


def _short(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"
