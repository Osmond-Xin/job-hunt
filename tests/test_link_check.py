"""Pre-submission link validation.

Every case here is a posting that actually wasted time on 2026-08-14, when 13
of 20 shortlisted candidates turned out to be unusable.
"""

from __future__ import annotations

import httpx

from job_hunt.services import link_check as lc


PADDING = (
    " We are a large employer offering competitive benefits, a pension plan, "
    "professional development, and a collaborative team environment. " * 12
)


def _classify(status=200, final="", body="", url="https://example.test/job/1", pad=True):
    """Bodies are padded because a page with almost no text is a JS shell, and
    the classifier deliberately refuses to call those LIVE."""
    return lc.classify(
        url, http_status=status, final_url=final or url, body=(body + PADDING) if pad else body
    )


def test_job_bank_expiry_is_a_410() -> None:
    """The cleanest expiry signal any source gives — four NS rows died this way."""
    v = _classify(status=410, body="Job posting expired - Job Bank")
    assert v.status == lc.EXPIRED and v.rejects


def test_hard_404_is_dead() -> None:
    assert _classify(status=404).status == lc.DEAD


def test_redirect_to_a_search_page_is_an_expiry_not_a_live_page() -> None:
    """LinkedIn answers a removed posting with HTTP 200 on a search page."""
    v = _classify(
        url="https://ca.linkedin.com/jobs/view/bi-lead-at-ns-4435310052",
        final="https://www.linkedin.com/jobs/senior-manager-jobs?trk=expired_jd_redirect",
        body="<html>plenty of unrelated jobs</html>",
    )
    assert v.status == lc.EXPIRED


def test_internal_competition_is_ineligible_not_dead() -> None:
    """GNB 17104: the API said "Open Competition"; only the form said otherwise."""
    body = (
        "This is an In-Service (closed) job opportunity, meaning applications are "
        "only open to employees and registered EEO candidates."
    )
    v = _classify(body=body)
    assert v.status == lc.INELIGIBLE and v.rejects


def test_nsgeu_external_applicant_clause_is_ineligible() -> None:
    body = (
        "This is a bargaining unit position initially restricted to current civil "
        "service employees represented by the NSGEU."
    )
    assert _classify(body=body).status == lc.INELIGIBLE


def test_candidate_inventory_is_not_a_vacancy() -> None:
    assert _classify(body="Requisition Type: Candidate Inventory").status == lc.NOT_A_VACANCY


def test_talent_pipeline_wording_is_not_a_vacancy() -> None:
    body = "This posting is not for an existing vacancy; we are building a talent pipeline."
    assert _classify(body=body).status == lc.NOT_A_VACANCY


def test_cloudflare_403_is_blocked_never_dead() -> None:
    """Dropping bot-walled hosts would silently discard real openings."""
    v = _classify(status=403, body="Attention Required! | Cloudflare")
    assert v.status == lc.BLOCKED
    assert not v.rejects


def test_captcha_served_with_http_200_is_blocked() -> None:
    """CGI's njoyn returns a Radware interstitial with a 200 status."""
    v = _classify(body="Radware Captcha Page We apologize for the inconvenience")
    assert v.status == lc.BLOCKED and not v.rejects


def test_healthy_posting_is_live() -> None:
    body = "Senior Solutions and Data Analyst. Closing Date: September 6, 2026."
    assert _classify(body=body).status == lc.LIVE


def test_linkedin_is_never_fetched() -> None:
    """The operator's account was restricted for high-volume access."""
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("LinkedIn must not be fetched")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    v, body = lc.check_url("https://ca.linkedin.com/jobs/view/123", client)
    assert v.status == lc.SKIPPED and not v.rejects
    assert body == ""


