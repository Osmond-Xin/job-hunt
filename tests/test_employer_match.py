"""Tests for EmployerMatcher — the consolidated employer/role matching used
across the tracker-matching call sites (nodes/tracker.py, email/reconcile.py,
the apply CLI, email/gaps.py, checkup.py).
"""

from __future__ import annotations

from job_hunt.repositories.tracker_repo import TrackerEntry
from job_hunt.services.employer_match import EmployerMatcher, is_reliable_match


def entry(number: int, company: str, role: str, status: str = "Applied") -> TrackerEntry:
    return TrackerEntry(
        number=number,
        date="2026-08-28",
        company=company,
        role=role,
        score="N/A",
        status=status,
        pdf="✅",
        report="",
        notes="",
    )


def test_mutate_rejects_generic_token_company_collision():
    """Regression (2026-07-09): 'CoLab Software' must not match 'Jonas
    Software'. The shared generic token "Software" pushed the raw company
    ratio to 0.79 and once flipped the wrong tracker row to Applied.
    """
    matcher = EmployerMatcher([
        entry(126, "Jonas Software", "Junior AI Software Engineer"),
    ])
    match = matcher.best(company="CoLab Software", role="Forward Deployed Engineer", intent="mutate")
    assert match is None


def test_raw_match_still_matches_suffix_variants():
    """Regression: 'Mariner' (as typed) must still find 'Mariner Partners
    Inc.' through the core algorithm — this is find_match's own weighted
    score (company 0.65 + role 0.35), unthresholded, exactly as before this
    consolidation."""
    matcher = EmployerMatcher([
        entry(1, "Mariner Partners Inc.", "AI Engineer - Healthcare"),
    ])
    match_entry, score = matcher.raw_match(company="Mariner", role="AI Engineer - Healthcare")
    assert match_entry is not None
    assert match_entry.number == 1
    assert score >= 0.70


def test_mutate_matches_the_suffix_variant_a_human_already_confirmed():
    """'Mariner' (typed via --company, or an evaluated JD's own company field)
    must match 'Mariner Partners Inc.' under `mutate` at find_match's
    original 0.70 threshold — nodes/tracker.py's and apply.py's write paths
    act on a human's own identification, so `mutate` does not add reconcile's
    extra floor on top. Regression guard: an earlier version of this
    consolidation applied reconcile's floor to every `mutate` caller and
    broke this exact case (0.714 < 0.75), which would have made
    `apply --confirmed` create a duplicate row instead of updating #1."""
    matcher = EmployerMatcher([
        entry(1, "Mariner Partners Inc.", "AI Engineer - Healthcare"),
    ])
    match = matcher.best(company="Mariner", role="AI Engineer - Healthcare", intent="mutate")
    assert match is not None
    assert match.entry.number == 1
    assert match.score >= 0.70


def test_reconciles_extra_floor_rejects_what_mutate_accepts():
    """The same suffix-variant match is safe for `mutate` (a human already
    made the identification) but not for reconcile acting unattended on
    inbound mail. reconcile keeps its own stricter floor as an explicit
    check layered on top of a raw match — `is_reliable_match` — rather than
    `mutate` imposing it on every caller. This is the divergence the
    coordinator's correction restored: `mutate` and reconcile's policy are
    two different gates, not one."""
    matcher = EmployerMatcher([
        entry(1, "Mariner Partners Inc.", "AI Engineer - Healthcare"),
    ])
    matched_entry, score = matcher.raw_match(company="Mariner", role="AI Engineer - Healthcare")
    assert matched_entry is not None

    reliable = is_reliable_match(
        company="Mariner", role="AI Engineer - Healthcare",
        matched_company=matched_entry.company, matched_role=matched_entry.role,
        score=score,
    )
    assert reliable is False


def test_report_matches_a_decorated_role_mutate_would_reject():
    """The ATS receipt decorates the title; the tracker stores it plainly.
    `report` (gaps/checkup) must not flag this as a missing application, but
    `mutate` must not treat it as confirmation to write to the row — a
    decorated title is not evidence the role text is close enough."""
    matcher = EmployerMatcher([
        entry(1, "Cohere", "Forward Deployed Engineer, Agentic Platform"),
    ])
    company = "Cohere"
    role = "Forward Deployed Engineer, Agentic Platform (ET - Canada/US)"

    report_match = matcher.best(company=company, role=role, intent="report")
    assert report_match is not None
    assert report_match.basis == "decorated_role"

    mutate_match = matcher.best(company=company, role=role, intent="mutate")
    assert mutate_match is None


