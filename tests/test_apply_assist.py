from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import typer
import yaml

from job_hunt.cli import (
    _answer_for_application_question,
    _apply_profile_values,
    _build_agent_apply_prompt,
    _extract_application_section,
    _fill_workday_textarea_answers,
    _filter_required_empty_fields,
    _import_resume_file,
    _copy_setup_examples,
    _shortlist_rows,
    _update_env_file,
    _find_saved_apply_answer,
    _find_report_answer,
    _infer_loop_target,
    _looks_like_honeypot_context,
    _loop_agent_apply_command,
    _parse_company_role_from_description,
    _report_fit_warnings,
    _radio_choice_for_question,
    _record_manual_submission,
    _load_saved_apply_answers,
    _resolve_report_path,
    apply_assist,
)
from job_hunt.repositories.tracker_repo import TRACKER_HEADER, TrackerEntry, TrackerRepository


def test_record_manual_submission_updates_existing_tracker_row(tmp_path) -> None:
    tracker_path = tmp_path / "applications.md"
    tracker_path.write_text(TRACKER_HEADER, encoding="utf-8")
    tracker = TrackerRepository(tracker_path)
    entry = TrackerEntry(
        number=1,
        date="2026-04-29",
        company="Acme",
        role="AI Engineer",
        score="4.0/5",
        status="Evaluated",
        pdf="❌",
        report="report.md",
        notes="apply",
    )
    tracker.append_entry(entry)

    updated = _record_manual_submission(
        tracker=tracker,
        tracker_entry=entry,
        company="Acme",
        role="AI Engineer",
        url="https://example.com/jobs/1",
        pdf=tmp_path / "cv.pdf",
    )

    assert updated.status == "Applied"
    assert updated.pdf == "✅"
    assert tracker.parse()[0].status == "Applied"


def test_record_manual_submission_imports_new_applied_row(tmp_path) -> None:
    tracker_path = tmp_path / "applications.md"
    tracker_path.write_text(TRACKER_HEADER, encoding="utf-8")
    tracker = TrackerRepository(tracker_path)

    entry = _record_manual_submission(
        tracker=tracker,
        tracker_entry=None,
        company="New Co",
        role="Data Engineer",
        url="https://example.com/jobs/2",
        pdf=None,
    )

    assert entry.number == 1
    assert entry.status == "Applied"
    assert entry.report.startswith("manual:")
    assert entry.pdf == "❌"  # no PDF supplied → keep historical default
    assert tracker.parse()[0].company == "New Co"


def test_record_manual_submission_without_url_creates_row(tmp_path) -> None:
    """Applications found without a URL (LinkedIn browsing, a referral) must
    still land in the tracker — url is not required to record."""
    tracker_path = tmp_path / "applications.md"
    tracker_path.write_text(TRACKER_HEADER, encoding="utf-8")
    tracker = TrackerRepository(tracker_path)

    entry = _record_manual_submission(
        tracker=tracker,
        tracker_entry=None,
        company="Referral Co",
        role="Data Engineer",
        url=None,
        pdf=None,
    )

    assert entry.status == "Applied"
    assert tracker.parse()[0].company == "Referral Co"
    # No "url=None" text — the note should say plainly there was no URL.
    assert "url=None" not in entry.notes
    assert "no URL" in entry.notes


_APPLY_ASSIST_DEFAULTS = dict(
    tracker_id=None,
    company=None,
    role=None,
    pdf=None,
    cover_letter_pdf=None,
    no_browser=False,
    headless=False,
    auto_fill=True,
    fill_only=False,
    confirmed=False,
    auto_submit=False,
    low_score_override=False,
)


def test_apply_assist_requires_url_without_no_browser(capsys) -> None:
    """A missing url with no --no-browser must fail fast, naming the reason —
    never silently skip opening the browser."""
    with pytest.raises(typer.Exit) as excinfo:
        apply_assist(url=None, **_APPLY_ASSIST_DEFAULTS)

    assert excinfo.value.exit_code == 1
    out = capsys.readouterr().out
    assert "no-browser" in out
    assert "URL" in out


