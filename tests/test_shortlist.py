"""Tests for the composed `triage` pipeline: rank -> screen -> verify -> overflow lane.

`job_hunt.services.triage` tests the leaves. This file tests the five
ordering invariants that used to live only as comments inside the `triage`
command body, each written after a real incident. `build_shortlist` takes
`screener` and `checker` as injectable callables specifically so these can be
proven without a network call or an LLM.
"""

from __future__ import annotations

from datetime import date

from job_hunt.services.link_check import DEAD, LIVE, SKIPPED, Verdict
from job_hunt.services.screen import Screened
from job_hunt.services.shortlist import ShortlistOptions, build_shortlist
from job_hunt.services.triage import parse_pipeline, score as triage_score


def _row(url: str, company: str, role: str, location: str, *, posted: str = "", source: str = "") -> str:
    parts = [url, company, role, location]
    if posted:
        parts.append(f"posted {posted}")
    if source:
        parts.append(f"source: {source}")
    return "- [ ] " + " | ".join(parts)


def _pipeline(rows: list[str]) -> str:
    return "\n".join(rows) + "\n"


def _write(tmp_path, name: str, text: str):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_verification_widens_the_ranked_pool_past_the_asked_for_limit(tmp_path) -> None:
    """`ranked_limit` and the later `head` slice both use `max(limit * 4, 40)`.

    Asking for 5 with --verify must rank (and be willing to check) 40
    candidates, not 5 — verification drops rows, so the pool needs spare
    candidates to backfill with, or 5 requested minus a few dead links comes
    back short for no reason the operator can see.
    """
    rows = [
        _row(f"https://example.invalid/{i}", f"Company{i}", "AI Engineer", "Halifax, NS")
        for i in range(50)
    ]
    pipeline = _write(tmp_path, "pipeline.md", _pipeline(rows))
    tracker = _write(tmp_path, "applications.md", "")

    calls: list[list[str]] = []

    def fake_checker(urls, *, delay_s=1.0, **_ignored):
        calls.append(list(urls))
        return {url: Verdict(url, LIVE) for url in urls}

    result = build_shortlist(
        pipeline=pipeline,
        tracker=tracker,
        options=ShortlistOptions(limit=5, verify=True, verify_delay=0.0),
        checker=fake_checker,
    )

    assert len(calls) == 1
    assert len(calls[0]) == 40  # max(5 * 4, 40), not the 5 that were asked for
    assert len(result.entries) == 5


def test_screen_verdicts_are_matched_by_position_not_row_identity(tmp_path) -> None:
    """`screen()`'s verdict dict is keyed by 1-based position in the ranked list.

    A fit or reason attached to the wrong row is silent — nothing raises, the
    shortlist just quietly praises or drops the wrong posting.
    """
    rows = [
        _row("https://example.invalid/a", "Alpha", "AI Engineer", "Halifax, NS"),
        _row("https://example.invalid/b", "Bravo", "AI Engineer", "Halifax, NS"),
        _row("https://example.invalid/c", "Charlie", "AI Engineer", "Halifax, NS"),
    ]
    pipeline = _write(tmp_path, "pipeline.md", _pipeline(rows))
    tracker = _write(tmp_path, "applications.md", "")

    seen_order: list[str] = []

    def fake_screener(pairs):
        verdicts = {}
        for index, (company, _role, _location) in enumerate(pairs, start=1):
            seen_order.append(company)
            # Drop whichever row landed on position 2 and stash each kept row's
            # own company as its "reason", so a mismatch shows up as a reason
            # attached to the wrong company.
            verdicts[index] = Screened(index=index, keep=index != 2, fit=float(index), reason=company)
        return verdicts, ""

    result = build_shortlist(
        pipeline=pipeline,
        tracker=tracker,
        options=ShortlistOptions(limit=10, screen=True, pool=10),
        screener=fake_screener,
    )

    dropped_company = seen_order[1]
    companies = {entry.ranked.row.company for entry in result.entries}
    assert dropped_company not in companies
    for entry in result.entries:
        expected_index = seen_order.index(entry.ranked.row.company) + 1
        assert entry.fit == float(expected_index)
        assert entry.screen_reason == entry.ranked.row.company


