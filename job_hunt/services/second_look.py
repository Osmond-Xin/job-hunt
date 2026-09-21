"""Second look: re-check what the filters cut, against the posting's own text.

Why this exists. Every filter before this one reads a *title*. On 2026-09-21
forty-odd postings were read in full and the titles turned out to be the weak
signal in both directions:

- **Fits the titles hid.** Beacon Software's posting was a plain "Software
  Engineer" — the adjacent tier, ranked far below the cut — whose body asked
  for "projects using LLM APIs, prompt engineering, or agentic frameworks",
  1-4 years, Python. It was also hidden by the one-role-per-employer rule. Ada's
  was "Customer Solutions Consultant"; Chowbus's "POS Support Specialist" with
  *fluent Chinese and English required*. None carries an AI title.
- **Misfits the titles promoted.** Of the AI-titled rows that reached the top
  30, most collapsed on reading: 5-10 years, a co-op term, Go or TypeScript
  depth, SCADA commissioning, an SAP credential.

So a cut made on a title is a guess, and this module is the confirmation.
It takes the rows that were cut for *reversible* reasons, reads the body, and
sorts them into "the body argues for this one" and "the cut was right". It
admits nothing by itself — a person reads the rescued list — and it says how
many it could not read, because "nothing rescued" must not stand in for
"nothing checked".

Four populations are re-checked (see ``candidates``):

1. hidden by one-role-per-employer — the operator lifted that rule on
   2026-09-21 for a role that fits better than the one already applied to;
2. excluded as "large employer";
3. buried: on-target enough to survive, too generic a title to rank — fresh,
   and on a directly readable ATS host;
4. near misses the scan's positive title filter discarded before they ever
   reached the pipeline (``data/scan-near-misses.tsv``).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Awaitable, Callable, Iterable

from job_hunt.services.triage import (
    ADJACENT_ROLE_RE,
    AGGREGATOR_HOST_RE,
    AI_ROLE_RE,
    SOLO_ROLE_RE,
    PipelineRow,
    already_applied,
    applied_employer,
    excluded,
    score,
)

NEAR_MISS_PATH = Path("data/scan-near-misses.tsv")
# Every posting this pass has read, and what it found. Two jobs: the daily
# budget moves on through the backlog instead of re-reading the same forty rows,
# and a cut that was confirmed has a dated record saying so.
LOG_PATH = Path("data/second-look-log.tsv")
_LOG_HEADER = ["date", "url", "company", "role", "why_cut", "verdict", "rescue", "kill"]
FRESH_DAYS = 14
REVERSIBLE_EXCLUSIONS = frozenset({"large employer"})

# What the body of a fitting posting said, 2026-09-21. Phrases, not topics.
RESCUE_SIGNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("agentic / LLM work", re.compile(
        r"agentic|\bllms?\b|large language model|prompt engineering|\brag\b|langchain|langgraph|"
        r"\bai agents?\b|generative ai", re.I)),
    ("AI coding tools named", re.compile(
        r"claude code|copilot|\bcursor\b|ai[- ]assisted (coding|development|tools)|ai[- ]native|"
        r"ai coding", re.I)),
    ("works beside business users", re.compile(
        r"embed(?:ded)? (?:directly )?with|business units?|internal (?:tools|stakeholders|business)|"
        r"non-technical|requirements into|client[- ]facing|customer[- ]facing|onboarding|"
        r"implementation", re.I)),
    ("automates manual work", re.compile(
        r"workflow automation|automat(?:e|es|ing|ion of) [^.\n]{0,40}(?:manual|workflows?|processes)",
        re.I)),
    ("Chinese asked for", re.compile(
        r"mandarin|cantonese|fluent in chinese|chinese (?:and|&) english|english (?:and|&) chinese|"
        r"中文|普通话|国语", re.I)),
    ("junior-to-mid level", re.compile(
        r"\b[0-4]\s*(?:\+|(?:-|–|to)\s*[1-4]\s*\+?)\s*years?|new grad|early[- ]career|"
        r"entry[- ]level|\bassociate\b|intermediate", re.I)),
    ("equivalent experience accepted", re.compile(r"equivalent (?:combination|experience)", re.I)),
)

# A rescue needs one of these. The rest only support it: measured on the first
# live run (2026-09-21), "junior-to-mid level" plus "works beside business users"
# alone rescued BMO's "Personal Banking Associate" and a "Portfolio Assistant" —
# true of nearly every entry-level posting in any occupation.
STRONG_RESCUE = frozenset(
    {"agentic / LLM work", "AI coding tools named", "automates manual work", "Chinese asked for"}
)

# What the body of a posting that collapsed said. Each of these ended a
# candidate that day; together they confirm a cut rather than overturn it.
KILL_SIGNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("5+ years", re.compile(
        r"\b(?:[5-9]|1\d)\s*\+?\s*(?:(?:-|–|to)\s*\d+\s*\+?\s*)?years?", re.I)),
    ("co-op / students only", re.compile(
        r"co-?op (?:position|term|work term|student)|must be (?:currently )?enrolled|"
        r"returning to (?:school|studies)|no more than \d+ months", re.I)),
    ("French required", re.compile(
        r"french (?:is )?required|fluen\w+ in french|bilingual \(french|ma[iî]trise du français", re.I)),
    ("security clearance", re.compile(r"security clearance|secret clearance|reliability status", re.I)),
    ("management level", re.compile(
        r"experience level\)?:?\s*management|direct reports|people management", re.I)),
    ("named-stack depth he cannot claim", re.compile(
        r"(?:expert\w*|proficien\w+|fluen\w+|strong|deep|extensive|advanced)[^.\n]{0,50}"
        r"\b(?:typescript|golang|kotlin|c#|\.net|spring|kubernetes|databricks|snowflake|scala|rust|"
        r"c\+\+|react native|swift|java(?!script))\b", re.I)),
    # A posting written in French is a French-language workplace whatever its
    # title says; the title-level French filter only reads English titles.
    ("posting written in French", re.compile(
        r"(?:\b(?:nous|vous|notre|votre|équipe|poste|expérience|compétences|développement)\b"
        r"[\s\S]{0,4000}?){6}", re.I)),
    ("domain credential", re.compile(
        r"\b(?:scada|opc server|hmi design|plcs?)\b|sap implementation|mining[- ]related degree|"
        r"\bcpa\b|\bp\.?eng\b", re.I)),
)


@dataclass(frozen=True)
class Candidate:
    row: PipelineRow
    why_cut: str


@dataclass
class Finding:
    candidate: Candidate
    rescue: list[str] = field(default_factory=list)
    kill: list[str] = field(default_factory=list)
    unread: str = ""

    @property
    def rescued(self) -> bool:
        return bool(STRONG_RESCUE.intersection(self.rescue)) and not self.kill and not self.unread


def body_signals(text: str) -> tuple[list[str], list[str]]:
    """(rescue labels, kill labels) found in a posting's text."""
    rescue = [label for label, pattern in RESCUE_SIGNALS if pattern.search(text)]
    kill = [label for label, pattern in KILL_SIGNALS if pattern.search(text)]
    return rescue, kill