def test_terminal_verdicts_are_cached_and_not_refetched(tmp_path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(410, text="Job posting expired", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    cache = tmp_path / "verdicts.json"
    urls = ["https://example.test/job/1"]

    first = lc.check_urls(urls, client=client, cache_path=cache, sleep=lambda s: None, confirm=False)
    second = lc.check_urls(urls, client=client, cache_path=cache, sleep=lambda s: None, confirm=False)

    assert first[urls[0]].status == lc.EXPIRED
    assert second[urls[0]].status == lc.EXPIRED
    assert len(calls) == 1, "a dead posting does not come back; do not re-fetch it"


def test_live_verdicts_are_rechecked_rather_than_trusted(tmp_path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text="a normal job posting" + PADDING, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    cache = tmp_path / "verdicts.json"
    urls = ["https://example.test/job/2"]

    lc.check_urls(urls, client=client, cache_path=cache, sleep=lambda s: None, confirm=False)
    lc.check_urls(urls, client=client, cache_path=cache, sleep=lambda s: None, confirm=False)

    assert len(calls) == 2, "a live posting can close tomorrow"


def test_requests_are_spaced_out() -> None:
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok" + PADDING, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    lc.check_urls(
        ["https://a.test/job/1", "https://b.test/job/2", "https://c.test/job/3"],
        client=client,
        delay_s=1.5,
        sleep=slept.append,
        use_cache=False,
        confirm=False,
    )
    assert slept == [1.5, 1.5], "one pause between fetches, none before the first"


def test_annotate_pipeline_marks_only_rejected_rows(tmp_path) -> None:
    """Rejected rows are struck out in place so they never rank again."""
    pipeline = tmp_path / "pipeline.md"
    pipeline.write_text(
        "\n".join(
            [
                "- [ ] https://a.test/job/1 | ACME | Analyst | NS | source: x",
                "- [ ] https://b.test/job/2 | Beta | Engineer | MB | source: y",
                "- [ ] https://c.test/job/3 | Gamma | Developer | ON | source: z",
                "- [x] https://d.test/job/4 | Delta | Applied already | ON | source: z",
            ]
        ),
        encoding="utf-8",
    )
    verdicts = {
        "https://a.test/job/1": lc.Verdict("https://a.test/job/1", lc.EXPIRED, "HTTP 410", 410),
        "https://b.test/job/2": lc.Verdict("https://b.test/job/2", lc.LIVE),
        "https://c.test/job/3": lc.Verdict("https://c.test/job/3", lc.BLOCKED, "bot wall", 403),
    }

    changed = lc.annotate_pipeline(pipeline, verdicts)
    text = pipeline.read_text(encoding="utf-8")

    assert changed == 1
    assert text.splitlines()[0].startswith("- [!]")
    assert "expired — verified" in text.splitlines()[0]
    # A live row and a bot-walled row are both left alone: BLOCKED is not
    # evidence the posting is gone.
    assert text.splitlines()[1].startswith("- [ ]")
    assert text.splitlines()[2].startswith("- [ ]")
    assert text.splitlines()[3].startswith("- [x]")


def test_annotate_pipeline_is_a_no_op_without_rejections(tmp_path) -> None:
    pipeline = tmp_path / "pipeline.md"
    original = "- [ ] https://a.test/job/1 | ACME | Analyst | NS | source: x"
    pipeline.write_text(original, encoding="utf-8")

    changed = lc.annotate_pipeline(
        pipeline, {"https://a.test/job/1": lc.Verdict("https://a.test/job/1", lc.LIVE)}
    )

    assert changed == 0
    assert pipeline.read_text(encoding="utf-8") == original


# --- Findings from the codex + MiniMax adversarial review, 2026-08-15 -------
#
# Every case below deleted a LIVE posting before the review caught it. That is
# the expensive direction: the operator never learns the posting existed.


def test_nsgeu_internal_priority_is_not_ineligibility() -> None:
    """He CAN apply; internal candidates are simply ranked first.

    The operator had already decided such postings are worth applying to. The
    first version of this filter deleted them.
    """
    body = (
        "This is a bargaining unit position initially restricted to current civil "
        "service employees represented by the NSGEU. External applicants and current "
        "casual employees will only be considered if there are no qualified internal "
        "candidates."
    )
    assert _classify(body=body).status == lc.LIVE


def test_employees_and_the_public_is_not_ineligibility() -> None:
    body = "This opportunity is only open to current employees and members of the public."
    assert _classify(body=body).status == lc.LIVE


def test_negated_restriction_is_not_ineligibility() -> None:
    body = "This posting is not only open to current employees; external applicants are welcome."
    assert _classify(body=body).status == lc.LIVE


def test_in_service_with_no_external_route_is_still_ineligible() -> None:
    """The real GNB wording must keep working after the carve-outs were added."""
    body = (
        "This is an In-Service (closed) job opportunity, meaning applications are only "
        "open to employees and registered EEO candidates. Please indicate your SNB or "
        "GNB email address."
    )
    assert _classify(body=body).status == lc.INELIGIBLE


def test_talent_pool_in_a_footer_does_not_delete_a_real_vacancy() -> None:
    body = (
        "Senior Software Developer. Apply now. "
        "Footer: when you apply you will be added to our talent pool for future openings."
    )
    assert _classify(body=body).status == lc.LIVE


def test_candidate_inventory_is_still_caught() -> None:
    assert _classify(body="Requisition Type: Candidate Inventory").status == lc.NOT_A_VACANCY


def test_similar_jobs_sidebar_does_not_expire_the_page() -> None:
    body = (
        "Senior Data Analyst. Apply by September 30. "
        "Similar jobs: the previous posting is no longer available."
    )
    assert _classify(body=body).status == lc.LIVE


def test_job_slug_containing_search_is_not_a_redirect_to_search() -> None:
    """"/jobs/search" matched the real slug "/jobs/search-engineer-123"."""
    v = _classify(
        url="https://company.test/jobs/search-engineer-123?utm=x",
        final="https://company.test/jobs/search-engineer-123",
        body="Search Engineer posting. Apply now.",
    )
    assert v.status == lc.LIVE


def test_annotate_pipeline_matches_the_url_field_exactly(tmp_path) -> None:
    """Substring matching marked /job/10 dead because /job/1 was."""
    pipeline = tmp_path / "pipeline.md"
    pipeline.write_text(
        "- [ ] https://x.test/job/1 | A | Dead | NS | source: x\n"
        "- [ ] https://x.test/job/10 | B | Live | NS | source: x",
        encoding="utf-8",
    )
    lc.annotate_pipeline(
        pipeline,
        {"https://x.test/job/1": lc.Verdict("https://x.test/job/1", lc.EXPIRED, "HTTP 410", 410)},
    )
    lines = pipeline.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("- [!]")
    assert lines[1].startswith("- [ ]"), "the longer URL must not be collateral damage"


def test_text_rejection_needs_two_confirmations(tmp_path) -> None:
    """One dissent keeps the posting."""
    answers = iter([("CONFIRM: page says closed", ""), ("KEEP: I do not see that", "")])

    def runner(prompt, model, max_tokens, timeout):
        return next(answers)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="This posting has closed." + PADDING, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    out = lc.check_urls(
        ["https://x.test/job/1"],
        client=client,
        cache_path=tmp_path / "c.json",
        sleep=lambda s: None,
        confirm_runner=runner,
    )
    verdict = out["https://x.test/job/1"]
    assert verdict.status == lc.LIVE
    assert "did not confirm" in verdict.detail


def test_two_confirmations_allow_the_rejection(tmp_path) -> None:
    def runner(prompt, model, max_tokens, timeout):
        return "CONFIRM: 'This posting has closed'", ""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="This posting has closed." + PADDING, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    out = lc.check_urls(
        ["https://x.test/job/1"],
        client=client,
        cache_path=tmp_path / "c.json",
        sleep=lambda s: None,
        confirm_runner=runner,
    )
    assert out["https://x.test/job/1"].status == lc.EXPIRED


def test_confirmation_failure_keeps_the_posting(tmp_path) -> None:
    """No reviewer available must never mean "delete it anyway"."""
    def runner(prompt, model, max_tokens, timeout):
        return "", "mmx not on PATH"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="This posting has closed." + PADDING, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    out = lc.check_urls(
        ["https://x.test/job/1"],
        client=client,
        cache_path=tmp_path / "c.json",
        sleep=lambda s: None,
        confirm_runner=runner,
    )
    assert out["https://x.test/job/1"].status == lc.LIVE


def test_hard_status_codes_skip_confirmation(tmp_path) -> None:
    """404 and 410 are not a matter of interpretation; do not spend calls."""
    calls: list[str] = []

    def runner(prompt, model, max_tokens, timeout):
        calls.append("called")
        return "CONFIRM: yes", ""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(410, text="Job posting expired", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    out = lc.check_urls(
        ["https://x.test/job/1"],
        client=client,
        cache_path=tmp_path / "c.json",
        sleep=lambda s: None,
        confirm_runner=runner,
    )
    assert out["https://x.test/job/1"].status == lc.EXPIRED
    assert calls == []


def test_client_side_shell_is_not_reported_as_live() -> None:
    """GNB's Oracle page renders 561 chars of chrome and no job description."""
    shell = "<html><head><title>Careers</title></head><body><div id='root'></div></body></html>"
    v = _classify(body=shell, pad=False)
    assert v.status == lc.BLOCKED
    assert not v.rejects
    assert "human" in v.detail


def test_a_text_based_rejection_expires_from_the_cache(tmp_path) -> None:
    """Employers re-post requisitions; a phrase match is a reading, not a fact."""
    import json
    from datetime import date, timedelta

    cache = tmp_path / "c.json"
    old = (date(2026, 8, 15) - timedelta(days=lc.CACHE_DAYS_TEXT + 1)).isoformat()
    cache.write_text(
        json.dumps({"https://x.test/job/1": {
            "status": lc.INELIGIBLE, "detail": "internal", "http_status": 200, "checked": old,
        }}),
        encoding="utf-8",
    )

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text="A perfectly ordinary posting." + PADDING, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    out = lc.check_urls(
        ["https://x.test/job/1"], client=client, cache_path=cache,
        sleep=lambda s: None, today=date(2026, 8, 15), confirm=False,
    )
    assert calls, "a stale text verdict must be re-checked"
    assert out["https://x.test/job/1"].status == lc.LIVE


def test_a_410_is_trusted_for_much_longer(tmp_path) -> None:
    import json
    from datetime import date, timedelta

    cache = tmp_path / "c.json"
    recent = (date(2026, 8, 15) - timedelta(days=30)).isoformat()
    cache.write_text(
        json.dumps({"https://x.test/job/1": {
            "status": lc.EXPIRED, "detail": "HTTP 410", "http_status": 410, "checked": recent,
        }}),
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("a 410 from 30 days ago should not be re-fetched")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = lc.check_urls(
        ["https://x.test/job/1"], client=client, cache_path=cache,
        sleep=lambda s: None, today=date(2026, 8, 15),
    )
    assert out["https://x.test/job/1"].status == lc.EXPIRED
