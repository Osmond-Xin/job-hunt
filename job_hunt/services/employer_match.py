"""Every way this system decides two employer/role names mean one tracker row.

Before this module, that decision was implemented six times at five
thresholds: ``TrackerRepository.find_match``, a second gate on top of it in
``email/reconcile.py``, three more matching strategies local to
``email/gaps.py``, a slug-token check in ``checkup.py``, and independently
scattered ``0.70`` literals in ``nodes/tracker.py`` and the apply CLI.

There are genuinely two matching problems here, with opposite cost
asymmetries:

- **"which row do I mutate?"** (``intent="mutate"``) — a false positive
  corrupts a real application's record. This is exactly ``find_match``'s
  algorithm at the same ``0.70`` threshold every caller already applied. It
  is safe at that bare threshold only for a caller a human is actually
  driving: ``cli/apply.py``'s manual-submission path, where a person typed
  ``--company``/``--role`` or ran ``--confirmed``. It is *not* safe, on its
  own, for a caller that writes unattended — ``nodes/tracker.py``'s
  ``write_tracker_addition`` and ``merge_or_update_tracker``, which run on
  every ``evaluate``/``evaluate-batch`` against ``jd_meta.company``/``.title``
  as the LLM extracted them from a scraped JD, with nobody confirming. Those
  callers layer ``is_reliable_match`` on top of a raw match before acting,
  the same stricter floor ``email/reconcile.py`` already applies for the same
  reason (acting on inbound mail with nobody in the loop) — one definition of
  "strict enough to act on without a human", used by every unattended writer.
- **"does this employer appear anywhere?"** (``intent="report"``) — a false
  negative hides a sent application, the failure this project cares most
  about. Loose: this folds in the fallbacks ``email/gaps.py`` used to avoid
  reporting a real application as missing over a decorated title or a
  legal-suffix mismatch.

``config/company-aliases.yml`` applies on every path through this module —
``EmployerMatcher`` loads it once and threads it through both intents and
``any_employer``, instead of only the one caller (``gaps.load_aliases``)
that read it before.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import AbstractSet, Literal, Mapping, Sequence

from rapidfuzz import fuzz

from job_hunt.models.tracker import TrackerEntry, normalize

Basis = Literal["exact", "alias", "company_role", "company_only", "decorated_role", "tokens"]

ALIAS_PATH = Path("config/company-aliases.yml")

# ---- find_match's own gates (unchanged from tracker_repo.TrackerRepository) ----
_COMPANY_WEIGHT = 0.65
_ROLE_WEIGHT = 0.35
# Generic tokens ("Software", "Inc", …) inflate the raw company ratio —
# "CoLab Software" vs "Jonas Software" scores 0.79 — and once matched a wrong
# tracker row on a real submission (2026-07-09). Distinct companies must also
# be similar on their distinctive tokens.
_DISTINCTIVE_GATE = 0.60
# When the company is an exact match, require strong role similarity too, to
# avoid conflating two different roles at the same company.
_EXACT_COMPANY_ROLE_FLOOR = 0.85

# ---- the bare 0.70 threshold find_match's raw score was checked against
# before this consolidation: `apply.py`'s attended `mutate` decision (a human
# typed --company/--role, or confirmed a submission, so the identification is
# already made), and `report`'s primary check before its two fallbacks. ----
MATCH_THRESHOLD = 0.70

# ---- the unattended floor, layered on top of a raw find_match-style score
# before it is safe to act *without* a human confirming the identification.
# This is NOT part of `EmployerMatcher.best(intent="mutate")` — that intent
# stays at the bare 0.70 above and is only for `apply.py`'s attended path.
# Every writer that acts with nobody in the loop applies this explicitly, via
# `is_reliable_match`: `email/reconcile.py` (inbound mail) and
# `nodes/tracker.py` (`write_tracker_addition` / `merge_or_update_tracker`,
# on every evaluate/evaluate-batch run, against LLM-extracted jd_meta). ----
MUTATE_SCORE_FLOOR = 0.75
MUTATE_ROLE_FLOOR_EXACT_COMPANY = 0.82
MUTATE_ROLE_FLOOR = 0.75

# gaps._company_only_match's bar for a fuzzy company-only hit (no role to
# confirm with, so the bar is high).
COMPANY_ONLY_THRESHOLD = 0.85

# cli/evaluation.py's `_partition_already_evaluated`: is a batch target's
# scraped (company, role) already a tracker row, so evaluate-batch can skip
# re-running it? Numerically the same as COMPANY_ONLY_THRESHOLD above but a
# different decision — this is checked against a role-inclusive `raw_match`
# score, not the company-only fallback. Deliberately stricter than
# MATCH_THRESHOLD: a false positive here silently drops a real job from the
# batch (the 2026-08-17 SIGA/Cohere incident), while a false negative only
# costs a duplicate evaluation — the direction this project would rather
# fail in. Do not retune without re-reading that incident.
ALREADY_EVALUATED_THRESHOLD = 0.85

_GENERIC_COMPANY_TOKENS = {
    "software", "inc", "corp", "corporation", "ltd", "llc", "co", "company",
    "technologies", "technology", "tech", "solutions", "systems",
    "group", "partners", "labs", "canada", "the",
}

# Words an ATS receipt adds to the employer's legal name but the tracker
# omits ("Clariti Cloud Inc." vs "Clariti"). Wider than
# _GENERIC_COMPANY_TOKENS — used only for `report`/`any_employer` lookups.
# Widening the `mutate` gate's token list would loosen the check `apply`
# uses to decide which row an application belongs to.
_REPORT_FILLER_TOKENS = _GENERIC_COMPANY_TOKENS | {"cloud", "consulting", "services", "holdings"}


@dataclass(frozen=True)
class Match:
    entry: TrackerEntry
    score: float
    basis: Basis


def distinctive_company_name(name: str) -> str:
    """Company name reduced to its distinctive tokens, joined, for fuzzy compare.

    Public home for what used to be ``tracker_repo._distinctive_company_name``.
    """
    tokens = [
        t for t in re.findall(r"[a-z0-9]+", name.lower())
        if t not in _GENERIC_COMPANY_TOKENS
    ]
    return "".join(tokens) or normalize(name)


def _report_tokens(name: str) -> frozenset[str]:
    """The company's own words, minus legal suffixes and industry filler."""
    tokens = frozenset(
        token
        for token in re.findall(r"[a-z0-9]+", name.lower())
        if token not in _REPORT_FILLER_TOKENS
    )
    return tokens or frozenset([normalize(name)])


