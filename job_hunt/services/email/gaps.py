"""Cross-reference LLM mailbox summaries against the tracker.

``email summarize`` writes high-quality classifications to
``data/email-summaries.jsonl``, but nothing reads them back — so an
acknowledgement for an application that was never recorded, or a rejection for
a row still marked Applied, stays invisible. This module reports those gaps.

It is deliberately read-only. An email body is untrusted input (see
``docs/design-notes.md`` P1), so the operator decides what to record.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz

from job_hunt.repositories.tracker_repo import (
    TrackerEntry,
    TrackerRepository,
    _distinctive_company_name,
    _GENERIC_COMPANY_TOKENS,
    normalize,
)
from job_hunt.services.email.summarize import SUMMARY_PATH

ALIAS_PATH = Path("config/company-aliases.yml")

# Categories that mean "this application exists".
_APPLIED_CATEGORIES = {"application_ack", "interview_invite", "offer", "rejection"}
# Categories that mean the tracker status is stale if it still says Applied.
_CLOSED_CATEGORIES = {"rejection"}
# Mail that says the application moved forward, not that one is missing.
_ADVANCE_CATEGORIES = {"interview_invite", "offer"}
_ADVANCED_STATUSES = {"Interview", "Offer", "Responded"}
# Statuses that already record an outcome, so a rejection mail is not news.
_SETTLED_STATUSES = {"Rejected", "Discarded", "SKIP", "Withdrawn", "Offer"}

_MATCH_THRESHOLD = 0.70


@dataclass
class Gap:
    kind: str  # "untracked" | "stale_status" | "advance"
    date: str
    company: str
    role: str
    category: str
    subject: str
    entry: TrackerEntry | None = None


def load_aliases(path: Path = ALIAS_PATH) -> dict[str, str]:
    """Mail-side employer name -> the name the tracker uses."""
    if not path.exists():
        return {}
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    pairs = (raw.get("aliases") or {}).items()
    return {normalize(str(k)): str(v) for k, v in pairs}


def _load_summaries(path: Path, since: str) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not row.get("job_related") or not row.get("company"):
            continue
        if row.get("category") not in _APPLIED_CATEGORIES:
            continue
        if (row.get("date") or "")[:10] < since:
            continue
        rows.append(row)
    rows.sort(key=lambda r: r.get("date") or "")
    return rows


_COMPANY_ONLY_THRESHOLD = 0.85


# Words an ATS receipt adds to the employer's legal name but the tracker omits.
# Kept local: widening the shared list would loosen the match gate that
# `apply` uses to decide which row an application belongs to.
_FILLER_TOKENS = _GENERIC_COMPANY_TOKENS | {"cloud", "consulting", "services", "holdings"}


def _distinctive_tokens(name: str) -> frozenset[str]:
    """The company's own words, minus legal suffixes and industry filler."""
    tokens = frozenset(
        token
        for token in re.findall(r"[a-z0-9]+", name.lower())
        if token not in _FILLER_TOKENS
    )
    return tokens or frozenset([normalize(name)])


def _company_only_match(tracker: TrackerRepository, company: str) -> TrackerEntry | None:
    """Match on the company alone, on its distinctive tokens.

    "Clariti Cloud Inc." in a receipt and "Clariti" in the tracker are the same
    employer; the legal-suffix noise is exactly what the distinctive-token
    reduction strips. The bar is high because there is no role to confirm with.
    """
    wanted_tokens = _distinctive_tokens(company)
    for entry in tracker.parse():
        if _same_employer(entry.company, company):
            return entry
        if not wanted_tokens or not _distinctive_tokens(entry.company):
            continue
        score = fuzz.ratio(_distinctive_company_name(entry.company), "".join(sorted(wanted_tokens))) / 100
        if score >= _COMPANY_ONLY_THRESHOLD:
            return entry
    return None