def test_apply_assist_no_browser_allows_missing_url(monkeypatch) -> None:
    """--no-browser is the flag that makes a missing url legal; execution must
    proceed past the gate instead of raising."""

    class _GateCleared(Exception):
        pass

    def _raise(*args, **kwargs):
        raise _GateCleared("reached load_settings — url gate did not block")

    monkeypatch.setattr("job_hunt.cli.apply.load_settings", _raise)

    kwargs = dict(_APPLY_ASSIST_DEFAULTS)
    kwargs["no_browser"] = True
    with pytest.raises(_GateCleared):
        apply_assist(url=None, **kwargs)


def test_apply_assist_url_provided_without_no_browser_still_passes_gate(monkeypatch) -> None:
    """Existing with-URL behaviour is unchanged: a URL provided without
    --no-browser must not trip the "missing url" gate."""

    class _GateCleared(Exception):
        pass

    def _raise(*args, **kwargs):
        raise _GateCleared("reached load_settings — url gate did not block")

    monkeypatch.setattr("job_hunt.cli.apply.load_settings", _raise)

    kwargs = dict(_APPLY_ASSIST_DEFAULTS)
    with pytest.raises(_GateCleared):
        apply_assist(url="https://example.com/jobs/1", **kwargs)


def test_apply_review_json_persists_pdf_and_validation_issues(tmp_path) -> None:
    """End-to-end check on the audit fields the user cares about for high-frequency runs.

    Asserts that ``_write_apply_review_summary`` writes:
    - ``pdf`` as the file path when ``pdf`` was attached
    - ``validation_issues`` as a list of {code, message, details} dicts

    This is the schema the user reads to decide whether a clean session can be
    auto-submitted or recorded; the code path is identical for Workday and
    non-Workday flows.
    """
    import json as _json
    from job_hunt.cli import _write_apply_review_summary
    from job_hunt.services.workday.review_gate import ReviewIssue, ISSUE_DATE_MISMATCH

    art = tmp_path / "apply-session"
    art.mkdir()
    pdf = tmp_path / "Example_Candidate_Resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 stub\n")
    screenshot = art / "apply-review-abc123.png"
    screenshot.touch()

    _write_apply_review_summary(
        artifact_dir=art,
        url="https://acme.wd5.myworkdayjobs.com/job/1",
        final_url="https://acme.wd5.myworkdayjobs.com/job/1",
        title="Acme - Senior Engineer",
        company="Acme",
        role="Senior Engineer",
        report_context=None,
        filled=["First Name", "Last Name"],
        skipped=[],
        answers=[],
        required_empty=[],
        actions=["Submit"],
        screenshot=screenshot,
        pdf=pdf,
        role_warnings=[],
        validation_issues=[
            ReviewIssue(
                code=ISSUE_DATE_MISMATCH,
                message="dates wrong",
                details={"expected_start": "01/2026"},
            ),
        ],
    )

    payload = _json.loads((art / "apply-review.json").read_text())
    assert payload["pdf"] == str(pdf)
    assert payload["validation_issues"] == [
        {
            "code": ISSUE_DATE_MISMATCH,
            "message": "dates wrong",
            "details": {"expected_start": "01/2026"},
        }
    ]


def test_apply_review_json_empty_validation_issues_when_clean(tmp_path) -> None:
    """Clean Workday Review writes empty list, not null — easy to scan with `jq`."""
    import json as _json
    from job_hunt.cli import _write_apply_review_summary

    art = tmp_path / "clean-session"
    art.mkdir()
    screenshot = art / "apply-review-clean.png"
    screenshot.touch()

    _write_apply_review_summary(
        artifact_dir=art, url="https://x", final_url="https://x", title="t",
        company="C", role="R", report_context=None,
        filled=[], skipped=[], answers=[], required_empty=[], actions=[],
        screenshot=screenshot, pdf=None, role_warnings=[],
    )

    payload = _json.loads((art / "apply-review.json").read_text())
    assert payload["validation_issues"] == []
    assert payload["pdf"] is None