def load_aliases(path: Path = ALIAS_PATH) -> dict[str, str]:
    """Mail-side employer name -> the name the tracker uses.

    Raw (human-readable) keys — ``EmployerMatcher`` builds both a
    normalize()-keyed lookup (exact substitution) and a token-level view
    (for ``any_employer``) from this.
    """
    if not path.exists():
        return {}
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(k): str(v) for k, v in (raw.get("aliases") or {}).items()}


def is_reliable_match(
    *, company: str | None, role: str | None,
    matched_company: str, matched_role: str, score: float,
) -> bool:
    """The floor every *unattended* writer layers on top of a raw
    find_match-style score before it is safe to act with nobody in the loop
    — ``email/reconcile.py`` (inbound mail) and ``nodes/tracker.py``
    (evaluate/evaluate-batch, against LLM-extracted ``jd_meta``). Not used by
    ``EmployerMatcher.best(intent="mutate")`` — that gate is for callers
    where a human already made the identification (``cli/apply.py``).

    Kept as a standalone function (not a method on ``EmployerMatcher``)
    because ``email/reconcile.py`` and ``nodes/tracker.py`` both apply it as
    their own explicit policy on top of a match, and ``email/review.py``
    still calls it by this contract via
    ``email/reconcile._is_reliable_tracker_match``.
    """
    if score < MUTATE_SCORE_FLOOR:
        return False
    if normalize(role or "") == normalize(matched_role):
        return True
    role_score = fuzz.token_set_ratio(matched_role, role or "") / 100
    if normalize(company or "") == normalize(matched_company):
        if role_score < MUTATE_ROLE_FLOOR_EXACT_COMPANY:
            return False
        # Fuzzy role similarity alone cannot separate "a decorated or
        # leveled variant of the same posting" from "two distinct postings
        # at the same company": "Senior Backend Engineer, Platform" vs
        # "Staff Backend Engineer, Platform" scores 0.897 here — measured
        # indistinguishable from "Senior Backend Engineer, Platform" vs
        # "Senior Backend Engineer, Azure" at 0.889, which find_match's own
        # exact-company floor (0.85, token_sort) already rejects as a
        # different role. Since merging two real postings into one row
        # destroys a record invisibly, while failing to merge only leaves a
        # duplicate row `tracker dedup` already exists to clean up, the
        # unattended path requires one normalized title to contain the
        # other — the relationship a legal-suffix/ATS decoration or a
        # trailing "I"/"II" grade actually has ("Software Developer" /
        # "Software Developer I"), and a seniority swap like Senior/Staff
        # does not.
        wanted_norm = normalize(role or "")
        matched_norm = normalize(matched_role)
        return wanted_norm in matched_norm or matched_norm in wanted_norm
    return role_score >= MUTATE_ROLE_FLOOR