def test_screened_sort_is_fit_then_score_not_either_alone(tmp_path) -> None:
    """The post-screen sort is `(-fit, -score)`. Score alone must not win.

    Charlie's deterministic priority score is the highest of the three, so a
    sort on score alone would put it first. Fit is the primary key: Charlie's
    model fit is the worst of the three and it must sink below both others.
    """
    rows = [
        _row("https://example.invalid/alpha", "Alpha", "AI Engineer", "Remote"),
        # Ten days older than Charlie, which is what keeps Charlie's
        # deterministic score strictly the highest: role shape is a tier, not a
        # tally, so "Founding Engineer AI" and "AI Engineer" score the same for
        # role and freshness has to separate them.
        _row(
            "https://example.invalid/bravo", "Government of Manitoba", "AI Engineer",
            "Winnipeg, MB", posted="2026-08-20", source="mb_gov",
        ),
        _row(
            "https://example.invalid/charlie", "Yukon Government", "Founding Engineer AI",
            "Whitehorse, YT", posted="2026-08-30", source="yk_gov",
        ),
    ]
    pipeline = _write(tmp_path, "pipeline.md", _pipeline(rows))
    tracker = _write(tmp_path, "applications.md", "")
    today = date(2026, 8, 31)

    fit_by_company = {"Alpha": 5.0, "Government of Manitoba": 5.0, "Yukon Government": 3.0}

    def fake_screener(pairs):
        return (
            {
                index: Screened(index=index, keep=True, fit=fit_by_company[company], reason="")
                for index, (company, _role, _location) in enumerate(pairs, start=1)
            },
            "",
        )

    # Sanity: Charlie's own deterministic score really is the highest of the
    # three, so this test would be meaningless if it weren't.
    rows_by_company = {r.company: r for r in parse_pipeline(pipeline.read_text(encoding="utf-8"))}
    scores = {company: triage_score(row, today=today)[0] for company, row in rows_by_company.items()}
    assert scores["Yukon Government"] > scores["Government of Manitoba"] > scores["Alpha"]

    result = build_shortlist(
        pipeline=pipeline,
        tracker=tracker,
        options=ShortlistOptions(limit=10, screen=True, pool=10),
        screener=fake_screener,
        today=today,
    )

    # Since 2026-10-24 the list is grouped by lane first (remote region leads),
    # and fit-then-score orders rows *within* a lane: Manitoba and Alpha share
    # the best-overall lane, and Manitoba's fit ties Alpha's while its score is
    # higher. Yukon is the remote-region lane's only row, so it comes first
    # despite the worst fit — the operator reads the lanes first.
    ordered = [(entry.lane, entry.ranked.row.company) for entry in result.entries]
    assert ordered == [
        ("remote region", "Yukon Government"),
        ("best overall", "Government of Manitoba"),
        ("best overall", "Alpha"),
    ]


def test_verification_resets_the_lane_pool_to_survivors(tmp_path) -> None:
    """The lane must never resurrect a posting verification just killed.

    Reversed on 2026-08-15 after a dead posting reached the shortlist through
    exactly this gap: the lane drew from the pre-verification pool instead of
    the survivors.
    """
    rows = [
        _row("https://example.invalid/a", "RowA Co", "AI Engineer", "Halifax, NS"),
        _row("https://example.invalid/b", "RowB Co", "AI Engineer", "Halifax, NS"),
        _row("https://example.invalid/c", "RowC Co", "AI Engineer", "Halifax, NS"),
    ]
    pipeline = _write(tmp_path, "pipeline.md", _pipeline(rows))
    tracker = _write(tmp_path, "applications.md", "")

    # RowA and RowB pass the model's screen with a good fit; RowC is the
    # overflow-lane candidate the fit ranking pushed out.
    fit_by_company = {"RowA Co": 5.0, "RowB Co": 5.0, "RowC Co": 1.0}

    def fake_screener(pairs):
        return (
            {
                index: Screened(index=index, keep=True, fit=fit_by_company[company], reason="")
                for index, (company, _role, _location) in enumerate(pairs, start=1)
            },
            "",
        )

    def fake_checker(urls, *, delay_s=1.0, **_ignored):
        return {
            url: Verdict(url, DEAD if url.endswith("/c") else LIVE)
            for url in urls
        }

    result = build_shortlist(
        pipeline=pipeline,
        tracker=tracker,
        options=ShortlistOptions(limit=1, screen=True, pool=10, verify=True, verify_delay=0.0),
        screener=fake_screener,
        checker=fake_checker,
    )

    companies = [entry.ranked.row.company for entry in result.entries]
    assert "RowC Co" not in companies
    assert companies == ["RowA Co", "RowB Co"]  # RowA shortlisted, RowB rescued to the lane
    assert result.entries[1].overflow is True


