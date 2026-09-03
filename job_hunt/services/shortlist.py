"""Turn the ranked pipeline into the list `job-hunt triage` actually shows.

`job_hunt.services.triage` supplies the leaves — parsing, exclusion, scoring,
the deterministic rank — but the shape the operator sees comes from composing
four stages: rank a wider pool than asked for → an optional LLM screen pass →
optional link verification (which also annotates the pipeline) → an
overflow-lane rescue. That composition is policy, established one incident
at a time, and it used to live inside the `triage` command body with no seam
to test it through. `screener` and `checker` are injectable so this runs
without a network call or an LLM in a test; `progress` is injectable so the
command can narrate the two slow steps in its own style without this module
doing any I/O of its own.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from job_hunt.services.link_check import SKIPPED, Verdict, annotate_pipeline, check_urls
from job_hunt.services.screen import Screened, screen
from job_hunt.services.triage import Ranked, excluded, parse_pipeline, rank, tracker_seen

# How many shortlist slots are reserved for this exploration budget.
#
# With --screen the model's fit rating is the primary sort key, so a row it
# rates 3 can never outrank anything it rates 4 — including rows the
# deterministic score liked. On 2026-08-16 that buried two GNWT rows
# (deterministic score 8.5 and 7.5) beneath a Nova Scotia full-stack post the
# model preferred.
#
# This was named `_immigration_lane` through 2026-09-03, as if it read
# geography or immigration status. It never has — it only ever looked at
# `Ranked.score`, the same deterministic fit score every other row gets, and
# any row could land here regardless of what it was. The name was claiming a
# protection the code did not provide.
#
# What it actually is: deliberately small, deliberately not a re-ranking, a
# bounded rescue for the highest deterministic-score rows the model's fit
# ranking pushed off the list. The same 2026-08-16 batch showed why it's kept
# small — the failure mode runs both ways: the row with the highest
# deterministic score of the batch (8.5, an ATIPP privacy analyst) evaluated
# at 1.18 once a model read the actual JD. The heuristic knows where a job
# sits in the ranking, not what it is. So this buys a bounded number of
# second looks per batch — which is exactly what learning that cost.
_OVERFLOW_LANE_SLOTS = 2


def _overflow_lane(pool: list[Ranked], shortlisted: list[Ranked]) -> list[Ranked]:
    """Highest deterministic-score rows that the fit ranking pushed off the list."""
    if not pool:
        return []
    already = {id(item) for item in shortlisted}
    overflow = [item for item in pool if id(item) not in already]
    overflow.sort(key=lambda item: -item.score)
    return overflow[:_OVERFLOW_LANE_SLOTS]


@dataclass(frozen=True)
class ShortlistOptions:
    limit: int = 10
    screen: bool = False
    pool: int = 60
    verify: bool = False
    verify_delay: float = 1.0


@dataclass(frozen=True)
class ShortlistEntry:
    ranked: Ranked
    fit: float | None  # None = unscreened (either --screen was off, or the model skipped this row)
    screen_reason: str  # only meaningful when fit is not None
    verdict: Verdict | None  # None = unverified
    overflow: bool  # True if this entry filled an overflow-lane slot, not a ranked-list slot


@dataclass(frozen=True)
class Shortlist:
    entries: list[ShortlistEntry]
    pending: int
    excluded: Counter[str]
    dropped_by_model: int
    unscreened: int
    rejected: Counter[str]
    unchecked: int  # SKIPPED — never folded into "nothing dead"
    marked_in_pipeline: int
    shortfall: int
    # The sketch for this dataclass had no room for a failed screen call: the
    # command still needs to say *why* the list is unscreened, not just that it
    # is. `screen()` fails open (every row kept, unscreened) so dropped_by_model
    # and unscreened alone don't distinguish "no --screen" from "--screen asked
    # for but mmx was unreachable".
    screen_error: str = ""


def build_shortlist(
    *,
    pipeline: Path,
    tracker: Path,
    options: ShortlistOptions,
    screener: Callable[[list[tuple[str, str, str]]], tuple[dict[int, Screened], str]] = screen,
    checker: Callable[..., dict[str, Verdict]] = check_urls,
    today: date | None = None,
    progress: Callable[[str], None] = lambda _m: None,
) -> Shortlist:
    """Rank the pipeline inbox down to `options.limit` candidates.

    Four stages, in order: rank, optional LLM screen, optional link
    verification (which also marks dead postings in `pipeline`), then the
    overflow lane. Each stage's ordering invariants are documented where
    they are enforced below — they were each written after a real incident.
    """
    rows = parse_pipeline(pipeline.read_text(encoding="utf-8"))
    seen_urls, seen_pairs = (
        tracker_seen(tracker.read_text(encoding="utf-8")) if tracker.exists() else (set(), set())
    )

    limit = options.limit
    # With screening on, rank a wider pool and let the model do the cutting:
    # its judgement of role shape is better than the regexes', and dropping a
    # row before the model sees it wastes the pass.
    # Verification drops rows, so it needs spare candidates to backfill with —
    # otherwise asking for 10 and losing 6 to dead links returns 4.
    ranked_limit = options.pool if options.screen else (max(limit * 4, 40) if options.verify else limit)
    best = rank(rows, limit=ranked_limit, seen_urls=seen_urls, seen_pairs=seen_pairs, today=today)

    fits: dict[int, float] = {}
    reasons: dict[int, str] = {}
    # Rows the fit ranking pushed out that the overflow lane may draw from
    # (see _OVERFLOW_LANE_SLOTS). Empty unless the model screened the pool —
    # without a fit key the list is already in deterministic-score order and
    # there is nothing for the lane to rescue.
    overflow_pool: list[Ranked] = []
    dropped_by_model = 0
    unscreened = 0
    screen_error = ""
    if options.screen and best:
        progress(f"screening {len(best)} rows through MiniMax…")
        verdicts, screen_error = screener(
            [(item.row.company, item.row.role, item.row.location) for item in best]
        )
        # `screen()`'s verdict dict is keyed by 1-based position in `best` —
        # not by any identity of the row itself. Losing this pairing silently
        # attaches the wrong verdict to the wrong row.
        kept = [
            (item, verdicts[index])
            for index, item in enumerate(best, start=1)
            if verdicts[index].keep
        ]
        dropped_by_model = len(best) - len(kept)
        unscreened = sum(1 for _item, verdict in kept if not verdict.screened)
        # Model fit first, then the deterministic priority score as tie-break.
        kept.sort(key=lambda pair: (-pair[1].fit, -pair[0].score))
        overflow_pool = [item for item, _verdict in kept]
        best = list(overflow_pool) if options.verify else overflow_pool[:limit]
        for item, verdict in kept:
            if verdict.screened:
                fits[id(item)] = verdict.fit
                reasons[id(item)] = verdict.reason

    verdicts_by_url: dict[str, Verdict] = {}
    rejected: Counter[str] = Counter()
    unchecked = 0
    marked = 0
    shortfall = 0
    if options.verify and best:
        # One batched call: `check_urls` shares a single client and only sleeps
        # *between* URLs, so feeding it one URL at a time meant no keep-alive
        # and no delay at all — the opposite of the intent.
        head = best[: max(limit * 4, 40)]
        progress(f"verifying {len(head)} candidates…")
        verdicts_by_url = checker([item.row.url for item in head], delay_s=options.verify_delay)
        survivors: list[Ranked] = []
        for item in head:
            verdict = verdicts_by_url.get(item.row.url)
            if verdict is not None and verdict.rejects:
                rejected[verdict.status] += 1
                continue
            survivors.append(item)
        marked = annotate_pipeline(pipeline, verdicts_by_url)
        # Survivors keep their rank order; the shortfall is reported below
        # rather than silently handed back as a shorter list.
        shortfall = limit - len(survivors)
        best = survivors[:limit]
        # The lane must never resurrect a posting verification just killed, and
        # rows past `head` were never checked at all.
        if overflow_pool:
            overflow_pool = list(survivors)
        # "Nothing dead" must not stand in for "nothing checked": hosts the
        # checker declines to fetch come back SKIPPED, and reporting them as
        # clean is how a dead posting reached the shortlist (2026-08-15).
        unchecked = sum(
            1
            for item in head
            if (v := verdicts_by_url.get(item.row.url)) is not None and v.status == SKIPPED
        )

    overflow_rows = _overflow_lane(overflow_pool, best)

    def _entry(item: Ranked, *, is_overflow: bool) -> ShortlistEntry:
        return ShortlistEntry(
            ranked=item,
            fit=fits.get(id(item)),
            screen_reason=reasons.get(id(item), ""),
            verdict=verdicts_by_url.get(item.row.url),
            overflow=is_overflow,
        )

    entries = [_entry(item, is_overflow=False) for item in best] + [
        _entry(item, is_overflow=True) for item in overflow_rows
    ]

    dropped = Counter(reason for row in rows if (reason := excluded(row)))

    return Shortlist(
        entries=entries,
        pending=len(rows),
        excluded=dropped,
        dropped_by_model=dropped_by_model,
        unscreened=unscreened,
        rejected=rejected,
        unchecked=unchecked,
        marked_in_pipeline=marked,
        shortfall=shortfall,
        screen_error=screen_error,
    )