def test_apply_review_json_carries_schema_version(tmp_path) -> None:
    """`schema_version` lets downstream `jq` / dashboard tooling reason about
    breaking changes without sniffing field shapes."""
    import json as _json
    from job_hunt.cli import _write_apply_review_summary, APPLY_REVIEW_SCHEMA_VERSION

    art = tmp_path / "session"
    art.mkdir()
    screenshot = art / "shot.png"
    screenshot.touch()

    _write_apply_review_summary(
        artifact_dir=art, url="https://x", final_url="https://x", title="t",
        company="C", role="R", report_context=None,
        filled=[], skipped=[], answers=[], required_empty=[], actions=[],
        screenshot=screenshot, pdf=None, role_warnings=[],
    )

    payload = _json.loads((art / "apply-review.json").read_text())
    assert payload["schema_version"] == APPLY_REVIEW_SCHEMA_VERSION
    assert isinstance(payload["schema_version"], int)


def test_record_manual_submission_new_row_with_pdf_marks_check(tmp_path) -> None:
    """Audit fix: a new Applied row created with --pdf must record pdf=✅."""
    tracker_path = tmp_path / "applications.md"
    tracker_path.write_text(TRACKER_HEADER, encoding="utf-8")
    tracker = TrackerRepository(tracker_path)
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 stub\n")

    entry = _record_manual_submission(
        tracker=tracker,
        tracker_entry=None,
        company="Brand New Co",
        role="AI Engineer",
        url="https://example.com/jobs/3",
        pdf=pdf,
    )

    assert entry.pdf == "✅"
    parsed = tracker.parse()[0]
    assert parsed.pdf == "✅"
    assert parsed.status == "Applied"


def test_application_question_answer_returns_empty_without_saved_or_report() -> None:
    """Without a saved or report-derived answer, the helper must not synthesise one."""
    answer = _answer_for_application_question(
        "Tell us about your experience in client facing roles.",
        company="Acme",
        role="AI Engineer",
    )
    assert answer == ""


def test_radio_choice_for_work_location_and_sponsorship() -> None:
    assert _radio_choice_for_question("Are you located in North America?") == "Yes"
    assert _radio_choice_for_question("Will you now or in the future require sponsorship?") == "No"
    assert _radio_choice_for_question("Are you currently located in EMEA & APAC?") == "No"