def test_skipped_verification_is_not_folded_into_nothing_dead(tmp_path) -> None:
    """SKIPPED (LinkedIn, not fetched by policy) must not count as "verified clean".

    On 2026-08-15 a SKIPPED row was reported as if it had been checked and
    found live, and a dead posting rode a `SKIPPED` verdict onto the
    shortlist. `unchecked` exists so the caller can say "not checked" instead
    of implying it passed verification.
    """
    rows = [
        _row("https://example.invalid/skipped", "SkipCo", "AI Engineer", "Halifax, NS"),
        _row("https://example.invalid/live", "LiveCo", "AI Engineer", "Halifax, NS"),
    ]
    pipeline = _write(tmp_path, "pipeline.md", _pipeline(rows))
    tracker = _write(tmp_path, "applications.md", "")

    def fake_checker(urls, *, delay_s=1.0, **_ignored):
        return {
            url: Verdict(url, SKIPPED if url.endswith("/skipped") else LIVE)
            for url in urls
        }

    result = build_shortlist(
        pipeline=pipeline,
        tracker=tracker,
        options=ShortlistOptions(limit=10, verify=True, verify_delay=0.0),
        checker=fake_checker,
    )

    assert result.rejected == {}
    assert result.unchecked == 1
    companies = {entry.ranked.row.company for entry in result.entries}
    assert companies == {"SkipCo", "LiveCo"}


def test_progress_is_reported_before_each_slow_call(tmp_path) -> None:
    """`--screen --verify` is minutes of wall time; the operator needs to see
    it start, not just see the result. This pins the two lines the command
    used to print immediately before the screen call and the verify call —
    losing them during the extraction to `build_shortlist` would make the
    command look hung with no way to tell.
    """
    rows = [
        _row(f"https://example.invalid/{i}", f"Company{i}", "AI Engineer", "Halifax, NS")
        for i in range(5)
    ]
    pipeline = _write(tmp_path, "pipeline.md", _pipeline(rows))
    tracker = _write(tmp_path, "applications.md", "")

    def fake_screener(pairs):
        return (
            {index: Screened(index=index, keep=True, fit=5.0, reason="") for index in range(1, len(pairs) + 1)},
            "",
        )

    def fake_checker(urls, *, delay_s=1.0, **_ignored):
        return {url: Verdict(url, LIVE) for url in urls}

    messages: list[str] = []

    build_shortlist(
        pipeline=pipeline,
        tracker=tracker,
        options=ShortlistOptions(limit=2, screen=True, pool=5, verify=True, verify_delay=0.0),
        screener=fake_screener,
        checker=fake_checker,
        progress=messages.append,
    )

    assert messages == [
        "screening 5 rows through MiniMax…",
        "verifying 5 candidates…",
    ]


def test_a_toronto_heavy_inbox_still_surfaces_remote_region_and_startup_work(tmp_path) -> None:
    """2026-09-16: 95 northern rows sat in the inbox while the top 30 was half
    Greater Toronto and had none. Since 2026-10-19 the shortlist reserves its
    slots for remote regions and start-ups (the source board says which)."""
    rows = [
        _row(f"https://example.invalid/t{i}", f"Startup{i}", "AI Engineer", "Toronto, Ontario", posted="2026-09-15")
        for i in range(40)
    ] + [
        _row("https://example.invalid/yk", "Government of Yukon", "Systems Analyst", "Whitehorse, Yukon",
             posted="2026-09-10"),
        _row("https://example.invalid/hn", "Seedco", "AI Engineer", "Remote, Canada",
             posted="2026-09-10", source="hn_who_is_hiring"),
    ]
    pipeline = _write(tmp_path, "pipeline.md", _pipeline(rows))
    tracker = _write(tmp_path, "applications.md", "")

    result = build_shortlist(
        pipeline=pipeline, tracker=tracker, options=ShortlistOptions(limit=10), today=date(2026, 9, 16)
    )

    shown = {entry.ranked.row.company for entry in result.entries}
    assert "Government of Yukon" in shown
    assert "Seedco" in shown
    assert result.mix_shown.regions["north"] == 1
    assert result.mix_pool.total == 42
    assert {gap.reservation for gap in result.reservation_gaps} == {"remote region", "start-up"}
    assert all(gap.sourcing for gap in result.reservation_gaps)


def test_screen_then_verify_checks_reserved_rows_the_model_ranked_low(tmp_path) -> None:
    """Codex review 2026-09-16: verification used to check a plain head[:40]
    of the model-fit order, so a Yukon row the model ranked 60th was dropped
    before its link was ever fetched, and reported as a sourcing gap."""
    rows = [
        _row(f"https://example.invalid/t{i}", f"Startup{i}", "AI Engineer", "Toronto, Ontario", posted="2026-09-15")
        for i in range(59)
    ] + [
        _row("https://example.invalid/yk", "Government of Yukon", "Systems Analyst", "Whitehorse, Yukon",
             posted="2026-09-10"),
    ]
    pipeline = _write(tmp_path, "pipeline.md", _pipeline(rows))
    tracker = _write(tmp_path, "applications.md", "")

    def fake_screener(batch):
        # Keep everything, rank the Yukon row last.
        return {
            index: Screened(index=index, keep=True, fit=1.0 if company == "Government of Yukon" else 5.0, reason="")
            for index, (company, _role, _location) in enumerate(batch, start=1)
        }, ""

    checked: list[str] = []

    def fake_checker(urls, *, delay_s=1.0, **_ignored):
        checked.extend(urls)
        return {url: Verdict(url, LIVE) for url in urls}

    result = build_shortlist(
        pipeline=pipeline, tracker=tracker,
        options=ShortlistOptions(limit=10, screen=True, pool=60, verify=True),
        screener=fake_screener, checker=fake_checker, today=date(2026, 9, 16),
    )

    assert "https://example.invalid/yk" in checked
    assert "Government of Yukon" in {entry.ranked.row.company for entry in result.entries if not entry.overflow}


