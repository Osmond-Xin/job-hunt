"""Postings already read by hand, so triage stops offering them again.

On 2026-10-24 eleven of the day's top 30 had been read and written off on
earlier days — the verdicts lived only in ``docs/postings-read-*.md``, which
triage cannot see, so the same SaskTel, Spare and Microsoft rows came back
every morning and were read again. A reading session now records each verdict
here (``job-hunt mark-read``); triage drops every ``COLLAPSE`` row, and every
row the second look already read and confirmed cut.

A ``FIT`` is kept visible: until it is applied to (which the tracker then
hides) it is still a candidate. An unread page is never recorded, so it is
retried.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from job_hunt.services.second_look import LOG_PATH as SECOND_LOOK_LOG, confirmed_cuts

READ_LOG_PATH = Path("data/postings-read.tsv")
_HEADER = ["date", "url", "company", "role", "verdict", "reason"]
VERDICTS = ("FIT", "COLLAPSE")


def _records(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def settled_urls(path: Path = READ_LOG_PATH, second_look: Path = SECOND_LOOK_LOG) -> set[str]:
    """URLs a reader has written off: the latest verdict per URL, by date.

    Both logs are merged on the date each verdict was reached, not on file
    position — a back-filled entry is appended late but dated when it was read
    (OpenCode red team 2026-10-24). On the same day a hand-recorded verdict wins
    over the second look's automatic one, so a FIT recorded by hand stays
    visible.
    """
    events: list[tuple[str, int, int, str, str]] = []
    for order, (url, stamp) in enumerate(confirmed_cuts(second_look).items()):
        events.append((stamp, 0, order, url, "COLLAPSE"))
    for order, entry in enumerate(_records(path)):
        if entry.get("url"):
            events.append((entry.get("date", ""), 1, order, entry["url"], entry.get("verdict", "")))
    latest: dict[str, str] = {}
    for _stamp, _source, _order, url, verdict in sorted(events):
        latest[url] = verdict
    return {url for url, verdict in latest.items() if verdict == "COLLAPSE"}


def record(
    url: str,
    verdict: str,
    reason: str,
    *,
    company: str = "",
    role: str = "",
    path: Path = READ_LOG_PATH,
    today: date | None = None,
) -> None:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}, not {verdict!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists() or path.stat().st_size == 0  # an empty file gets its header too
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        if new_file:
            writer.writerow(_HEADER)
        writer.writerow([(today or date.today()).isoformat(), url, company, role, verdict, reason])