def _fresh(stamp: str, today: date) -> bool:
    try:
        return (today - datetime.strptime(stamp, "%Y-%m-%d").date()).days <= FRESH_DAYS
    except ValueError:
        return False


def load_near_misses(path: Path = NEAR_MISS_PATH, *, today: date | None = None) -> list[PipelineRow]:
    """Fresh rows the scan's positive title filter discarded."""
    today = today or date.today()
    if not path.exists():
        return []
    rows: list[PipelineRow] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for record in csv.DictReader(handle, delimiter="\t"):
            if not _fresh(record.get("posted") or record.get("first_seen") or "", today):
                continue
            rows.append(
                PipelineRow(
                    url=record.get("url", ""),
                    company=record.get("company", ""),
                    role=record.get("title", ""),
                    location=record.get("location", ""),
                    posted=record.get("posted", ""),
                    source=record.get("portal", ""),
                )
            )
    return rows


def reviewed_urls(path: Path = LOG_PATH) -> set[str]:
    if not path.exists():
        return set()
    with path.open(encoding="utf-8", newline="") as handle:
        return {record["url"] for record in csv.DictReader(handle, delimiter="\t") if record.get("url")}


def log_findings(findings: list["Finding"], path: Path = LOG_PATH, *, today: date | None = None) -> None:
    """Record what was read. An unread page is NOT recorded, so it is retried."""
    stamp = (today or date.today()).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        if new_file:
            writer.writerow(_LOG_HEADER)
        for item in findings:
            if item.unread:
                continue
            verdict = "rescued" if item.rescued else ("cut confirmed" if item.kill else "no signal")
            row = item.candidate.row
            writer.writerow([stamp, row.url, row.company, row.role, item.candidate.why_cut,
                             verdict, "; ".join(item.rescue), "; ".join(item.kill)])