def test_one_posting_under_two_employer_spellings_fills_one_slot(tmp_path) -> None:
    """Codex review 2026-09-16, round 2: rank() dedups on (company, role), so the
    same URL under three spellings took three public-sector slots."""
    rows = [
        _row(f"https://example.invalid/t{i}", f"Startup{i}", "AI Engineer", "Toronto, Ontario", posted="2026-09-15")
        for i in range(20)
    ] + [
        _row("https://example.invalid/yk", name, "Systems Analyst", "Whitehorse, Yukon", posted="2026-09-10")
        for name in ("Government of Yukon", "Yukon Government", "Government of the Yukon")
    ]
    pipeline = _write(tmp_path, "pipeline.md", _pipeline(rows))
    tracker = _write(tmp_path, "applications.md", "")
    result = build_shortlist(
        pipeline=pipeline, tracker=tracker, options=ShortlistOptions(limit=10), today=date(2026, 9, 16)
    )
    urls = [entry.ranked.row.url for entry in result.entries]
    assert len(urls) == len(set(urls)) == 10


def test_lanes_come_first_and_written_off_rows_never_return(tmp_path) -> None:
    """2026-10-24: the top of the list was the best-overall public-sector rows,
    and 11 of 30 had been read and written off on earlier days. The two lanes
    now lead the list, and a settled URL is dropped before ranking."""
    rows = [
        _row(f"https://example.invalid/t{i}", f"Corp{i}", "AI Engineer", "Toronto, Ontario", posted="2026-10-20")
        for i in range(10)
    ] + [
        _row("https://example.invalid/yk", "Government of Yukon", "Systems Analyst", "Whitehorse, Yukon",
             posted="2026-10-10"),
        _row("https://example.invalid/hn", "Seedco", "AI Engineer", "Remote, Canada",
             posted="2026-10-10", source="hn_who_is_hiring"),
        _row("https://example.invalid/read", "Readco", "AI Engineer", "Remote, Canada",
             posted="2026-10-22", source="hn_who_is_hiring"),
    ]
    pipeline = _write(tmp_path, "pipeline.md", _pipeline(rows))
    tracker = _write(tmp_path, "applications.md", "")

    result = build_shortlist(
        pipeline=pipeline, tracker=tracker, options=ShortlistOptions(limit=5), today=date(2026, 10, 24),
        settled={"https://example.invalid/read"},
    )

    shown = [entry.ranked.row.company for entry in result.entries]
    assert shown[:2] == ["Government of Yukon", "Seedco"]
    assert "Readco" not in shown
    assert result.pending == 12
    assert result.excluded["already read and written off (data/postings-read.tsv, second look)"] == 1



def test_verification_keeps_the_best_overall_rows_the_plain_list_had(tmp_path) -> None:
    """Codex red team 2026-10-24: with the slate grouped by lane before
    verification re-chose from it, remote rows were read as the "best overall"
    and the real best-overall rows vanished (4/4/2 became 6/4/0)."""
    rows = [
        _row(f"https://example.invalid/c{i}", f"Corp{i}", "AI Engineer", "Toronto, Ontario", posted="2026-10-20")
        for i in range(6)
    ] + [
        _row(f"https://example.invalid/r{i}", f"Town{i}", "Systems Analyst", "Whitehorse, Yukon", posted="2026-10-01")
        for i in range(8)
    ] + [
        _row(f"https://example.invalid/s{i}", f"Seed{i}", "AI Engineer", "Remote, Canada",
             posted="2026-10-01", source="hn_who_is_hiring")
        for i in range(8)
    ]
    pipeline = _write(tmp_path, "pipeline.md", _pipeline(rows))
    tracker = _write(tmp_path, "applications.md", "")

    def lanes(result):
        return [entry.lane for entry in result.entries]

    plain = build_shortlist(pipeline=pipeline, tracker=tracker, options=ShortlistOptions(limit=10),
                            today=date(2026, 10, 24))
    verified = build_shortlist(
        pipeline=pipeline, tracker=tracker, options=ShortlistOptions(limit=10, verify=True),
        checker=lambda urls, delay_s: {}, today=date(2026, 10, 24),
    )
    assert lanes(plain) == lanes(verified)
    assert lanes(plain).count("best overall") == 2