class EmployerMatcher:
    """Every way this system decides two employer/role names mean one row.

    Aliases from ``config/company-aliases.yml`` apply on every path, not
    just one.
    """

    def __init__(
        self,
        entries: Sequence[TrackerEntry],
        aliases: Mapping[str, str] | None = None,
    ) -> None:
        self._entries = list(entries)
        aliases = aliases or {}
        self._alias_lookup = {normalize(k): v for k, v in aliases.items()}
        self._alias_token_pairs = [
            (_report_tokens(k), _report_tokens(v)) for k, v in aliases.items()
        ]

    def resolve_alias(self, company: str) -> str:
        """The tracker-side name for a mail-side name, if one is aliased.

        Public: callers that display the resolved name (``email/gaps.py``'s
        ``Gap.company``) need this independent of any matching call.
        """
        return self._alias_lookup.get(normalize(company), company)

    # ---- core scoring: identical to the pre-consolidation find_match ----

    def _core_best(
        self, *, company: str, role: str | None
    ) -> tuple[TrackerEntry | None, float, bool]:
        """Returns (entry, score, is_exact). ``is_exact`` means company and
        role both matched exactly after normalization."""
        company_norm = normalize(company)
        role_norm = normalize(role or "")
        best: TrackerEntry | None = None
        best_score = 0.0
        best_exact = False
        for entry in self._entries:
            entry_company_norm = normalize(entry.company)
            company_score = (
                1.0 if entry_company_norm == company_norm
                else fuzz.ratio(entry_company_norm, company_norm) / 100
            )
            if company_score < 1.0:
                distinctive = (
                    fuzz.ratio(
                        distinctive_company_name(entry.company),
                        distinctive_company_name(company),
                    )
                    / 100
                )
                if distinctive < _DISTINCTIVE_GATE:
                    continue
            if role_norm:
                entry_role_norm = normalize(entry.role)
                role_score = (
                    1.0 if entry_role_norm == role_norm
                    else fuzz.token_sort_ratio(entry.role, role or "") / 100
                )
            else:
                role_score = 0.0
            if company_score == 1.0 and role_score < _EXACT_COMPANY_ROLE_FLOOR:
                continue
            score = company_score * _COMPANY_WEIGHT + role_score * _ROLE_WEIGHT
            if score > best_score:
                best_score = score
                best = entry
                best_exact = company_score == 1.0 and role_score == 1.0
        return best, best_score, best_exact

    def raw_match(self, *, company: str | None, role: str | None) -> tuple[TrackerEntry | None, float]:
        """find_match's exact algorithm, no threshold applied.

        ``TrackerRepository.find_match`` used to delegate to this (a
        repository reaching into the service layer to do it); that method is
        gone now and every former caller that wants a raw (entry, score) pair
        constructs ``EmployerMatcher(tracker.parse())`` and calls this
        directly.
        """
        if not company:
            return None, 0.0
        entry, score, _ = self._core_best(company=self.resolve_alias(company), role=role)
        return entry, score

    def best(
        self, *, company: str, role: str | None, intent: Literal["mutate", "report"]
    ) -> Match | None:
        """intent='mutate': the gate before writing to a row on a caller's own
        identification — find_match's algorithm at its historical 0.70
        threshold, no fallbacks. Safe at that bare threshold only for a
        caller a human is actually driving (``cli/apply.py``'s
        manual-submission path). Every *unattended* writer (inbound mail in
        ``email/reconcile.py``; the LLM-extracted ``jd_meta`` in
        ``nodes/tracker.py``) needs a stricter check than this on top; that
        is ``is_reliable_match``, applied explicitly by each of them on top
        of ``raw_match``, not something this intent applies for you.
        intent='report': the loose gate for gaps/checkup — a miss hides a
        sent application, which is worse.
        """
        if not company:
            return None
        resolved = self.resolve_alias(company)
        if intent == "mutate":
            return self._best_mutate(company=resolved, role=role)
        return self._best_report(company=resolved, role=role)

    def _best_mutate(self, *, company: str, role: str | None) -> Match | None:
        entry, score, exact = self._core_best(company=company, role=role)
        if entry is None or score < MATCH_THRESHOLD:
            return None
        basis: Basis = "exact" if exact else "company_role"
        return Match(entry=entry, score=score, basis=basis)

    def _best_report(self, *, company: str, role: str | None) -> Match | None:
        entry, score, exact = self._core_best(company=company, role=role)
        if entry is not None and score >= MATCH_THRESHOLD:
            basis: Basis = "exact" if exact else "company_role"
            return Match(entry=entry, score=score, basis=basis)
        if role:
            # An ATS receipt decorates the title the tracker stores plainly:
            # "…, Agentic Platform (ET - Canada/US)" vs "…, Agentic Platform".
            # The company_role score above requires 0.85 role similarity once
            # the company is an exact hit, so a decorated title scores nothing.
            decorated = self._decorated_role_match(company=company, role=role)
            if decorated is not None:
                return decorated
        else:
            # An acknowledgement often names only the company; role scores
            # 0.0, so even an exact company lands under the threshold above.
            company_only = self._company_only_match(company=company)
            if company_only is not None:
                return company_only
        return None

    def _same_employer(self, entry_company: str, wanted_company: str) -> bool:
        """Same employer, allowing one side to name a department or legal suffix.

        The tracker records the department the competition was posted by
        ("Government of Manitoba — Health, Seniors and Long Term Care"); the
        SuccessFactors receipt names only the government. Requiring exact
        equality is the shape of false positive that makes a mailbox-gap
        check unusable.
        """
        if normalize(entry_company) == normalize(wanted_company):
            return True
        wanted_tokens = _report_tokens(wanted_company)
        entry_tokens = _report_tokens(entry_company)
        if not wanted_tokens or not entry_tokens:
            return False
        return wanted_tokens <= entry_tokens or entry_tokens <= wanted_tokens

    def _decorated_role_match(self, *, company: str, role: str) -> Match | None:
        wanted_role = normalize(role)
        for entry in self._entries:
            if not self._same_employer(entry.company, company):
                continue
            entry_role = normalize(entry.role)
            if not entry_role:
                continue
            if entry_role in wanted_role or wanted_role in entry_role:
                return Match(entry=entry, score=1.0, basis="decorated_role")
        return None

    def _company_only_match(self, *, company: str) -> Match | None:
        """Match on the company alone, on its distinctive tokens.

        "Clariti Cloud Inc." in a receipt and "Clariti" in the tracker are the
        same employer; the legal-suffix noise is exactly what the
        distinctive-token reduction strips. The bar is high because there is
        no role to confirm with.
        """
        wanted_tokens = _report_tokens(company)
        for entry in self._entries:
            if self._same_employer(entry.company, company):
                return Match(entry=entry, score=1.0, basis="company_only")
            entry_tokens = _report_tokens(entry.company)
            if not wanted_tokens or not entry_tokens:
                continue
            score = (
                fuzz.ratio(distinctive_company_name(entry.company), "".join(sorted(wanted_tokens)))
                / 100
            )
            if score >= COMPANY_ONLY_THRESHOLD:
                return Match(entry=entry, score=score, basis="company_only")
        return None

    def any_employer(self, tokens: AbstractSet[str]) -> list[Match]:
        """Slug-token lookup, for artifact directories that have no role.

        Aliases expand the query: if the token set encodes an alias's own
        distinctive words (e.g. a directory slug built from "Seon"), the
        canonical name's tokens ("Safe Fleet") are added to the comparison
        too, so the artifact still resolves to the tracker's row.
        """
        expanded = set(tokens)
        for alias_tokens, canonical_tokens in self._alias_token_pairs:
            if alias_tokens and alias_tokens <= expanded:
                expanded |= canonical_tokens
        matches: list[Match] = []
        for entry in self._entries:
            entry_tokens = _report_tokens(entry.company)
            overlap = entry_tokens & expanded
            if overlap:
                score = len(overlap) / max(len(entry_tokens), 1)
                matches.append(Match(entry=entry, score=score, basis="tokens"))
        return matches