def candidates(
    rows: Iterable[PipelineRow],
    *,
    shown_urls: set[str],
    seen_urls: set[str],
    seen_pairs: set[tuple[str, str]],
    seen_employers: dict[str, str],
    near_misses: Iterable[PipelineRow] = (),
    already_reviewed: set[str] | None = None,
    today: date | None = None,
    limit: int = 40,
) -> list[Candidate]:
    """Rows cut for a reversible reason, newest first, capped at ``limit``.

    The cap is shared out so one population cannot starve the others: a single
    employer with sixty open roles would otherwise fill the whole budget.
    """
    today = today or date.today()
    already_reviewed = already_reviewed or set()
    pools: dict[str, list[Candidate]] = {"employer": [], "excluded": [], "buried": [], "near": []}
    for row in rows:
        if row.url in seen_urls or row.url in shown_urls or already_applied(row, seen_pairs):
            continue
        if row.url in already_reviewed:
            continue
        if not (AI_ROLE_RE.search(row.role) or SOLO_ROLE_RE.search(row.role) or ADJACENT_ROLE_RE.search(row.role)):
            continue  # a bank teller at a large employer is not a reversible cut
        reason = excluded(row)
        if reason:
            if reason in REVERSIBLE_EXCLUSIONS and _fresh(row.posted, today):
                pools["excluded"].append(Candidate(row, reason))
            continue
        employer = applied_employer(row, seen_employers)
        if employer:
            if _fresh(row.posted, today):
                pools["employer"].append(Candidate(row, f"one role per employer ({employer})"))
            continue
        if not _fresh(row.posted, today) or AGGREGATOR_HOST_RE.search(row.url):
            continue
        if AI_ROLE_RE.search(row.role) or SOLO_ROLE_RE.search(row.role):
            continue  # these tiers already rank; they are not buried by their title
        points, _ = score(row, today=today)
        if points >= 1:
            pools["buried"].append(Candidate(row, "generic title, ranked below the cut"))
    for row in near_misses:
        if row.url and row.url not in seen_urls and row.url not in already_reviewed and not already_applied(row, seen_pairs):
            if not excluded(row):
                pools["near"].append(Candidate(row, "title unknown to the scan filter"))

    for pool in pools.values():
        pool.sort(key=lambda item: item.row.posted, reverse=True)
    picked: list[Candidate] = []
    taken: set[str] = set()
    while len(picked) < limit and any(pools.values()):
        for pool in pools.values():
            if pool and len(picked) < limit:
                item = pool.pop(0)
                if item.row.url not in taken:
                    taken.add(item.row.url)
                    picked.append(item)
    return picked


async def review(
    picked: list[Candidate],
    fetch: Callable[[str], Awaitable[str]],
    *,
    concurrency: int = 4,
) -> list[Finding]:
    """Read each candidate's posting and sort it. ``fetch`` returns body text."""
    import asyncio

    gate = asyncio.Semaphore(concurrency)

    async def one(item: Candidate) -> Finding:
        async with gate:
            try:
                text = await fetch(item.row.url)
            except Exception as exc:  # noqa: BLE001 - one dead page must not end the pass
                return Finding(item, unread=type(exc).__name__)
        if len(text or "") < 400:
            return Finding(item, unread="page too thin to judge")
        rescue, kill = body_signals(text)
        return Finding(item, rescue=rescue, kill=kill)

    return list(await asyncio.gather(*(one(item) for item in picked)))