def test_report_matches_company_only_where_mutate_requires_a_role():
    """A bare acknowledgement names only the company. `report` should treat
    that as the known employer (company_only); `mutate` must refuse — there
    is no role text to confirm which row to write to, and the wrong write
    corrupts a real application."""
    matcher = EmployerMatcher([
        entry(1, "Clariti", "Forward Deployed Engineer"),
    ])
    company = "Clariti Cloud Inc."

    report_match = matcher.best(company=company, role=None, intent="report")
    assert report_match is not None
    assert report_match.basis == "company_only"

    mutate_match = matcher.best(company=company, role=None, intent="mutate")
    assert mutate_match is None


def test_alias_applies_to_both_intents():
    matcher = EmployerMatcher(
        [entry(1, "Safe Fleet", "Safety Engineer", "Applied")],
        aliases={"Seon": "Safe Fleet"},
    )
    mutate_match = matcher.best(company="Seon", role="Safety Engineer", intent="mutate")
    assert mutate_match is not None
    assert mutate_match.entry.number == 1

    report_match = matcher.best(company="Seon", role=None, intent="report")
    assert report_match is not None
    assert report_match.entry.number == 1


def test_unattended_gate_does_not_merge_two_distinct_postings_at_one_company():
    """Two real Microsoft postings must not collapse into one tracker row.
    Under `mutate`'s bare 0.70 the "Staff" posting scores 0.95 and matches —
    safe for `apply.py`'s attended path, where a human already identified the
    row, but not for an unattended writer like `nodes/tracker.py`. Measured:
    fuzzy role similarity alone can't tell this apart from a genuinely
    different role at the same company ("...Platform" vs "...Azure" scores
    0.889 — indistinguishable from the Staff/Senior pair's 0.897, and that
    second pair is already rejected by find_match's own exact-company floor).
    `is_reliable_match` — the floor every unattended writer applies — must
    reject the Staff/Senior pair too, by requiring one normalized title to
    contain the other rather than trusting the fuzzy score."""
    matcher = EmployerMatcher([
        entry(1, "Microsoft", "Senior Backend Engineer, Platform"),
    ])
    matched_entry, score = matcher.raw_match(
        company="Microsoft", role="Staff Backend Engineer, Platform"
    )
    assert matched_entry is not None
    assert score >= 0.90  # confirms `mutate` alone would treat this as a strong match

    attended = matcher.best(company="Microsoft", role="Staff Backend Engineer, Platform", intent="mutate")
    assert attended is not None  # apply.py's attended path is unaffected

    reliable = is_reliable_match(
        company="Microsoft", role="Staff Backend Engineer, Platform",
        matched_company=matched_entry.company, matched_role=matched_entry.role,
        score=score,
    )
    assert reliable is False


def test_unattended_gate_still_accepts_a_grade_suffix_on_the_same_title():
    """The unattended containment check must not reject a legitimate
    decoration of the same posting: "Software Developer" vs "Software
    Developer I" is exactly the "Software Developer" / "…(ET - Canada/US)"
    shape, not a seniority swap."""
    matcher = EmployerMatcher([
        entry(1, "Acme", "Software Developer"),
    ])
    matched_entry, score = matcher.raw_match(company="Acme", role="Software Developer I")
    assert matched_entry is not None

    reliable = is_reliable_match(
        company="Acme", role="Software Developer I",
        matched_company=matched_entry.company, matched_role=matched_entry.role,
        score=score,
    )
    assert reliable is True


def test_any_employer_expands_slug_tokens_through_an_alias():
    matcher = EmployerMatcher(
        [entry(1, "Safe Fleet", "Safety Engineer", "Applied")],
        aliases={"Seon": "Safe Fleet"},
    )
    matches = matcher.any_employer({"seon", "safety", "engineer"})
    assert [m.entry.number for m in matches] == [1]

    no_alias_matcher = EmployerMatcher([entry(1, "Safe Fleet", "Safety Engineer", "Applied")])
    assert no_alias_matcher.any_employer({"seon", "safety", "engineer"}) == []
