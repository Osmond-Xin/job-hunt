"""Guards every new source adapter shares, so a malformed response is counted,
not crashed on and not mistaken for a quiet board.

- ``safe_total`` reads an advertised count from whatever a source sends — an
  int, "1,371", "127.0", None — and returns None for "unknown", never 0.
- ``absolute_url`` accepts only an http(s) URL (resolving a documented
  relative form against ``base``), so ``123`` or ``javascript:void(0)`` can
  never become a posting link.
- ``guard_rows`` runs a row parser over a list and counts every element that
  is not a dict, raises, or yields a row with no title or no usable URL as
  *lost*. A parser returns ``SKIP`` for a row dropped on purpose (a Job Bank
  mirror, a filled posting), which is not loss.

Added 2026-09-27 after review of the new-sources batch: one null list element
raised out of an adapter and, through the scan's catch-all, discarded every
row the tier had already read.
"""

from __future__ import annotations

import re
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

SKIP = object()
_NUMBER_RE = re.compile(r"^\s*(\d[\d,]*)(?:\.0+)?\s*$")


def safe_total(value: Any) -> int | None:
    """An advertised count, or None when the source did not give a usable one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 and value == int(value) else None
    match = _NUMBER_RE.match(str(value)) if isinstance(value, str) else None
    return int(match.group(1).replace(",", "")) if match else None


def absolute_url(value: Any, base: str | None = None) -> str:
    """``value`` as an absolute http(s) URL, or "" when it cannot be one."""
    if not isinstance(value, str) or not value.strip():
        return ""
    url = value.strip()
    if base and not urlparse(url).scheme:
        url = urljoin(base, url)
    parsed = urlparse(url)
    return url if parsed.scheme in ("http", "https") and parsed.netloc else ""


def guard_rows(
    items: Any, parse: Callable[[dict[str, Any]], Any], *, base: str | None = None
) -> tuple[list[dict[str, str]], int]:
    """(rows, lost) from ``items``; see the module docstring."""
    rows: list[dict[str, str]] = []
    lost = 0
    for item in items if isinstance(items, list) else []:
        try:
            row = parse(item) if isinstance(item, dict) else None
        except (AttributeError, TypeError, ValueError, KeyError, IndexError):
            row = None
        if row is SKIP:
            continue
        url = absolute_url(row.get("url"), base) if isinstance(row, dict) else ""
        if not url or not str(row.get("title") or "").strip():
            lost += 1
            continue
        rows.append({**row, "url": url})
    return rows, lost
