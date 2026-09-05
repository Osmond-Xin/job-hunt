"""Normalise the raw date/age strings the discovery tiers emit into the ISO
``YYYY-MM-DD`` format ``triage.py``'s freshness scoring recognises (see its
``_POSTED_RE``).

Shared by ``gov_boards.py`` (GNWT's "17 hours ago" / "3 days ago") and
``scan.py`` (Workday's "Posted 3 Days Ago" / "Posted Today", Adzuna's
timestamp, and Job Bank's "August 06, 2026"). The relative-age arithmetic
briefly existed as two separate copies, one per module, before this file —
this is the one body that does it.
"""

from __future__ import annotations

import datetime as dt
import re

# GNWT prints how long ago a job was posted rather than the date ("17 hours
# ago", "3 days ago"). Freshness is one of the four axes triage ranks on, so
# an absent date costs the whole board its recency signal. Workday needs the
# same "N units ago" arithmetic for its "Posted N Days Ago" phrasing — one
# regex covers every unit either board uses; `.search` finds the digit+unit
# substring regardless of a leading "Posted ", a trailing "s", or Workday's
# "30+" floor notation (the optional `\+?` after the digits).
_AGE_RE = re.compile(r"(\d+)\+?\s*(min|hour|day|week|month|year)", re.I)
_AGE_DAYS = {"min": 0, "hour": 0, "day": 1, "week": 7, "month": 30, "year": 365}


def relative_age_to_iso(fragment: str, today: dt.date | None = None) -> str:
    """ISO date for a "N units ago" interval, or "" when it is not one.

    Also understands Workday's "Today" / "Yesterday" phrasing and its "N+"
    floor — "+" means at least N, not exactly N, so it is nudged one day
    older to keep triage's >30-day staleness cut from missing "30+ Days Ago".
    """
    value = (fragment or "").strip()
    if not value:
        return ""
    today = today or dt.date.today()
    lowered = value.lower()
    if "today" in lowered:
        return today.isoformat()
    if "yesterday" in lowered:
        return (today - dt.timedelta(days=1)).isoformat()
    match = _AGE_RE.search(value)
    if not match:
        return ""
    days = int(match.group(1)) * _AGE_DAYS[match.group(2).lower()]
    if "+" in value:
        days += 1
    return (today - dt.timedelta(days=days)).isoformat()


_ADZUNA_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def adzuna_created_to_iso(value: str) -> str:
    """Adzuna's `created` carries a full timestamp
    ("2026-08-01T00:00:00Z"); triage's freshness scoring only recognises
    the ISO date portion, so the time and zone are trimmed off here.
    """
    match = _ADZUNA_DATE_RE.match((value or "").strip())
    return match.group(0) if match else ""


def jobbank_date_to_iso(value: str) -> str:
    """Job Bank's `date` field reads "August 06, 2026"; triage's
    freshness scoring only recognises ISO ``YYYY-MM-DD`` inside the
    pipeline line, so this tier scored as permanently undated until the
    format was normalised here.
    """
    value = (value or "").strip()
    if not value:
        return ""
    try:
        return dt.datetime.strptime(value, "%B %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        return ""