def test_apply_profile_values_reads_profile_config(tmp_path, monkeypatch) -> None:
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    (profile_dir / "profile.yml").write_text(
        """
candidate:
  full_name: "Test Candidate"
  email: "candidate@example.com"
  phone: "555-0101"
  location: "Toronto, ON, Canada"
  portfolio_url: "https://example.com"
  linkedin: "https://linkedin.com/in/test"
  github: "https://github.com/test"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    values = _apply_profile_values()

    assert values["name"] == "Test Candidate"
    assert values["email"] == "candidate@example.com"
    assert values["phone"] == "555-0101"
    assert values["location"] == "Toronto, ON, Canada"


def test_build_agent_apply_prompt_includes_safety_and_commands() -> None:
    prompt = _build_agent_apply_prompt(
        url="https://example.com/apply",
        company="Acme",
        role="AI Engineer",
        pdf=Path("output/resume.pdf"),
        tracker_id=12,
    )

    assert "Never click the final Submit/Apply button yourself." in prompt
    assert ".venv/bin/job-hunt apply https://example.com/apply" in prompt
    assert "--tracker-id 12" in prompt
    assert "--company Acme" in prompt
    assert "--role 'AI Engineer'" in prompt
    assert "--pdf output/resume.pdf" in prompt
    assert "--fill-only" in prompt
    assert "--no-browser --confirmed" in prompt
    assert "apply-replace-pdf '<new-resume.pdf>'" in prompt
    assert "apply-capture-page" in prompt


def test_extract_application_section_and_answer_from_report() -> None:
    report = """
# Report

## A) Fit
General context.

## G) Draft Application Answers

### Why Acme / Why this role?
I am excited by Acme because this role combines LLM systems and production engineering.

### Additional information
I bring AI orchestration, RAG, and product judgment.

## H) Other
Ignore me.
""".strip()
    section = _extract_application_section(report)

    assert "Why Acme" in section
    assert "Ignore me" not in section
    answer = _find_report_answer(
        "Why are you interested in this role?",
        {"application_section": section},
    )
    assert "LLM systems" in answer


def test_saved_apply_answers_are_reused_by_similar_question(tmp_path) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (artifact_dir / "apply-review.json").write_text(
        """
{
  "answers": [
    {
      "question": "Why are you interested in this role? *",
      "answer": "I am interested because this combines applied AI, data systems, and user impact."
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )

    saved = _load_saved_apply_answers(artifact_dir)
    answer = _find_saved_apply_answer(
        "Why are you interested in this role?",
        {"saved_answers": saved},
    )

    assert "applied AI" in answer


def test_saved_apply_answers_do_not_fill_references(tmp_path) -> None:
    context = {
        "saved_answers": [
            {
                "question": "(Optional) Please share any additional references you'd like to include.",
                "answer": "A stale reference answer should not be reused.",
            }
        ]
    }

    answer = _answer_for_application_question(
        "(Optional) Please share any additional references you'd like to include.",
        company="Acme",
        role="Research Fellow",
        report_context=context,
    )

    assert answer == ""


def _fake_workday_textarea(*, question_html: str, current_value: str = "", visible: bool = True):
    """Build a Playwright-like locator double for one textarea + its surrounding label."""
    area = MagicMock()
    area.is_visible = AsyncMock(return_value=visible)
    area.input_value = AsyncMock(return_value=current_value)
    # _field_context walks up to a few ancestors and reads inner_text. Returning the
    # question text directly from the locator's evaluate() short-circuits the search.
    area.evaluate = AsyncMock(return_value=question_html)
    area.inner_text = AsyncMock(return_value=question_html)
    fill_calls = []

    async def _fill(text: str) -> None:
        fill_calls.append(text)

    area.fill = AsyncMock(side_effect=_fill)
    # _field_contains_text reads input_value to verify the fill actually persisted.
    area.input_value = AsyncMock(side_effect=lambda: fill_calls[-1] if fill_calls else current_value)
    return area, fill_calls


def _fake_page_with_textareas(textareas):
    page = MagicMock()
    textarea_locator = MagicMock()
    textarea_locator.count = AsyncMock(return_value=len(textareas))
    textarea_locator.nth = MagicMock(side_effect=lambda i: textareas[i])
    rich_locator = MagicMock()
    rich_locator.count = AsyncMock(return_value=0)
    rich_locator.nth = MagicMock(side_effect=IndexError)

    def _locator(selector: str):
        if selector == "textarea":
            return textarea_locator
        if selector == '[role="textbox"][contenteditable="plaintext-only"]':
            return rich_locator
        return MagicMock(count=AsyncMock(return_value=0))

    page.locator = MagicMock(side_effect=_locator)
    return page


def test_workday_textarea_uses_saved_answer(monkeypatch) -> None:
    """When yaml ops don't cover a free-form question, the saved-answer fuzzy match wins."""
    area, fill_calls = _fake_workday_textarea(
        question_html="Why are you interested in this role? *"
    )
    page = _fake_page_with_textareas([area])
    # _field_context is async + uses page.evaluate; stub it to return the question text directly.
    # _fill_workday_textarea_answers (cli.apply) calls both as bare names, so
    # the patch has to land on cli.apply's own copy.
    monkeypatch.setattr(
        "job_hunt.cli.apply._field_context",
        AsyncMock(return_value="Why are you interested in this role? *"),
    )
    monkeypatch.setattr(
        "job_hunt.cli.apply._field_contains_text",
        AsyncMock(return_value=True),
    )

    saved = [
        {
            "question": "Why are you interested in this role?",
            "answer": "I am interested because this combines applied AI and product judgment.",
        }
    ]

    filled, skipped, answers = asyncio.run(
        _fill_workday_textarea_answers(
            page,
            company="Acme",
            role="AI Engineer",
            report_context={"saved_answers": saved},
        )
    )

    assert any("applied AI" in (call or "") for call in fill_calls), fill_calls
    assert answers == [
        {
            "question": "Why are you interested in this role? *",
            "answer": "I am interested because this combines applied AI and product judgment.",
        }
    ]
    assert filled and "Why are you interested" in filled[0]
    assert skipped == []


def test_workday_textarea_skips_already_filled_areas(monkeypatch) -> None:
    """A textarea that already has user content must not be overwritten."""
    area = MagicMock()
    area.is_visible = AsyncMock(return_value=True)
    area.input_value = AsyncMock(return_value="user-typed answer")
    area.fill = AsyncMock()
    page = _fake_page_with_textareas([area])
    # _fill_workday_textarea_answers (cli.apply) calls both as bare names, so
    # the patch has to land on cli.apply's own copy.
    monkeypatch.setattr("job_hunt.cli.apply._field_context", AsyncMock(return_value="Question?"))
    monkeypatch.setattr("job_hunt.cli.apply._field_contains_text", AsyncMock(return_value=True))

    filled, skipped, answers = asyncio.run(
        _fill_workday_textarea_answers(
            page, company=None, role=None, report_context=None
        )
    )

    area.fill.assert_not_called()
    assert filled == [] and skipped == [] and answers == []


def test_workday_resume_was_uploaded_detects_filename_in_body() -> None:
    from job_hunt.cli import _workday_resume_was_uploaded
    page = MagicMock()
    page.url = "https://acme.wd5.myworkdayjobs.com/job/123"
    locator = MagicMock()
    locator.inner_text = AsyncMock(
        return_value="Resume/CV Example_Candidate_Resume.pdf Successfully Uploaded"
    )
    page.locator = MagicMock(return_value=locator)

    pdf = Path("/output/Example_Candidate_Resume.pdf")
    assert asyncio.run(_workday_resume_was_uploaded(page, pdf))


def test_workday_resume_was_uploaded_returns_false_for_non_workday() -> None:
    from job_hunt.cli import _workday_resume_was_uploaded
    page = MagicMock()
    page.url = "https://jobs.ashbyhq.com/foo/apply"
    pdf = Path("/output/Example_Candidate_Resume.pdf")
    assert not asyncio.run(_workday_resume_was_uploaded(page, pdf))


def test_apply_profile_values_reads_auto_submit_gate(tmp_path, monkeypatch) -> None:
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    (profile_dir / "profile.yml").write_text(
        """
candidate:
  full_name: "Example Candidate"
apply:
  auto_submit_enabled: true
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    values = _apply_profile_values()
    assert values["apply_auto_submit_enabled"] is True


def test_apply_profile_values_auto_submit_default_false(tmp_path, monkeypatch) -> None:
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    (profile_dir / "profile.yml").write_text(
        """
candidate:
  full_name: "Example Candidate"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    values = _apply_profile_values()
    assert values["apply_auto_submit_enabled"] is False


def test_workday_resume_was_uploaded_returns_false_when_filename_absent() -> None:
    from job_hunt.cli import _workday_resume_was_uploaded
    page = MagicMock()
    page.url = "https://acme.wd5.myworkdayjobs.com/job/123"
    locator = MagicMock()
    locator.inner_text = AsyncMock(return_value="No resume uploaded yet")
    page.locator = MagicMock(return_value=locator)

    pdf = Path("/output/Example_Candidate_Resume.pdf")
    assert not asyncio.run(_workday_resume_was_uploaded(page, pdf))


def test_workday_textarea_marks_no_answer_questions_as_skipped(monkeypatch) -> None:
    area, _ = _fake_workday_textarea(question_html="Some unmatched custom question?")
    page = _fake_page_with_textareas([area])
    monkeypatch.setattr(
        "job_hunt.cli.apply._field_context",
        AsyncMock(return_value="Some unmatched custom question?"),
    )

    filled, skipped, answers = asyncio.run(
        _fill_workday_textarea_answers(
            page, company="Acme", role="Engineer", report_context=None
        )
    )

    assert filled == []
    assert answers == []
    assert any("no auto-answer" in line for line in skipped), skipped


def test_honeypot_context_is_detected() -> None:
    assert _looks_like_honeypot_context("Enter website. This input is for robots only, do not enter if you're human.")
    assert not _looks_like_honeypot_context("Portfolio website")


def test_update_env_file_preserves_existing_and_updates_values(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("EXISTING=1\nMINIMAX_MODEL=old\n", encoding="utf-8")

    _update_env_file(env, {"MINIMAX_MODEL": "new-model", "MINIMAX_API_KEY": "secret"})

    text = env.read_text(encoding="utf-8")
    assert "EXISTING=1" in text
    assert "MINIMAX_MODEL=new-model" in text
    assert "MINIMAX_API_KEY=secret" in text


def test_import_resume_file_from_markdown_updates_cv(tmp_path, monkeypatch) -> None:
    resume = tmp_path / "resume.md"
    resume.write_text("# Test Resume\n\nPython and AI systems.", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    _import_resume_file(resume)

    assert Path("profile/cv.md").read_text(encoding="utf-8").startswith("# Test Resume")
    assert (Path("storage/resumes") / "resume.md").exists()


def test_shortlist_rows_reads_pending_pipeline(tmp_path, monkeypatch) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "pipeline.md").write_text(
        "- [ ] https://example.com/job | Acme | AI Engineer | Remote Canada | source: ashby\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    rows = _shortlist_rows(limit=5)

    assert rows[0]["source"] == "pipeline"
    assert rows[0]["company"] == "Acme"
    assert rows[0]["role"] == "AI Engineer"


def test_copy_setup_examples_creates_portals_in_empty_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    _copy_setup_examples()

    assert Path("config/settings.yml").exists()
    portals = yaml.safe_load(Path("config/portals.yml").read_text(encoding="utf-8"))
    assert "tracked_companies" in portals


def test_resolve_report_path_from_markdown_link(tmp_path, monkeypatch) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    report = reports / "123-acme.md"
    report.write_text("# ok", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert _resolve_report_path("[123](reports/123-acme.md)") == Path("reports/123-acme.md")


def test_report_fit_warnings_for_skip_and_low_score() -> None:
    warnings = _report_fit_warnings({"score": "2.4/5", "recommendation": "SKIP"})

    assert any("SKIP" in warning for warning in warnings)
    assert any("low" in warning for warning in warnings)


def test_filter_required_empty_fields_removes_already_filled_labels() -> None:
    assert _filter_required_empty_fields(
        ["Name", "Phone Number", "Work authorization"],
        ["Name", "Phone Number"],
    ) == ["Work authorization"]


def test_parse_company_role_from_description() -> None:
    assert _parse_company_role_from_description("Cohere - Senior Software Engineer") == (
        "Cohere",
        "Senior Software Engineer",
    )
    assert _parse_company_role_from_description("AI Engineer at Acme") == ("Acme", "AI Engineer")


def test_loop_agent_apply_command_omits_unknown_fields() -> None:
    command = _loop_agent_apply_command(
        url="https://example.com/apply",
        company="Acme",
        role="AI Engineer",
        pdf=Path("output/resume.pdf"),
    )

    assert command == ".venv/bin/job-hunt agent-apply https://example.com/apply --company Acme --role 'AI Engineer' --pdf output/resume.pdf"


def test_infer_loop_target_uses_url_metadata_without_description(tmp_path, monkeypatch) -> None:
    tracker_path = tmp_path / "data" / "applications.md"
    tracker_path.parent.mkdir()
    tracker_path.write_text(TRACKER_HEADER, encoding="utf-8")
    tracker = TrackerRepository(tracker_path)
    tracker.append_entry(
        TrackerEntry(
            number=1,
            date="2026-04-29",
            company="Cohere",
            role="Senior Software Engineer, Security Agents",
            score="3.9/5",
            status="Evaluated",
            pdf="✅",
            report="reports/cohere.md",
            notes="apply",
        )
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    pdf = output_dir / "cohere-security-agents-resume.pdf"
    pdf.write_text("pdf", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    # _infer_loop_target (cli.apply) calls this as a bare name resolved from
    # its own module's import of services.web_extract, so the patch has to
    # land on cli.apply's copy, not job_hunt.cli's re-export.
    monkeypatch.setattr(
        "job_hunt.cli.apply._extract_loop_url_metadata",
        lambda url: {"company": "Cohere", "title": "Senior Software Engineer, Security Agents", "text": ""},
    )

    target = _infer_loop_target(
        url="https://jobs.ashbyhq.com/cohere/job-id/application",
        description="",
    )

    assert target["company"] == "Cohere"
    assert target["role"] == "Senior Software Engineer, Security Agents"
    assert target["tracker_entry"].number == 1
    assert target["pdf"] == Path("output/cohere-security-agents-resume.pdf")


def test_match_threshold_constant_is_read_at_call_sites(tmp_path, monkeypatch) -> None:
    """Verify that apply.py reads MATCH_THRESHOLD from employer_match, not a local literal.

    When MATCH_THRESHOLD is monkeypatched to a higher value, _infer_loop_target
    must respect that higher threshold, proving the call sites read the constant
    rather than a hard-coded literal. Specifically, we test that the line 791 check
    (if entry and score >= MATCH_THRESHOLD) changes behavior.
    """
    tracker_path = tmp_path / "data" / "applications.md"
    tracker_path.parent.mkdir()
    tracker_path.write_text(TRACKER_HEADER, encoding="utf-8")
    tracker = TrackerRepository(tracker_path)
    # Add an entry with company/role that scores between 0.70 and 0.85 when fuzzy-matched.
    # "Acme Inc" vs "Acme" scores around 0.76-0.78 (company weight 0.65, ~0.85 ratio).
    tracker.append_entry(
        TrackerEntry(
            number=1,
            date="2026-04-29",
            company="Acme Inc",
            role="Software Engineer",
            score="4.0/5",
            status="Evaluated",
            pdf="✅",
            report="reports/acme.md",
            notes="apply",
        )
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "job_hunt.cli.apply._extract_loop_url_metadata",
        lambda url: {"company": "Acme", "title": "Data Engineer", "text": ""},
    )

    # With default threshold (0.70), the moderately good company match (0.76+)
    # should pass and entry.role should be taken (line 791).
    target_default = _infer_loop_target(
        url="https://example.com/jobs/1",
        description="",
    )
    assert target_default["role"] == "Software Engineer", \
        "Default threshold 0.70 should apply entry.role from line 791"

    # Patch MATCH_THRESHOLD to 0.90 in the apply module's namespace.
    # Line 791 will now require score >= 0.90 before taking entry.role.
    monkeypatch.setattr("job_hunt.cli.apply.MATCH_THRESHOLD", 0.90)

    # With inflated threshold (0.90), the 0.76 score fails line 791, so
    # entry.role is not overridden; role remains "Data Engineer" from metadata.
    target_inflated = _infer_loop_target(
        url="https://example.com/jobs/1",
        description="",
    )
    assert target_inflated["role"] == "Data Engineer", \
        "Inflated threshold 0.90 should skip line 791 and keep metadata role"
