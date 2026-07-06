"""Immigration-priority location matching.

The operator's core goal is Canadian PR. A full-time offer in certain
provinces (AIP) or pilot communities (RCIP) or PNP-favourable provinces
advances that goal enough to justify applying to roles with a weaker
background fit. The region list lives in ``profile/profile.yml`` under
``immigration_priority:`` so the operator can edit it as programs change —
the code knows nothing about immigration rules, only place-name matching
(same design principle as the mode switch, docs/design-notes.md §N.1).
"""

from __future__ import annotations

from pathlib import Path

import yaml

_PROFILE_PATH = Path("profile/profile.yml")


def priority_config(profile_path: Path | None = None) -> dict:
    """Return the ``immigration_priority`` block, or ``{}`` when absent/disabled."""
    path = profile_path if profile_path is not None else _PROFILE_PATH
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    block = raw.get("immigration_priority")
    if not isinstance(block, dict) or not block.get("enabled", False):
        return {}
    return block


def place_tokens(profile_path: Path | None = None) -> list[str]:
    """Lowercased place names (provinces + communities) from the priority block."""
    block = priority_config(profile_path)
    tokens: list[str] = []
    for key in ("provinces", "communities"):
        for entry in block.get(key) or []:
            token = str(entry).strip().lower()
            if token and token not in tokens:
                tokens.append(token)
    return tokens


def matched_places(location: str, profile_path: Path | None = None) -> list[str]:
    """Return the priority place names found in ``location`` (case-insensitive)."""
    value = (location or "").lower()
    if not value:
        return []
    return [token for token in place_tokens(profile_path) if token in value]


def immigration_context(location: str, profile_path: Path | None = None) -> str:
    """One-paragraph scorer context when the JD location is a priority region.

    Empty string when the block is disabled or the location does not match —
    callers render it into prompts behind ``{% if immigration_context %}``.
    """
    places = matched_places(location, profile_path)
    if not places:
        return ""
    block = priority_config(profile_path)
    note = str(block.get("score_note") or "").strip()
    lines = [
        f"JD location `{location.strip()}` matches the operator's "
        f"immigration-priority regions: {', '.join(places)}."
    ]
    if note:
        lines.append(note)
    return "\n".join(lines)
