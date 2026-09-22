"""The second look, and the two scan-time losses found alongside it (2026-09-21)."""

from __future__ import annotations

import asyncio
from datetime import date

from job_hunt.services import scan as scan_module
from job_hunt.services import second_look as sl
from job_hunt.services.scan import ScannedJob, _is_near_miss, _title_matches
from job_hunt.services.triage import PipelineRow

TODAY = date(2026, 9, 21)


def _row(company: str, role: str, url: str, posted: str = "2026-09-19", location: str = "Toronto, ON") -> PipelineRow:
    return PipelineRow(url=url, company=company, role=role, location=location, posted=posted, source="getro")


# --- scan: a negative stem must not eat the words that merely contain it ---


def test_intern_does_not_discard_internal_and_sales_does_not_discard_salesforce():
    negatives = ["intern", "sales", "co-op"]
    assert _title_matches("Software Engineer, Internal Platform", ["software engineer"], negatives)
    assert _title_matches("Internal Tools Engineer", ["internal tools"], negatives)
    assert _title_matches("Salesforce Developer", ["developer"], negatives)


def test_the_negatives_still_bite_on_the_real_words():
    negatives = ["intern", "sales", "pharmac"]
    assert not _title_matches("Software Engineer Intern", ["software engineer"], negatives)
    assert not _title_matches("Sales Engineer", ["engineer"], negatives)
    assert not _title_matches("Pharmacy Systems Analyst", ["systems analyst"], negatives)


# --- scan: what the positive filter discards is written down, not lost ---


def test_a_near_miss_is_an_occupation_the_positive_list_did_not_know():
    negatives = ["sales", "cook"]
    assert _is_near_miss("Functional Analyst, Registrar's Office", negatives)
    assert _is_near_miss("AI Automation Specialist", negatives)
    assert not _is_near_miss("Sales Support Specialist", negatives)  # cut by a negative: a real cut
    assert not _is_near_miss("Line Cook", negatives)
    assert not _is_near_miss("Receptionist", negatives)


def test_near_misses_are_recorded_once(tmp_path, monkeypatch):
    ledger = tmp_path / "scan-near-misses.tsv"
    monkeypatch.setattr(scan_module, "NEAR_MISS_PATH", ledger)
    monkeypatch.setattr(scan_module, "_near_miss_urls", None)
    job = ScannedJob(title="Functional Analyst", url="https://x.test/1", company="MSVU", location="Halifax, NS", portal="getro", source="getro")
    scan_module._record_near_miss(job)
    scan_module._record_near_miss(job)
    lines = ledger.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2 and lines[0].startswith("url\t") and "Functional Analyst" in lines[1]


# --- second look: signals ---


def test_the_beacon_posting_is_rescued_by_its_body_not_its_title():
    body = (
        "1-4 years of full-time software engineering experience with strong fluency in Python. "
        "You have built projects using LLM APIs, prompt engineering, or agentic frameworks. "
        "Embed with internal business units such as Finance and Legal."
    )
    rescue, kill = sl.body_signals(body)
    assert "agentic / LLM work" in rescue and "works beside business users" in rescue
    assert "junior-to-mid level" in rescue
    assert kill == []


def test_the_postings_that_collapsed_are_confirmed_as_cuts():
    assert "5+ years" in sl.body_signals("6 to 10 years of experience across software engineering")[1]
    assert "co-op / students only" in sl.body_signals("As this is a co-op position, candidates must be enrolled")[1]
    assert "named-stack depth he cannot claim" in sl.body_signals("Expertise in Java and strong Kotlin skills")[1]
    assert "domain credential" in sl.body_signals("OPC server setup and HMI design in SCADA environments")[1]
    assert "co-op / students only" in sl.body_signals("no more than 18 months of professional experience")[1]


def test_chinese_being_required_is_a_reason_to_read_the_posting():
    assert "Chinese asked for" in sl.body_signals("Fluent in Chinese and English required")[0]


# --- second look: who gets re-checked ---


def _pick(rows, **kwargs):
    defaults = dict(shown_urls=set(), seen_urls=set(), seen_pairs=set(), seen_employers={}, today=TODAY)
    return sl.candidates(rows, **{**defaults, **kwargs})


