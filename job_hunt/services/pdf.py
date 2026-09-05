"""Generic PDF byte-level utilities, shared by node and service callers alike."""

from __future__ import annotations

import re

_COUNT_RE = re.compile(rb"/Count\s+(\d+)")


def pdf_page_count(pdf_bytes: bytes) -> int | None:
    """Page count from the page-tree /Count. Counting `/Type /Page` is wrong —
    it also matches the /Pages tree node."""
    matches = _COUNT_RE.findall(pdf_bytes)
    if not matches:
        return None
    return max(int(m) for m in matches)