def _same_employer(entry_company: str, wanted_company: str) -> bool:
    """Same employer, allowing one side to name a department or legal suffix.

    The tracker records the department the competition was posted by
    ("Government of Manitoba — Health, Seniors and Long Term Care"); the
    SuccessFactors receipt names only the government. Requiring exact equality
    reported rows #725 and #728 as missing applications on 2026-09-01 — the
    shape of false positive that makes the whole check unusable, since six of
    the twelve gaps that day were name mismatches for work already recorded.
    """
    if normalize(entry_company) == normalize(wanted_company):
        return True
    wanted_tokens = _distinctive_tokens(wanted_company)
    entry_tokens = _distinctive_tokens(entry_company)
    if not wanted_tokens or not entry_tokens:
        return False
    return wanted_tokens <= entry_tokens or entry_tokens <= wanted_tokens


def _decorated_role_match(
    tracker: TrackerRepository, company: str, role: str
) -> TrackerEntry | None:
    wanted_role = normalize(role)
    for entry in tracker.parse():
        if not _same_employer(entry.company, company):
            continue
        entry_role = normalize(entry.role)
        if not entry_role:
            continue
        if entry_role in wanted_role or wanted_role in entry_role:
            return entry
    return None


def find_gaps(
    *,
    since: str,
    summary_path: Path = SUMMARY_PATH,
    tracker: TrackerRepository | None = None,
) -> list[Gap]:
    """Applications the mailbox knows about that the tracker does not."""
    tracker = tracker or TrackerRepository(Path("data/applications.md"))
    aliases = load_aliases()
    gaps: list[Gap] = []
    seen_untracked: set[str] = set()
    for row in _load_summaries(summary_path, since):
        company = aliases.get(normalize(row["company"]), row["company"])
        role = row.get("role") or ""
        entry, score = tracker.find_match(company=company, role=role)
        matched = entry is not None and score >= _MATCH_THRESHOLD
        if not matched and role:
            # An ATS receipt decorates the title the tracker stores plainly:
            # "…, Agentic Platform (ET - Canada/US)" vs "…, Agentic Platform".
            # find_match requires 0.85 role similarity once the company is an
            # exact hit, so the decorated title scores nothing at all.
            entry = _decorated_role_match(tracker, company, role)
            matched = entry is not None
        if not matched and not role:
            # An acknowledgement often names only the company. find_match scores
            # role at 0.0 then, so even an exact company lands under the
            # threshold; fall back to an exact company name instead.
            entry = _company_only_match(tracker, company)
            matched = entry is not None
        if not matched and row.get("category") in _ADVANCE_CATEGORIES:
            # An interview invitation from an employer already in the tracker is
            # not a missing application — it is a status the row has not caught
            # up with. Reporting it as "untracked" is how a real advance from
            # GNWT sat unnoticed for eleven days among forty-six false alarms.
            entry = _company_only_match(tracker, company)
            if entry is not None:
                if entry.status not in _ADVANCED_STATUSES | _SETTLED_STATUSES:
                    gaps.append(
                        Gap(
                            kind="advance",
                            date=(row.get("date") or "")[:10],
                            company=company,
                            role=role,
                            category=row.get("category") or "",
                            subject=(row.get("subject") or "")[:80],
                            entry=entry,
                        )
                    )
                continue
        if not matched:
            # One row per company/role, not one per acknowledgement email.
            key = (company + "|" + role).lower()
            if key in seen_untracked:
                continue
            seen_untracked.add(key)
            gaps.append(
                Gap(
                    kind="untracked",
                    date=(row.get("date") or "")[:10],
                    company=company,
                    role=role,
                    category=row.get("category") or "",
                    subject=(row.get("subject") or "")[:80],
                )
            )
            continue
        if row.get("category") in _CLOSED_CATEGORIES and entry.status not in _SETTLED_STATUSES:
            gaps.append(
                Gap(
                    kind="stale_status",
                    date=(row.get("date") or "")[:10],
                    company=company,
                    role=role,
                    category=row.get("category") or "",
                    subject=(row.get("subject") or "")[:80],
                    entry=entry,
                )
            )
    return gaps
