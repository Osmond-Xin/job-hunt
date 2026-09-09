"""String helpers with no rendering opinion.

``_short`` lived in ``cli/_render.py`` next to the Rich console, which was fine
while only commands used it. The Workday step machine uses it too, and a service
importing from ``cli/`` is the layer inversion this refactor exists to remove --
so the truncation moved down here and ``_render`` re-exports it for the commands
that already imported it from there.
"""

from __future__ import annotations


def _short(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"
