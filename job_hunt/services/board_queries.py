"""The search terms a general board reader runs, shared by every such reader."""

from __future__ import annotations

from typing import Any


def board_queries(config: dict[str, Any], roles: list[str]) -> list[str]:
    """``queries`` (or the profile's role terms), then ``extra_queries``, deduplicated in order.

    ``extra_queries`` adds to the role terms rather than replacing them: the
    titles remote-region employers use ("IT Analyst", "Systems Administrator")
    are not in the AI-leaning profile list (2026-10-24).
    """
    terms = [*(config.get("queries") or roles or []), *(config.get("extra_queries") or [])]
    return list(dict.fromkeys(str(term).strip() for term in terms if str(term).strip()))