def test_a_role_hidden_by_one_role_per_employer_is_re_checked():
    rows = [_row("Beacon Software", "Software Engineer", "https://jobs.ashbyhq.com/beacon/1")]
    picked = _pick(rows, seen_employers={"beacon software": "Beacon Software"})
    assert [c.why_cut for c in picked] == ["one role per employer (Beacon Software)"]


def test_a_generic_title_on_a_readable_host_is_re_checked_but_an_aggregator_is_not():
    rows = [
        _row("Acme", "Software Engineer", "https://jobs.ashbyhq.com/acme/1"),
        _row("Acme Two", "Software Engineer", "https://www.adzuna.ca/details/1"),
    ]
    assert [c.row.company for c in _pick(rows)] == ["Acme"]


def test_what_already_ranks_and_what_is_stale_are_left_alone():
    rows = [
        _row("Acme", "AI Engineer", "https://jobs.lever.co/acme/1"),  # ranks on its own title
        _row("Old Co", "Software Engineer", "https://jobs.lever.co/old/1", posted="2026-07-01"),
        _row("Senior Co", "Senior Software Engineer", "https://jobs.lever.co/sr/1"),  # a real cut
    ]
    assert _pick(rows) == []


def test_scan_near_misses_join_the_list_and_the_cap_is_shared():
    rows = [_row(f"Co{i}", "Software Engineer", f"https://jobs.lever.co/co{i}/1") for i in range(10)]
    near = [_row("MSVU", "Functional Analyst", "https://msvu.test/1")]
    picked = _pick(rows, near_misses=near, limit=4)
    assert len(picked) == 4
    assert any(c.why_cut == "title unknown to the scan filter" for c in picked)


def test_an_unreadable_page_is_reported_as_unread_never_as_clean():
    picked = [sl.Candidate(_row("A", "Software Engineer", "https://a.test"), "buried"),
              sl.Candidate(_row("B", "Software Engineer", "https://b.test"), "buried")]

    async def fetch(url: str) -> str:
        if "a.test" in url:
            raise TimeoutError
        return "agentic frameworks, 2+ years, Python. " * 20

    findings = asyncio.run(sl.review(picked, fetch))
    assert findings[0].unread == "TimeoutError" and not findings[0].rescued
    assert findings[1].rescued


def test_level_and_business_words_alone_do_not_rescue_anything():
    """First live run: they rescued BMO's Personal Banking Associate."""
    finding = sl.Finding(sl.Candidate(_row("BMO", "Analyst", "https://b.test"), "large employer"),
                         rescue=["works beside business users", "junior-to-mid level"])
    assert not finding.rescued
    finding.rescue.append("agentic / LLM work")
    assert finding.rescued


def test_a_non_technical_title_is_never_a_candidate():
    rows = [_row("BMO", "Personal Banking Associate", "https://bmo.test/1")]
    assert _pick(rows, seen_employers={"bmo": "BMO"}) == []


def test_java_depth_and_a_french_posting_confirm_the_cut():
    assert "named-stack depth he cannot claim" in sl.body_signals("Expertise in Java development")[1]
    assert "named-stack depth he cannot claim" not in sl.body_signals("Strong JavaScript skills")[1]
    french = "Nous cherchons un développeur. Vous joindrez notre équipe. Votre expérience et vos compétences comptent pour ce poste."
    assert "posting written in French" in sl.body_signals(french)[1]


def test_a_posting_already_read_is_not_read_again_but_an_unread_one_is_retried(tmp_path):
    log = tmp_path / "second-look-log.tsv"
    read = sl.Finding(sl.Candidate(_row("A", "Software Engineer", "https://jobs.lever.co/a/1"), "buried"), kill=["5+ years"])
    unread = sl.Finding(sl.Candidate(_row("B", "Software Engineer", "https://jobs.lever.co/b/1"), "buried"), unread="TimeoutError")
    sl.log_findings([read, unread], log, today=TODAY)
    done = sl.reviewed_urls(log)
    assert done == {"https://jobs.lever.co/a/1"}
    assert "cut confirmed" in log.read_text(encoding="utf-8")
    rows = [read.candidate.row, unread.candidate.row]
    assert [c.row.company for c in _pick(rows, already_reviewed=done)] == ["B"]


def test_the_iso_country_code_counts_as_canada_but_candidate_does_not():
    from job_hunt.services.scan import _passes_canada_filter

    assert _passes_canada_filter("Remote CAN")
    assert not _passes_canada_filter("Remote US")
    assert not _passes_canada_filter("Remote - candidates in EMEA")
