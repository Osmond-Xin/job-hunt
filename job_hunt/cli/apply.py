from __future__ import annotations

import shutil
import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
import typer
from job_hunt.config.models import load_settings
from job_hunt.graphs.evaluate_job import build_evaluate_job_graph
from job_hunt.models.events import ApplicationEvent
from job_hunt.repositories.tracker_repo import TrackerRepository
from job_hunt.repositories.email_event_repo import EmailEventRepository
from job_hunt.services.activity import ActivityEvent, ActivityLogger
from job_hunt.services.web import apply_ipc, apply_ops, apply_run_log, page_summary
from job_hunt.services.workday.required_empty import (
    filter_non_blocking_workday_skips as _filter_non_blocking_workday_skips,
    filter_required_empty_fields as _filter_required_empty_fields,
)

from job_hunt.services.apply.agent_prompt import (
    _build_agent_apply_prompt,
    _infer_loop_target,
    _loop_agent_apply_command,
)
from job_hunt.services.apply.answers import (
    _answer_for_application_question,
    _load_saved_apply_answers,
    _radio_choice_for_question,
)
from job_hunt.services.apply.artifacts import (
    _apply_artifact_dir,
)
from job_hunt.services.apply.linking import (
    _append_apply_review_event,
    _link_artifacts_to_row,
    _record_manual_submission,
    _tracker_entry_blocks_apply,
    _write_apply_review_summary,
)
from job_hunt.services.apply.reporting import (
    _load_apply_report_context,
    _report_fit_warnings,
    low_score_verdict,
)

from job_hunt.services.web.form_fill import (
    ApplyDoRefused,
    _advance_application_start,
    _attach_cover_letter,
    _attach_resume,
    _click_radio_near_text,
    _do_check_by_label,
    _do_click_by_label,
    _enter_application_form,
    _field_contains_text,
    _field_context,
    _fill_by_label_or_placeholder,
    _fill_contenteditable,
    _fill_location,
    _looks_like_submit_label,
    _required_empty_fields,
    _scroll_application_form,
    _wait_for_application_ready,
)
from job_hunt.services.workday.steps import (
    _collect_workday_review_issues,
    _do_fill_by_label,
    _do_select_by_label,
    _fill_workday_current_step,
    _maybe_workday_login,
    _recover_workday_error_page,
    _try_workday_final_submit,
    _workday_advance_all_steps,
    _workday_current_step,
    _workday_resume_was_uploaded,
)
from job_hunt.services.workday.detect import is_workday_page
from ._render import _short, console
from job_hunt.services.profile_loader import _apply_profile_values
from job_hunt.services.source_type import _resolve_source_type
from .outreach import _gate_outward_artifact
from . import app


# These two resolve a session for a CLI command and say why when they cannot,
# so they print and exit. That keeps them here rather than in
# services/apply/ -- "pure" in this refactor means no Playwright *and* no
# console (docs/apply-seam-plan.md, Phase 1).
def _active_apply_artifact_dir(session: str | None = None) -> Path:
    """Find the most-recent active session and warn if its heartbeat is stale.

    Phase 3.3: prefer ``.session.json`` heartbeat freshness; fall back to
    ``.cdp`` for sessions started by an older runner that doesn't write a
    heartbeat yet. ``session`` (a substring of the artifact dir name)
    disambiguates when several sessions are alive at once.
    """
    root = Path("artifacts/apply")
    alive = apply_ipc.find_alive_session_dirs(root)
    if session:
        matches = [d for d in alive if session in d.name]
        if len(matches) == 1:
            return matches[0]
        console.print(
            f"[red]--session '{session}' matches {len(matches)} live session(s): "
            f"{', '.join(d.name for d in matches) or 'none'}[/red]"
        )
        raise typer.Exit(1)
    if len(alive) > 1:
        console.print(
            "[red]Multiple live fill-only sessions; pick one with --session:[/red]"
        )
        for d in alive:
            console.print(f"- {d.name}")
        raise typer.Exit(1)
    art_dir = apply_ipc.find_active_session_dir(root)
    if art_dir is None:
        console.print(
            "[red]No active fill-only session found. Start one with: apply --fill-only[/red]"
        )
        raise typer.Exit(1)
    if not apply_ipc.session_is_alive(art_dir):
        console.print(
            f"[yellow]Warning:[/yellow] {art_dir.name} has no recent heartbeat — "
            "the fill-only loop may be dead. The command will be queued but may "
            "never run; consider restarting with `apply --fill-only`."
        )
    return art_dir


def _tracker_entry_by_id(tracker: TrackerRepository, tracker_id: int | None):
    if tracker_id is None:
        return None
    for entry in tracker.parse():
        if entry.number == tracker_id:
            return entry
    console.print(f"[red]Tracker row #{tracker_id} not found.[/red]")
    raise typer.Exit(1)


def _report_low_score_verdict(verdict) -> None:
    """Say what the ethical-use gate decided, and act on it.

    The decision itself is `services/apply/reporting.low_score_verdict`; the
    wording and the exit are here, where the rest of the user-facing text lives.
    """
    if verdict.overridden:
        console.print(
            f"[yellow]warning:[/yellow] tracker score {verdict.score}/5 < "
            f"{verdict.threshold} — applying anyway (--low-score-override)."
        )
        return
    if verdict.allowed:
        return
    console.print(
        f"[red]Aborting:[/red] tracker score {verdict.score}/5 is below the ethical-use "
        f"threshold of {verdict.threshold}/5.\n"
        f"Recruiter time has cost. Re-evaluate with `job-hunt evaluate`, or pass "
        f"`--low-score-override` if you have a specific reason to apply anyway."
    )
    raise typer.Exit(1)





@app.command("apply")
def apply_assist(
    url: str | None = typer.Argument(
        None,
        help=(
            "Application or job form URL to open. Required unless --no-browser "
            "is set — there is nothing to open in a browser otherwise. Recording "
            "a submission via --no-browser never requires one."
        ),
    ),
    tracker_id: int | None = typer.Option(None, help="Existing tracker row number to mark Applied after confirmation."),
    company: str | None = typer.Option(None, help="Company name if no tracker row is supplied."),
    role: str | None = typer.Option(None, help="Role title if no tracker row is supplied."),
    pdf: Path | None = typer.Option(None, help="Resume/CV PDF to attach if a file input is found."),
    cover_letter_pdf: Path | None = typer.Option(
        None,
        "--cover-letter-pdf",
        help="Cover-letter PDF to attach when the form has a 'Cover Letter' file input.",
    ),
    no_browser: bool = typer.Option(False, help="Skip opening Playwright; only record confirmation."),
    headless: bool = typer.Option(False, help="Run Chromium headless. Usually leave false for manual submit."),
    auto_fill: bool = typer.Option(True, help="Auto-fill recognized application fields before pausing for review."),
    fill_only: bool = typer.Option(False, "--fill-only", help="Fill form and keep browser open; skip terminal confirm. Use --confirmed separately to record."),
    confirmed: bool = typer.Option(False, "--confirmed", help="Skip browser and confirmation; record as Applied immediately (use after manual submission)."),
    auto_submit: bool = typer.Option(
        False,
        "--auto-submit",
        help=(
            "Click the final Submit button automatically when ALL gates pass: "
            "CLI flag set + profile.yml apply.auto_submit_enabled=true + Workday URL "
            "+ Review-gate validation_issues empty + required_empty empty. "
            "Off by default; falls back to manual submit when any gate fails."
        ),
    ),
    low_score_override: bool = typer.Option(
        False,
        "--low-score-override",
        help=(
            "Override the ethical low-score gate. By default, applying to a tracker "
            "row with weighted_total < 3.0 aborts. Set this flag if you have a "
            "specific reason to apply anyway (e.g. learning experience, network signal)."
        ),
    ),
) -> None:
    """Assist with an application. By default never clicks the final submit button.

    ``--auto-submit`` opts in to one-click submission, but the click only fires
    when every safety gate passes (see flag help). Any gate failure falls back
    to the normal "user submits manually" flow without partial state.
    """
    if url is None and not no_browser:
        console.print(
            "[red]URL is required unless --no-browser is set:[/red] there is "
            "nothing to open in a browser. Pass a URL, or add --no-browser to "
            "record an application found without one (e.g. from LinkedIn "
            "browsing or a referral)."
        )
        raise typer.Exit(1)

    settings = load_settings()
    tracker = TrackerRepository(Path("data/applications.md"))
    existing = _tracker_entry_by_id(tracker, tracker_id) if tracker_id is not None else None

    resolved_company = company or (existing.company if existing else None)
    resolved_role = role or (existing.role if existing else None)

    if pdf and not pdf.exists():
        console.print(f"[red]PDF not found:[/red] {pdf}")
        raise typer.Exit(1)

    if cover_letter_pdf and not cover_letter_pdf.exists():
        console.print(f"[red]Cover letter PDF not found:[/red] {cover_letter_pdf}")
        raise typer.Exit(1)

    # Profile gate: --auto-submit only matters when the user has also turned
    # auto_submit_enabled on in profile.yml. This is two-key safety so a stray
    # flag in shell history can't surprise-submit an application.
    auto_submit_profile_enabled = _apply_profile_values().get(
        "apply_auto_submit_enabled", False
    )
    # Mode gate: auto-submit is force-disabled in student mode regardless of
    # both other flags. Co-op / intern forms have higher per-employer variance
    # (custom questions, portal-specific consent) and the upside of one-click
    # submission is small there. See docs/design-notes.md §N.3.
    from job_hunt.services.profile_loader import current_mode as _read_mode
    operator_mode = _read_mode()
    auto_submit_active = bool(
        auto_submit and auto_submit_profile_enabled and operator_mode == "full"
    )
    if auto_submit and operator_mode == "student":
        console.print(
            "[yellow]--auto-submit ignored:[/yellow] mode=student in profile.yml. "
            "Auto-submit is restricted to full mode. Falling back to manual submit."
        )
    elif auto_submit and not auto_submit_profile_enabled:
        console.print(
            "[yellow]--auto-submit ignored:[/yellow] profile.yml is missing "
            "`apply.auto_submit_enabled: true`. Falling back to manual submit."
        )

    artifact_dir = _apply_artifact_dir(resolved_company, resolved_role)
    report_context = _load_apply_report_context(
        tracker=tracker,
        tracker_entry=existing,
        company=resolved_company,
        role=resolved_role,
    )
    if report_context is None:
        report_context = {}
    report_context["saved_answers"] = _load_saved_apply_answers(artifact_dir)

    _report_low_score_verdict(low_score_verdict(report_context, override=low_score_override))

    if confirmed:
        submitted = True
    elif no_browser:
        submitted = typer.confirm("Have you manually submitted this application?", default=False)
    else:
        browser_result = asyncio.run(
            _open_apply_page(
                url,
                pdf=pdf,
                cover_letter_pdf=cover_letter_pdf,
                headless=headless,
                auto_fill=auto_fill,
                company=resolved_company,
                role=resolved_role,
                fill_only=fill_only,
                artifact_dir=artifact_dir,
                report_context=report_context,
                auto_submit=auto_submit_active,
            )
        )
        if browser_result.get("deferred"):
            console.print("Fill-only session ended. No tracker changes made; record with --no-browser --confirmed after manual submit.")
            return
        submitted = browser_result["submitted"]

    if not submitted:
        ActivityLogger(settings.activity).emit(
            ActivityEvent(
                type="apply.cancelled",
                level="info",
                summary=f"Apply assist cancelled for {resolved_company or url}",
                mode=operator_mode,
                payload={"url": url, "company": resolved_company, "role": resolved_role},
            )
        )
        console.print("No tracker changes made.")
        return

    if not resolved_company or not resolved_role:
        console.print("[red]Company and role are required to record a submission without a tracker id.[/red]")
        raise typer.Exit(1)

    updated = _record_manual_submission(
        tracker=tracker,
        tracker_entry=existing,
        company=resolved_company,
        role=resolved_role,
        url=url,
        pdf=pdf,
    )
    event = ApplicationEvent(
        id=f"evt_{uuid.uuid4().hex}",
        source="system_apply",
        event_type="application_submitted",
        event_time=datetime.now(timezone.utc),
        company=resolved_company,
        role=resolved_role,
        job_url=url,
        sender="job-hunt",
        subject=f"Manual submission confirmed: {resolved_company} / {resolved_role}",
        snippet="User confirmed manual browser submission from job-hunt apply.",
        evidence=["manual confirmation"],
        confidence=1.0,
        tracker_entry_id=updated.number,
    )
    EmailEventRepository().append(event)
    ActivityLogger(settings.activity).emit(
        ActivityEvent(
            type="apply.submitted",
            level="info",
            summary=f"Application submitted: {resolved_company} / {resolved_role}",
            application_id=updated.number,
            mode=operator_mode,
            payload={"url": url, "pdf": str(pdf) if pdf else None},
        )
    )
    console.print(f"[green]Recorded Applied[/green] tracker row #{updated.number}: {updated.company} / {updated.role}")
    marker = _link_artifacts_to_row(pdf, updated, url)
    if marker:
        console.print(f"[dim]Linked {marker.parent.name} to row #{updated.number}.[/dim]")


@app.command("agent-apply")
def agent_apply_prompt(
    url: str = typer.Argument(..., help="Application or job form URL."),
    company: str | None = typer.Option(None, help="Company name for tracker recording."),
    role: str | None = typer.Option(None, help="Role title for tracker recording."),
    pdf: Path | None = typer.Option(None, help="Resume/CV PDF to attach."),
    tracker_id: int | None = typer.Option(None, help="Existing tracker row number."),
) -> None:
    """Print a Claude Code / Codex CLI runbook for assisted applications."""
    console.print(
        _build_agent_apply_prompt(
            url=url,
            company=company,
            role=role,
            pdf=pdf,
            tracker_id=tracker_id,
        ),
        soft_wrap=True,
    )


@app.command("loop")
def full_loop_from_url(
    url: str = typer.Argument(..., help="Job or application URL."),
    description: str | None = typer.Argument(None, help="Optional override, e.g. 'Cohere AI engineer security agents'."),
    evaluate_first: bool = typer.Option(False, "--evaluate", help="Run evaluation first, then infer tracker/report/PDF."),
    print_prompt: bool = typer.Option(True, "--prompt/--no-prompt", help="Print the copy-paste agent prompt."),
) -> None:
    """Prepare full-loop apply commands from a job or application URL."""
    if evaluate_first:
        console.print("[yellow]Running evaluation first. This may take a while.[/yellow]")
        graph = build_evaluate_job_graph()
        run_id = f"run_{uuid.uuid4().hex}"
        source_type = _resolve_source_type(url, "auto")
        result = asyncio.run(
            graph.ainvoke(
                {
                    "input": url,
                    "run_id": run_id,
                    "thread_id": run_id,
                    "source_type": source_type,
                    "url": url if source_type == "url" else None,
                },
                config={"configurable": {"thread_id": run_id}},
            )
        )
        if result.get("errors"):
            console.print("[yellow]Evaluation completed with warnings/errors:[/yellow]")
            for error in result["errors"]:
                console.print(f"- {error}")

    target = _infer_loop_target(url=url, description=description or "")
    if not target["company"] or not target["role"]:
        console.print("[yellow]Could not confidently infer company/role.[/yellow]")
        console.print("Run with --evaluate, or add an optional override description if the page blocks extraction.")
    if not target["pdf"]:
        console.print("[yellow]Could not find a role-specific PDF; using generic AI Engineer preview if available.[/yellow]")

    console.print("\n[bold]Inferred target[/bold]")
    console.print(f"Company: {target['company'] or '?'}")
    console.print(f"Role: {target['role'] or '?'}")
    console.print(f"PDF: {target['pdf'] or '?'}")
    if target.get("metadata"):
        meta = target["metadata"]
        console.print(f"URL extraction: {meta.get('adapter') or '?'} {meta.get('ats') or ''}".strip())
    if target.get("tracker_entry"):
        entry = target["tracker_entry"]
        console.print(f"Tracker: #{entry.number} {entry.score} {entry.status} {entry.report}")
        if _tracker_entry_blocks_apply(entry):
            console.print("[red]Warning:[/red] matched tracker/report suggests this may not be worth applying. Review before continuing.")

    command = _loop_agent_apply_command(url=url, company=target["company"], role=target["role"], pdf=target["pdf"])
    console.print("\n[bold]Run this in the agent[/bold]")
    console.print(command, soft_wrap=True)

    if print_prompt:
        console.print("\n[bold]Minimal prompt for another agent[/bold]")
        console.print(
            f"""读取 docs/full-loop-execution.md，按完整闭环执行这个职位。不要点击最终 Submit/Apply，我会手动提交。

url: {url}
先运行：

```bash
{command}
```
""",
            soft_wrap=True,
        )


@app.command("apply-replace-pdf")
def apply_replace_pdf(
    pdf: Path = typer.Argument(..., help="New PDF to upload into the open browser session."),
) -> None:
    """Replace the resume PDF in a running --fill-only browser session."""
    if not pdf.exists():
        console.print(f"[red]PDF not found:[/red] {pdf}")
        raise typer.Exit(1)
    art_dir = _active_apply_artifact_dir()
    apply_ipc.submit_command(
        art_dir,
        apply_ipc.COMMAND_TYPE_REPLACE_PDF,
        {"pdf": str(pdf.resolve())},
    )
    console.print(f"Replace request sent → {art_dir.name}")
    console.print(f"New PDF: {pdf}")
    console.print("Browser will update in ~2 seconds and take a new screenshot.")


@app.command("apply-capture-page")
def apply_capture_page() -> None:
    """Capture the current page in a running --fill-only browser session."""
    art_dir = _active_apply_artifact_dir()
    apply_ipc.submit_command(art_dir, apply_ipc.COMMAND_TYPE_CAPTURE_PAGE)
    console.print(f"Capture request sent → {art_dir.name}")
    console.print("Browser will capture the current page in ~2 seconds.")


@app.command("apply-refill-current-page")
def apply_refill_current_page() -> None:
    """Re-run auto-fill and PDF attachment on the current page of an active fill-only session."""
    art_dir = _active_apply_artifact_dir()
    apply_ipc.submit_command(art_dir, apply_ipc.COMMAND_TYPE_REFILL_CURRENT_PAGE)
    console.print(f"Refill request sent → {art_dir.name}")
    console.print("Browser will refill the current page in ~2 seconds and take a new screenshot.")


@app.command("apply-answers")
def apply_answers(
    company: str = typer.Option(..., "--company", help="Company name to match against existing reports."),
    role: str = typer.Option(..., "--role", help="Role title to match against existing reports."),
    form_text: str | None = typer.Option(
        None,
        "--form-text",
        help="Form questions pasted as a single string (use --form-text-file for multi-line).",
    ),
    form_text_file: Path | None = typer.Option(
        None,
        "--form-text-file",
        help="Path to a text file containing the verbatim form questions.",
    ),
    url: str | None = typer.Option(None, "--url", help="Optional application URL for context."),
    jd: str | None = typer.Option(
        None,
        "--jd",
        help="Path to JD file or pasted JD text. Grounds the red team's targeting "
        "pass (CLAUDE.md §1) against the actual posting; optional.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        help="Write the markdown answers to this file in addition to stdout.",
    ),
) -> None:
    """Generate per-question answers for a non-Workday application form using the matched report."""
    from job_hunt.nodes.apply_screen_assist import generate_apply_answers, load_form_text

    text = load_form_text(form_text, form_text_file)
    if not text:
        console.print(
            "[red]Provide form questions via --form-text or --form-text-file.[/red]"
        )
        raise typer.Exit(1)

    jd_text = ""
    if jd:
        jd_path = Path(jd)
        jd_text = jd_path.read_text(encoding="utf-8") if jd_path.exists() else jd

    tracker = TrackerRepository(Path("data/applications.md"))
    report_context = _load_apply_report_context(
        tracker=tracker,
        tracker_entry=None,
        company=company,
        role=role,
    )
    section_g = ""
    report_full = ""
    if report_context and report_context.get("path"):
        report_path = Path(report_context["path"])
        if report_path.exists():
            section_g = report_context.get("application_section") or ""
            # Section G is the extract of this report that answers form
            # questions. Sending the whole report alongside it adds ~13k
            # tokens of duplicate context per call, so the full text is a
            # fallback for when the section could not be located.
            if not section_g:
                report_full = report_path.read_text(encoding="utf-8")
    if not section_g and not report_full:
        console.print(
            f"[yellow]No matching report found for {company} / {role}; "
            "answers will be grounded in the CV only.[/yellow]"
        )

    cv_path = Path("profile/cv.md")
    cv_md = cv_path.read_text(encoding="utf-8") if cv_path.exists() else ""

    async def run() -> None:
        result = await generate_apply_answers(
            company=company,
            role=role,
            url=url or "",
            form_text=text,
            report_section_g=section_g,
            report_full=report_full,
            cv_md=cv_md,
        )
        for error in result.errors:
            console.print(f"[yellow]warning:[/yellow] {error}")

        # CLAUDE.md §1: application-form answers are named alongside résumés
        # and cover letters as requiring red team before delivery, and the
        # reviewer reads artifacts off disk — so, unlike before, the answers
        # always get written out, not only when --output was passed. Prefer
        # the run directory the matched report already lives in (paired with
        # the pipeline's own cv.pdf / redteam.md); fall back to a company/role
        # slug when no report matched.
        if output is not None:
            answers_path = output
        elif report_context and report_context.get("path"):
            answers_path = Path("output") / Path(report_context["path"]).stem / "apply-answers.md"
        else:
            company_slug = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")
            role_slug = re.sub(r"[^a-z0-9]+", "-", role.lower()).strip("-")
            answers_path = Path("output") / f"{company_slug}-{role_slug}-apply-answers.md"
        answers_path.parent.mkdir(parents=True, exist_ok=True)
        answers_path.write_text(result.content + "\n", encoding="utf-8")

        if not jd_text:
            # Without a JD the review's TARGETING pass (CLAUDE.md §1) has nothing
            # to compare against and degrades to a no-op — say so rather than
            # letting a clean-looking verdict imply all three passes ran.
            console.print(
                "[yellow]No JD supplied (--jd); the red team's targeting pass "
                "has nothing to compare against.[/yellow]"
            )
        _gate_outward_artifact(artifact_path=answers_path, jd_text=jd_text, company=company, role=role)

        # CLAUDE.md §1: printing the answers and announcing their path *is*
        # delivery — this command used to do both before the gate above, so
        # the operator could read and paste the answers into an employer's
        # form before any verdict existed. Presence of the gate was verified
        # three times over; order never was.
        console.print(result.content)
        console.print(f"\n[green]Wrote answers to[/green] {answers_path}")

    asyncio.run(run())


@app.command("apply-close-session")
def apply_close_session() -> None:
    """Gracefully close an active fill-only browser so login cookies/profile state are saved."""
    art_dir = _active_apply_artifact_dir()
    apply_ipc.submit_command(art_dir, apply_ipc.COMMAND_TYPE_CLOSE_SESSION)
    console.print(f"Graceful close request sent → {art_dir.name}")
    console.print("Browser will close after saving the persistent profile.")


@app.command("apply-status")
def apply_status(
    controls: bool = typer.Option(
        False,
        "--controls",
        help="Include the full form-control summary (label/type/value/required).",
    ),
    session: str | None = typer.Option(
        None,
        "--session",
        help="Artifact-dir name substring to disambiguate between live sessions.",
    ),
) -> None:
    """Print a compact text report of the live fill-only page (no screenshot).

    Token-efficient replacement for reading a screenshot or driving a browser
    MCP: URL, Workday step, error banners, required-but-empty fields, and
    (with --controls) every visible form control with its current value.
    """
    art_dir = _active_apply_artifact_dir(session)
    sentinel = apply_ipc.submit_command(
        art_dir, apply_ipc.COMMAND_TYPE_STATUS, {"controls": controls}
    )
    response = apply_ipc.wait_for_response(art_dir, apply_ipc.command_id_of(sentinel))
    if response is None:
        console.print(
            "[red]No response from the fill-only session (timeout). "
            "It may be dead — restart with `apply --fill-only`.[/red]"
        )
        raise typer.Exit(1)
    for line in page_summary.render_status_lines(response):
        console.print(line)


@app.command("apply-do")
def apply_do(
    click: str | None = typer.Option(
        None, "--click", help="Click a button/link by its visible label."
    ),
    fill: str | None = typer.Option(
        None, "--fill", help="Fill an input by label: 'label=value'."
    ),
    select: str | None = typer.Option(
        None, "--select", help="Pick a dropdown option by label: 'label=option'."
    ),
    check: str | None = typer.Option(
        None, "--check", help="Check a checkbox/radio by its label."
    ),
    session: str | None = typer.Option(
        None,
        "--session",
        help="Artifact-dir name substring to disambiguate between live sessions.",
    ),
) -> None:
    """Run one targeted action inside the live fill-only session.

    The escape hatch for fixing a single missed field without an interactive
    browser session. Never touches Submit: submit-like labels are rejected —
    the final click stays human-only (or goes through the gated --auto-submit).
    """
    try:
        op, label, value = apply_ops.parse_op_args(
            click=click, fill=fill, select=select, check=check
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    if op == apply_ops.OP_CLICK and _looks_like_submit_label(label):
        console.print(
            "[red]apply-do refuses submit-like clicks; the final Submit stays "
            "manual (or use the gated `apply --auto-submit`).[/red]"
        )
        raise typer.Exit(1)
    art_dir = _active_apply_artifact_dir(session)
    sentinel = apply_ipc.submit_command(
        art_dir,
        apply_ipc.COMMAND_TYPE_DO,
        {"op": op, "label": label, "value": value},
    )
    response = apply_ipc.wait_for_response(art_dir, apply_ipc.command_id_of(sentinel))
    if response is None:
        console.print(
            "[red]No response from the fill-only session (timeout). "
            "It may be dead — restart with `apply --fill-only`.[/red]"
        )
        raise typer.Exit(1)
    if response.get("ok"):
        console.print(f"[green]Done:[/green] {op} '{label}'"
                      + (f" = '{value}'" if value else ""))
    else:
        console.print(
            f"[red]Failed:[/red] {op} '{label}' — {response.get('detail') or 'no matching element'}"
        )
    if response.get("url"):
        console.print(f"URL now: {response['url']}")
    required_empty = response.get("required_empty")
    if required_empty:
        console.print(f"Required still empty ({len(required_empty)}):")
        for item in required_empty:
            console.print(f"  - {item}")
    elif required_empty == []:
        console.print("Required still empty: none")
    if not response.get("ok"):
        raise typer.Exit(1)




























_BROWSER_PROFILE = Path("storage/browser-profile")
_CDP_PORT = 9222


# Session screenshots are agent/user evidence, not print material: full-page
# JPEG at this quality is ~5-10x smaller than the old PNG and cheaper for the
# agent to read, with no loss of legibility for form text.
_SCREENSHOT_JPEG_QUALITY = 60


async def _save_session_screenshot(page, art_dir: Path, prefix: str) -> Path:
    path = art_dir / f"{prefix}-{uuid.uuid4().hex[:8]}.jpg"
    try:
        await page.screenshot(
            path=str(path), full_page=True,
            type="jpeg", quality=_SCREENSHOT_JPEG_QUALITY,
            timeout=15000,
        )
    except Exception:
        # Very tall/hostile pages can stall full-page capture; a viewport
        # shot is still useful evidence and keeps the session responsive.
        await page.screenshot(
            path=str(path), full_page=False,
            type="jpeg", quality=_SCREENSHOT_JPEG_QUALITY,
            timeout=10000,
        )
    return path


async def _open_apply_page(
    url: str,
    *,
    pdf: Path | None,
    headless: bool,
    auto_fill: bool,
    company: str | None,
    role: str | None,
    fill_only: bool = False,
    artifact_dir: Path | None = None,
    report_context: dict | None = None,
    auto_submit: bool = False,
    cover_letter_pdf: Path | None = None,
) -> dict:
    from playwright.async_api import async_playwright

    art_dir = artifact_dir or Path("artifacts/apply")
    art_dir.mkdir(parents=True, exist_ok=True)
    _BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)
    # Clear stale Chromium singleton locks left by abrupt process kills.
    for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        (_BROWSER_PROFILE / lock).unlink(missing_ok=True)

    cdp_args: list[str] = []

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(_BROWSER_PROFILE),
            headless=headless,
            args=cdp_args,
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await _enter_application_form(page)
        await _advance_application_start(page)
        await _maybe_workday_login(
            page,
            artifact_dir=art_dir,
            warn=lambda msg: console.print(f"[yellow]{msg}[/yellow]"),
        )
        await _advance_application_start(page)
        await _recover_workday_error_page(page, url)
        await _wait_for_application_ready(page)
        title = await page.title()
        role_warnings = await _page_identity_warnings(page, company=company, role=role)
        role_warnings.extend(_report_fit_warnings(report_context))
        file_inputs = page.locator("input[type=file]")
        file_count = await file_inputs.count()

        # --- LinkedIn Easy Apply branch -----------------------------------
        # When the URL is a LinkedIn job posting, the page either exposes a
        # native "Easy Apply" modal (handled here) or a third-party redirect
        # button (let the generic flow below pick it up after the redirect).
        # The dedicated driver returns OUTCOME_NOT_EASY_APPLY when the URL
        # does not match, so this branch only activates on real LinkedIn
        # job pages.
        filled: list[str] = []
        skipped: list[str] = []
        answers: list[dict[str, str]] = []
        attached = False
        cover_letter_attached = False
        required_empty: list[str] = []
        validation_issues = []
        actions: list[str] = []
        auto_submit_clicked = False
        linkedin_handled = False

        from job_hunt.services.linkedin.easy_apply import (
            OUTCOME_LOGIN_REQUIRED,
            OUTCOME_MODAL_NOT_OPENED,
            OUTCOME_NOT_EASY_APPLY,
            OUTCOME_SUBMITTED,
        )

        linkedin_result = await _maybe_linkedin_easy_apply(
            page,
            pdf=pdf,
            company=company,
            role=role,
            report_context=report_context,
            auto_submit=auto_submit,
            artifact_dir=art_dir,
        )
        if linkedin_result is not None and linkedin_result.outcome not in (
            OUTCOME_NOT_EASY_APPLY,
            OUTCOME_MODAL_NOT_OPENED,
        ):
            linkedin_handled = True
            filled.extend(linkedin_result.filled)
            skipped.extend(linkedin_result.skipped)
            answers.extend(linkedin_result.answers)
            required_empty = list(linkedin_result.required_empty)
            attached = pdf is not None and any(
                "LinkedIn Resume:" in item for item in linkedin_result.filled
            )
            if attached and pdf and not (art_dir / pdf.name).exists():
                shutil.copy2(pdf, art_dir / pdf.name)
            auto_submit_clicked = linkedin_result.submitted
            if linkedin_result.outcome == OUTCOME_LOGIN_REQUIRED:
                console.print(
                    "[red]LinkedIn session is not signed in.[/red] "
                    "Open the persistent browser profile, sign in, then re-run."
                )
            elif auto_submit_clicked:
                apply_run_log.emit(
                    art_dir, "auto_submit.fired",
                    url=page.url, platform="linkedin",
                )
                console.print(
                    "[green]Auto-submit clicked.[/green] Waiting for confirmation page…"
                )
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=30000)
                except Exception:
                    pass
                await page.wait_for_timeout(3000)
                apply_run_log.emit(
                    art_dir, "auto_submit.confirmed",
                    url=page.url, platform="linkedin",
                )
            elif auto_submit:
                # LinkedIn driver applied the gates itself; surface the reason
                # so the user can see what blocked the click.
                if required_empty:
                    apply_run_log.emit(
                        art_dir, "auto_submit.gated",
                        reason="required_empty_fields",
                        platform="linkedin",
                        fields=required_empty[:10],
                    )
                    console.print(
                        f"[yellow]Auto-submit skipped: {len(required_empty)} required field(s) "
                        f"still empty on LinkedIn Review.[/yellow]"
                    )
                elif linkedin_result.outcome != OUTCOME_SUBMITTED:
                    apply_run_log.emit(
                        art_dir, "auto_submit.gated",
                        reason="linkedin_review_not_reached",
                        outcome=linkedin_result.outcome,
                        platform="linkedin",
                    )

        if not linkedin_handled:
            # Auto-fill text fields first so React components finish mounting,
            # then attach the PDF so file upload state is set on a stable form.
            if auto_fill:
                filled, skipped, answers = await _auto_fill_application(
                    page,
                    company=company,
                    role=role,
                    report_context=report_context,
                )

            if pdf and not is_workday_page(page):
                attached = await _attach_resume(page, pdf)
                if attached:
                    await page.wait_for_timeout(2000)
                    if not (art_dir / pdf.name).exists():
                        shutil.copy2(pdf, art_dir / pdf.name)

            if cover_letter_pdf and not is_workday_page(page):
                cover_letter_attached = await _attach_cover_letter(page, cover_letter_pdf)
                if cover_letter_attached:
                    await page.wait_for_timeout(1500)
                    if not (art_dir / cover_letter_pdf.name).exists():
                        shutil.copy2(cover_letter_pdf, art_dir / cover_letter_pdf.name)

            # Advance through all remaining Workday steps (My Experience → Application Questions
            # → Voluntary Disclosures) stopping at Review so the user submits manually.
            adv_filled, adv_skipped, adv_answers = await _workday_advance_all_steps(
                page, _apply_profile_values(), pdf=pdf,
                company=company, role=role, report_context=report_context,
                artifact_dir=art_dir,
            )
            filled.extend(adv_filled)
            skipped.extend(adv_skipped)
            answers.extend(adv_answers)
            skipped = _filter_non_blocking_workday_skips(skipped)

            # Workday uploads the resume inside the My Experience step (not via the
            # earlier `_attach_resume` call), so the original ``attached`` flag is
            # always False for Workday flows. Verify the PDF actually landed on the
            # page before claiming success, then mirror it into the artifact dir so
            # `apply-review.json["pdf"]` is accurate.
            if pdf and not attached and await _workday_resume_was_uploaded(page, pdf):
                attached = True
                if not (art_dir / pdf.name).exists():
                    shutil.copy2(pdf, art_dir / pdf.name)

            labels = await page.locator("button, a[role=button], input[type=submit]").all_inner_texts()
            actions = [_short(label.strip(), 80) for label in labels if label.strip()]
            required_empty = await _required_empty_fields(page)
            required_empty = _filter_required_empty_fields(required_empty, filled)
            validation_issues = await _collect_workday_review_issues(page) if is_workday_page(page) else []

        # --- Auto-submit (Phase 4 — gated) ----------------------------------
        # Only fires when ALL of the following are true:
        #  - caller passed auto_submit=True (CLI flag + profile.yml gate already
        #    AND-ed by apply_assist before we got here)
        #  - URL is a Workday host (the only ATS where we have a structured
        #    Review gate; other sites stay manual until they have one too)
        #  - validation_issues is empty (Review-gate clean)
        #  - required_empty is empty (no required field still missing)
        # When any gate fails we leave the page exactly as-is for manual review.
        # LinkedIn Easy Apply runs its own gate above; do not re-enter the
        # Workday-specific branches when the LinkedIn driver handled the page.
        if auto_submit and not linkedin_handled:
            workday_host = is_workday_page(page)
            if not workday_host:
                apply_run_log.emit(
                    art_dir, "auto_submit.gated",
                    reason="non_workday_host", url=page.url,
                )
                console.print("[yellow]Auto-submit skipped: only Workday URLs supported.[/yellow]")
            elif validation_issues:
                apply_run_log.emit(
                    art_dir, "auto_submit.gated",
                    reason="review_validation_issues",
                    issue_codes=[i.code for i in validation_issues],
                )
                console.print(
                    f"[yellow]Auto-submit skipped: {len(validation_issues)} Review-gate "
                    f"issue(s).[/yellow]"
                )
            elif required_empty:
                apply_run_log.emit(
                    art_dir, "auto_submit.gated",
                    reason="required_empty_fields",
                    fields=required_empty[:10],
                )
                console.print(
                    f"[yellow]Auto-submit skipped: {len(required_empty)} required field(s) "
                    f"still empty.[/yellow]"
                )
            else:
                clicked = await _try_workday_final_submit(page)
                if clicked:
                    auto_submit_clicked = True
                    apply_run_log.emit(
                        art_dir, "auto_submit.fired",
                        url=page.url,
                    )
                    console.print("[green]Auto-submit clicked.[/green] Waiting for confirmation page…")
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=30000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(3000)
                    apply_run_log.emit(
                        art_dir, "auto_submit.confirmed",
                        url=page.url,
                    )
                else:
                    apply_run_log.emit(
                        art_dir, "auto_submit.gated",
                        reason="submit_button_not_found",
                    )
                    console.print(
                        "[yellow]Auto-submit skipped: Submit button not located on Review page.[/yellow]"
                    )

        screenshot = await _save_session_screenshot(page, art_dir, "apply-review")
        if required_empty or validation_issues:
            # Failure-path aid: dump a compact form-control summary so the agent
            # can diagnose from JSON instead of reading the screenshot.
            controls = await page_summary.collect_form_controls(page)
            (art_dir / "apply-controls.json").write_text(
                json.dumps(
                    {"url": page.url, "form_controls": controls},
                    ensure_ascii=False, indent=2,
                ),
                encoding="utf-8",
            )
        summary_path = _write_apply_review_summary(
            artifact_dir=art_dir,
            url=url,
            final_url=page.url,
            title=title,
            company=company,
            role=role,
            report_context=report_context,
            filled=filled,
            skipped=skipped,
            answers=answers,
            required_empty=required_empty,
            actions=actions,
            screenshot=screenshot,
            pdf=pdf if attached else None,
            role_warnings=role_warnings,
            validation_issues=validation_issues,
        )

        console.print(f"Opened: {title or url}")
        console.print(f"Final URL: {page.url}")
        console.print(f"Artifact dir: {art_dir}")
        if report_context and report_context.get("path"):
            console.print(f"Matched report: {report_context['path']}")
        if role_warnings:
            console.print("[yellow]Identity warnings:[/yellow]")
            for warning in role_warnings:
                console.print(f"- {warning}")
        console.print(f"File inputs: {file_count}")
        if attached:
            console.print(f"[green]Attached PDF:[/green] {pdf}")
        elif pdf:
            console.print("[yellow]PDF was not attached; no usable file input was found.[/yellow]")
        if filled:
            console.print("Auto-filled fields:")
            for item in filled:
                console.print(f"- {item}")
        if skipped:
            console.print("Needs review / not auto-filled:")
            for item in skipped[:20]:
                console.print(f"- {item}")
        if required_empty:
            console.print("[yellow]Required fields still empty / need review:[/yellow]")
            for item in required_empty[:20]:
                console.print(f"- {item}")
        if actions:
            console.print("Visible action labels:")
            for label in actions[:12]:
                console.print(f"- {label}")
        console.print(f"Review screenshot: {screenshot}")
        console.print(f"Review summary: {summary_path}")

        # Auto-submit short-circuit: if we already clicked the final Submit
        # button, the application is in flight and there is nothing left to do
        # in the browser. Skip both the fill-only sentinel loop AND the
        # confirmation prompt; the caller will mark the row Applied.
        if auto_submit_clicked:
            await context.close()
            return {
                "submitted": True,
                "auto_submitted": True,
                "screenshot": str(screenshot),
                "artifact_dir": str(art_dir),
            }

        if fill_only:
            cdp_sentinel = art_dir / ".cdp"
            cdp_sentinel.write_text("active")
            session_started = asyncio.get_event_loop().time()
            session_token = uuid.uuid4().hex
            apply_ipc.clear_stale_responses(art_dir)
            apply_ipc.write_heartbeat(
                art_dir, started_at=session_started, session_token=session_token
            )
            apply_run_log.emit(
                art_dir, "session.started", url=url, company=company, role=role,
                pdf=str(pdf) if pdf else None,
            )
            console.print("\n[yellow]Browser open — review and submit manually.[/yellow]")
            console.print(f"Sentinel dir: {art_dir}")
            console.print("Commands: apply-replace-pdf <pdf>  |  Tell Claude 'submitted' when done.")

            last_screenshot = screenshot
            last_activity_at = asyncio.get_event_loop().time()
            last_heartbeat_at = 0.0
            close_requested = False
            while True:
                now = asyncio.get_event_loop().time()
                # Idle exit: bail out if no command/refill happened for a long time.
                if now - last_activity_at > apply_ipc.IDLE_TIMEOUT_SECONDS:
                    apply_run_log.emit(art_dir, "session.idle_exit",
                                       idle_seconds=int(now - last_activity_at))
                    console.print(
                        f"[yellow]Idle timeout reached after "
                        f"{apply_ipc.IDLE_TIMEOUT_SECONDS // 60} min — closing fill-only loop.[/yellow]"
                    )
                    break
                # Heartbeat refresh.
                if now - last_heartbeat_at >= apply_ipc.HEARTBEAT_REFRESH_SECONDS:
                    apply_ipc.write_heartbeat(
                        art_dir, started_at=session_started,
                        session_token=session_token,
                    )
                    last_heartbeat_at = now
                await asyncio.sleep(2)

                # Drain the ``.cmd-*.json`` command queue (race-free, mtime-ordered).
                pending = apply_ipc.consume_pending_commands(art_dir)

                for cmd in pending:
                    last_activity_at = asyncio.get_event_loop().time()
                    # Authenticate every sentinel against the per-session nonce:
                    # a stale script or stray file must not drive the browser.
                    if cmd.token != session_token:
                        apply_run_log.emit(
                            art_dir, "command.rejected",
                            kind=cmd.kind, reason="bad_session_token",
                        )
                        console.print(
                            f"[red]Rejected command '{cmd.kind}': session token mismatch.[/red]"
                        )
                        try:
                            apply_ipc.write_response(
                                art_dir, cmd.id,
                                {"ok": False, "detail": "session token mismatch"},
                            )
                        except Exception:
                            pass
                        continue
                    if cmd.kind == apply_ipc.COMMAND_TYPE_REPLACE_PDF:
                        new_pdf_str = str(cmd.payload.get("pdf", "")).strip()
                        # Reject empty payloads up front: ``Path("")`` would
                        # resolve to ``Path(".")`` which is truthy + exists, so
                        # a malformed sentinel would otherwise try to attach
                        # the working directory as a PDF.
                        if not new_pdf_str:
                            apply_run_log.emit(
                                art_dir, "command.replace_pdf.failed",
                                reason="empty_payload",
                            )
                            console.print("[red]apply-replace-pdf: empty payload[/red]")
                            continue
                        new_pdf = Path(new_pdf_str)
                        if new_pdf.exists() and new_pdf.is_file():
                            await _attach_resume(page, new_pdf)
                            await page.wait_for_timeout(1500)
                            shutil.copy2(new_pdf, art_dir / new_pdf.name)
                            last_screenshot = await _save_session_screenshot(
                                page, art_dir, "apply-review"
                            )
                            _append_apply_review_event(
                                artifact_dir=art_dir,
                                event=f"PDF replaced: {new_pdf.name}",
                                screenshot=last_screenshot,
                            )
                            apply_run_log.emit(
                                art_dir, "command.replace_pdf",
                                pdf=str(new_pdf), screenshot=str(last_screenshot),
                            )
                            console.print(f"[green]PDF replaced:[/green] {new_pdf}")
                            console.print(f"New screenshot: {last_screenshot}")
                        else:
                            apply_run_log.emit(
                                art_dir, "command.replace_pdf.failed",
                                pdf=new_pdf_str, reason="not_found",
                            )
                            console.print(f"[red]PDF not found:[/red] {new_pdf_str}")
                    elif cmd.kind == apply_ipc.COMMAND_TYPE_CAPTURE_PAGE:
                        last_screenshot = await _save_session_screenshot(
                            page, art_dir, "apply-page"
                        )
                        current_title = await page.title()
                        _append_apply_review_event(
                            artifact_dir=art_dir,
                            event=f"Page captured: {current_title or page.url}",
                            screenshot=last_screenshot,
                        )
                        apply_run_log.emit(
                            art_dir, "command.capture_page",
                            url=page.url, title=current_title,
                            screenshot=str(last_screenshot),
                        )
                        console.print(f"[green]Page captured:[/green] {last_screenshot}")
                    elif cmd.kind == apply_ipc.COMMAND_TYPE_REFILL_CURRENT_PAGE:
                        last_screenshot = await _handle_refill_current_page(
                            page, art_dir, url, company, role, report_context,
                            pdf, role_warnings,
                        )
                    elif cmd.kind == apply_ipc.COMMAND_TYPE_STATUS:
                        # Fail closed: a status/do handler crash must never
                        # kill the fill-only session.
                        try:
                            status_payload = await _collect_status_payload(
                                page,
                                include_controls=bool(cmd.payload.get("controls")),
                                filled_hint=filled,
                            )
                            apply_ipc.write_response(art_dir, cmd.id, status_payload)
                            apply_run_log.emit(
                                art_dir, "command.status",
                                url=page.url,
                                required_empty_count=len(status_payload.get("required_empty") or []),
                                error_count=len(status_payload.get("errors") or []),
                            )
                            console.print("[green]Status request answered.[/green]")
                        except Exception as exc:
                            apply_run_log.emit(
                                art_dir, "command.status.failed",
                                error=_short(str(exc), 160),
                            )
                            console.print(f"[red]Status request failed:[/red] {exc}")
                    elif cmd.kind == apply_ipc.COMMAND_TYPE_DO:
                        try:
                            do_result = await _handle_do_command(
                                page, cmd.payload, filled_hint=filled,
                            )
                            apply_ipc.write_response(art_dir, cmd.id, do_result)
                            apply_run_log.emit(
                                art_dir,
                                "command.do" if do_result.get("ok") else "command.do.failed",
                                op=do_result.get("op"), label=do_result.get("label"),
                                detail=do_result.get("detail") or None,
                            )
                            _append_apply_review_event(
                                artifact_dir=art_dir,
                                event=(
                                    f"apply-do {do_result.get('op')} '{do_result.get('label')}': "
                                    + ("ok" if do_result.get("ok") else f"failed ({do_result.get('detail') or 'no match'})")
                                ),
                            )
                            console.print(
                                f"[green]apply-do handled:[/green] {do_result.get('op')} '{do_result.get('label')}'"
                                if do_result.get("ok")
                                else f"[red]apply-do failed:[/red] {do_result.get('op')} '{do_result.get('label')}'"
                            )
                        except Exception as exc:
                            apply_run_log.emit(
                                art_dir, "command.do.failed",
                                error=_short(str(exc), 160),
                            )
                            console.print(f"[red]apply-do crashed:[/red] {exc}")
                    elif cmd.kind == apply_ipc.COMMAND_TYPE_CLOSE_SESSION:
                        _append_apply_review_event(
                            artifact_dir=art_dir,
                            event="Graceful browser close requested; persistent profile should save login state.",
                            screenshot=last_screenshot,
                        )
                        apply_run_log.emit(art_dir, "command.close_session")
                        console.print("[green]Graceful close requested; saving browser profile.[/green]")
                        close_requested = True

                if close_requested:
                    break

            cdp_sentinel.unlink(missing_ok=True)
            apply_ipc.clear_heartbeat(art_dir)
            apply_run_log.emit(art_dir, "session.ended", final_url=page.url)
            await context.close()
            return {"submitted": False, "deferred": True, "screenshot": str(last_screenshot), "artifact_dir": str(art_dir)}

        console.print(
            "\nReview the form in the browser and submit it manually. "
            "This command will only update local state after you confirm."
        )
        submitted = await asyncio.to_thread(
            typer.confirm,
            "Have you manually submitted this application?",
            default=False,
        )
        await context.close()
    return {"submitted": submitted, "screenshot": str(screenshot), "artifact_dir": str(art_dir)}


async def _handle_refill_current_page(
    page,
    art_dir: Path,
    url: str,
    company: str | None,
    role: str | None,
    report_context: dict | None,
    pdf: "Path | None",
    role_warnings: list[str],
) -> Path:
    """Re-run auto-fill + Workday advance on the current page.

    Extracted from the inline ``apply --fill-only`` loop in Phase 3.3 so the
    sentinel-driven and command-driven entry points share one implementation.
    Returns the latest screenshot path so the caller can update its cursor.
    """
    filled, skipped, answers = await _auto_fill_application(
        page, company=company, role=role, report_context=report_context,
    )
    attached = False
    if pdf and not is_workday_page(page):
        attached = await _attach_resume(page, pdf)
        if attached:
            await page.wait_for_timeout(1500)
            shutil.copy2(pdf, art_dir / pdf.name)
    adv_filled, adv_skipped, adv_answers = await _workday_advance_all_steps(
        page, _apply_profile_values(), pdf=pdf,
        company=company, role=role, report_context=report_context,
        artifact_dir=art_dir,
    )
    filled.extend(adv_filled)
    skipped.extend(adv_skipped)
    answers.extend(adv_answers)
    skipped = _filter_non_blocking_workday_skips(skipped)

    # Same Workday upload-detection logic as `_open_apply_page`: surface
    # `attached=True` when the PDF filename is visible on the page so the
    # refilled apply-review.json shows the resume rather than null.
    if pdf and not attached and await _workday_resume_was_uploaded(page, pdf):
        attached = True
        if not (art_dir / pdf.name).exists():
            shutil.copy2(pdf, art_dir / pdf.name)
    required_empty = await _required_empty_fields(page)
    required_empty = _filter_required_empty_fields(required_empty, filled)
    refill_validation_issues = (
        await _collect_workday_review_issues(page)
        if is_workday_page(page) else []
    )
    labels = await page.locator("button, a[role=button], input[type=submit]").all_inner_texts()
    actions = [_short(label.strip(), 80) for label in labels if label.strip()]
    last_screenshot = await _save_session_screenshot(page, art_dir, "apply-review")
    if required_empty or refill_validation_issues:
        controls = await page_summary.collect_form_controls(page)
        (art_dir / "apply-controls.json").write_text(
            json.dumps(
                {"url": page.url, "form_controls": controls},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
    _write_apply_review_summary(
        artifact_dir=art_dir,
        url=url,
        final_url=page.url,
        title=await page.title(),
        company=company,
        role=role,
        report_context=report_context,
        filled=filled,
        skipped=skipped,
        answers=answers,
        required_empty=required_empty,
        actions=actions,
        screenshot=last_screenshot,
        pdf=pdf if attached else None,
        role_warnings=role_warnings,
        validation_issues=refill_validation_issues,
    )
    apply_run_log.emit(
        art_dir, "command.refill_current_page",
        url=page.url, attached=attached,
        validation_issue_count=len(refill_validation_issues),
        required_empty_count=len(required_empty),
        screenshot=str(last_screenshot),
    )
    console.print(f"[green]Current page refilled:[/green] {last_screenshot}")
    if attached:
        console.print(f"[green]Attached PDF:[/green] {pdf}")
    if filled:
        console.print("Auto-filled fields:")
        for item in filled:
            console.print(f"- {item}")
    if required_empty:
        console.print("[yellow]Required fields still empty / need review:[/yellow]")
        for item in required_empty[:20]:
            console.print(f"- {item}")
    return last_screenshot


async def _collect_status_payload(
    page, *, include_controls: bool, filled_hint: list[str]
) -> dict:
    """Build the compact apply-status response for the live page.

    ``filled_hint`` is the session's accumulated ``filled[]`` list, used to
    suppress required-empty false positives the same way the fill path does.
    """
    try:
        title = await page.title()
    except Exception:
        title = ""
    step = ""
    if is_workday_page(page):
        try:
            step = await _workday_current_step(page)
        except Exception:
            step = ""
    required_empty = _filter_required_empty_fields(
        await _required_empty_fields(page), filled_hint
    )
    errors = await page_summary.collect_error_banners(page)
    try:
        labels = await page.locator(
            "button, a[role=button], input[type=submit]"
        ).all_inner_texts()
        actions = [_short(label.strip(), 80) for label in labels if label.strip()][:12]
    except Exception:
        actions = []
    payload: dict = {
        "ok": True,
        "url": page.url,
        "title": title,
        "workday_step": step,
        "errors": errors,
        "required_empty": required_empty,
        "actions": actions,
    }
    if include_controls:
        payload["form_controls"] = await page_summary.collect_form_controls(page)
    return payload


async def _handle_do_command(page, payload: dict, *, filled_hint: list[str]) -> dict:
    """Execute one apply-do op against the live page; always returns a response."""
    op = str(payload.get("op") or "")
    label = str(payload.get("label") or "")
    value = str(payload.get("value") or "")
    ok = False
    detail = ""
    if not op or not label:
        detail = "missing op/label"
    elif op == apply_ops.OP_CLICK and _looks_like_submit_label(label):
        # Defense in depth: the CLI already refuses submit-like clicks, but a
        # hand-written sentinel must not bypass the human-only submit rule.
        detail = "submit-like click refused"
    else:
        try:
            ok = await apply_ops.execute_op(
                page, op, label, value,
                click=_do_click_by_label,
                fill=_do_fill_by_label,
                select=_do_select_by_label,
                check=_do_check_by_label,
            )
        except ApplyDoRefused as exc:
            detail = str(exc)
        except Exception as exc:
            detail = _short(str(exc), 160)
    if ok and op == apply_ops.OP_CLICK:
        # A click may navigate or trigger validation; let the DOM settle so
        # the required_empty/url below describe the resulting page, not the
        # one the click left behind.
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        await page.wait_for_timeout(1200)
    required_empty = _filter_required_empty_fields(
        await _required_empty_fields(page), filled_hint
    )
    return {
        "ok": ok,
        "op": op,
        "label": label,
        "value": value,
        "detail": detail,
        "url": page.url,
        "required_empty": required_empty,
    }




































async def _auto_fill_application(
    page,
    *,
    company: str | None,
    role: str | None,
    report_context: dict | None = None,
) -> tuple[list[str], list[str], list[dict[str, str]]]:
    values = _apply_profile_values()
    filled: list[str] = []
    skipped: list[str] = []
    answers: list[dict[str, str]] = []

    for label, value in [
        ("First Name", values["first_name"]),
        ("Last Name", values["last_name"]),
        ("Name", values["name"]),
        ("Email", values["email"]),
        ("LinkedIn", values["linkedin"]),
        ("GitHub", values["github"]),
        ("Portfolio", values["portfolio"]),
        ("Other links", values["portfolio"]),
    ]:
        if await _fill_by_label_or_placeholder(page, label, value):
            filled.append(label)
    for label in ["Phone Number", "Phone"]:
        if await _fill_by_label_or_placeholder(page, label, values["phone"]):
            filled.append(label)
            break
    if await _fill_location(page, values["location"]):
        filled.append("Location")

    workday_filled, workday_skipped, workday_answers = await _fill_workday_current_step(
        page, values, company=company, role=role, report_context=report_context
    )
    filled.extend(workday_filled)
    skipped.extend(workday_skipped)
    answers.extend(workday_answers)

    await _scroll_application_form(page)

    for label, value in [
        (
            "If you were to start at Anthropic full-time after the Fellows program, when is the earliest you could start?",
            values["full_time_start"],
        ),
        ("What is your current country of residence?", values["country"]),
        ("Country of residence", values["country"]),
    ]:
        if await _fill_by_label_or_placeholder(page, label, value):
            filled.append(_short(label, 80))

    textareas = page.locator("textarea")
    for index in range(await textareas.count()):
        area = textareas.nth(index)
        question = await _field_context(area)
        answer = _answer_for_application_question(
            question,
            company=company,
            role=role,
            report_context=report_context,
        )
        if answer:
            await area.fill(answer)
            if await _field_contains_text(area, answer):
                filled.append(_short(question or f"textarea {index + 1}", 80))
                answers.append({"question": question or f"textarea {index + 1}", "answer": answer})
            else:
                skipped.append(_short(f"{question or f'textarea {index + 1}'} (fill did not persist)", 120))
        elif question:
            skipped.append(_short(question, 120))

    rich_textboxes = page.locator('[role="textbox"][contenteditable="plaintext-only"]')
    for index in range(await rich_textboxes.count()):
        box = rich_textboxes.nth(index)
        question = await _field_context(box)
        answer = _answer_for_application_question(
            question,
            company=company,
            role=role,
            report_context=report_context,
        )
        if answer:
            if await _fill_contenteditable(box, answer):
                filled.append(_short(question or f"rich text {index + 1}", 80))
                answers.append({"question": question or f"rich text {index + 1}", "answer": answer})
            else:
                skipped.append(_short(f"{question or f'rich text {index + 1}'} (fill did not persist)", 120))
        elif question:
            skipped.append(_short(question, 120))

    radios = page.locator('input[type="radio"]')
    seen_radio_names: set[str] = set()
    for index in range(await radios.count()):
        radio = radios.nth(index)
        name = await radio.get_attribute("name") or f"radio-{index}"
        if name in seen_radio_names:
            continue
        seen_radio_names.add(name)
        context = await _field_context(radio)
        choice = _radio_choice_for_question(context)
        if choice and await _click_radio_near_text(page, name, choice):
            filled.append(_short(f"{context}: {choice}", 100))
        elif context:
            skipped.append(_short(context, 120))

    return filled, skipped, answers

































































































async def _page_identity_warnings(page, *, company: str | None, role: str | None) -> list[str]:
    warnings: list[str] = []
    try:
        page_text = await page.locator("body").inner_text(timeout=3000)
    except Exception:
        page_text = ""
    try:
        title = await page.title()
    except Exception:
        title = ""
    lower = f"{title}\n{page_text}".lower()
    if company and company.lower() not in lower:
        warnings.append(f"Expected company {company!r} was not clearly visible on the page.")
    if role:
        role_tokens = [token for token in re.findall(r"[a-z0-9]+", role.lower()) if len(token) >= 4]
        matched = sum(1 for token in role_tokens if token in lower)
        if role_tokens and matched / len(role_tokens) < 0.45:
            warnings.append(f"Expected role {role!r} did not strongly match visible page text.")

        # P2-9 role drift: extract the page's stated role (og:title / h1 / title)
        # and fuzzy-compare. This surfaces *what* the page is advertising, not
        # just absence — useful when a URL was reposted under a different title.
        from job_hunt.services.role_drift import detect_role_drift, extract_page_role

        page_role = await extract_page_role(page)
        finding = detect_role_drift(role, page_role)
        if finding.warning:
            warnings.append(finding.warning)
    return warnings


# ---------------------------------------------------------------------------
# LinkedIn Easy Apply — Playwright helpers + dispatcher wrapper.
#
# The pure dispatcher (`run_easy_apply`) and field strategy helpers live in
# `job_hunt.services.linkedin.*`. The functions below are the live Playwright
# adapters injected into the dispatcher. They are intentionally small and
# unit-tested via the dispatcher's AsyncMock harness rather than a real
# browser. ADR-013 (LinkedIn Easy Apply) — see docs/design-notes.md.
# ---------------------------------------------------------------------------


def _linkedin_modal(page):
    return page.locator('div[role="dialog"]').first


async def _linkedin_click_by_name(page, name: str) -> bool:
    """Click a button in the Easy Apply modal (or the trigger button) by name.

    LinkedIn uses both ``aria-label`` and visible button text for navigation
    controls. We try the modal first so the Easy Apply Submit / Next clicks
    never bleed into the page-level Apply button.
    """
    label_re = re.compile(rf"^\s*{re.escape(name)}\s*$", re.I)
    for scope in (_linkedin_modal(page), page):
        try:
            if hasattr(scope, "count") and not await scope.count():
                continue
            btn = scope.get_by_role("button", name=label_re).first
            if not await btn.count():
                btn = scope.locator(
                    f'button[aria-label*="{name}"]'
                ).first
                if not await btn.count():
                    continue
            await btn.click(timeout=8000)
            await page.wait_for_timeout(800)
            return True
        except Exception:
            continue
    return False


async def _linkedin_fill_by_label(page, label: str, value: str) -> bool:
    try:
        modal = _linkedin_modal(page)
        if not await modal.count():
            return False
        target = modal.get_by_label(label, exact=False).first
        if not await target.count():
            return False
        await target.fill(value, timeout=5000)
        return True
    except Exception:
        return False


async def _linkedin_select_dropdown(page, label: str, option: str) -> bool:
    try:
        modal = _linkedin_modal(page)
        if not await modal.count():
            return False
        select = modal.get_by_label(label, exact=False).first
        if not await select.count():
            return False
        await select.select_option(label=option, timeout=3000)
        return True
    except Exception:
        return False


async def _linkedin_dropdown_options(page, label: str) -> list[str]:
    try:
        modal = _linkedin_modal(page)
        if not await modal.count():
            return []
        select = modal.get_by_label(label, exact=False).first
        if not await select.count():
            return []
        opts = await select.locator("option").all_inner_texts()
        return [opt.strip() for opt in opts if opt.strip()]
    except Exception:
        return []


async def _linkedin_select_radio(page, question: str, choice: str) -> bool:
    try:
        modal = _linkedin_modal(page)
        if not await modal.count():
            return False
        # Prefer the radio scoped to the matching fieldset/legend.
        legend_text = question[:80].replace('"', '')
        legend = modal.locator(
            f'fieldset:has(legend:has-text("{legend_text}"))'
        ).first
        scope = legend if await legend.count() else modal
        target = scope.get_by_role("radio", name=choice).first
        if not await target.count():
            target = scope.locator(
                f'label:has-text("{choice}") input[type=radio]'
            ).first
        if not await target.count():
            return False
        await target.click(timeout=5000, force=True)
        return True
    except Exception:
        return False


async def _linkedin_attach_resume(page, pdf: Path) -> bool:
    try:
        modal = _linkedin_modal(page)
        if not await modal.count():
            return False
        body = await modal.inner_text(timeout=2000)
        if pdf.name in body:
            return True
        file_input = modal.locator('input[type=file]').first
        if not await file_input.count():
            return False
        await file_input.set_input_files(str(pdf))
        await page.wait_for_timeout(1500)
        return True
    except Exception:
        return False


async def _linkedin_read_modal_heading(page) -> str:
    try:
        modal = _linkedin_modal(page)
        if not await modal.count():
            return ""
        for selector in ("h2", "h3"):
            heading = modal.locator(selector).first
            if await heading.count():
                text = (await heading.inner_text(timeout=2000)).strip()
                if text:
                    return text
        return ""
    except Exception:
        return ""


async def _linkedin_read_required_empty(page) -> list[str]:
    try:
        modal = _linkedin_modal(page)
        if not await modal.count():
            return []
        return await modal.evaluate(
            """(modal) => {
                const norm = t => (t || '').replace(/\\s+/g, ' ').trim();
                const labelFor = el => {
                    const aria = el.getAttribute('aria-label');
                    if (aria) return norm(aria);
                    if (el.labels && el.labels[0]) return norm(el.labels[0].innerText);
                    const id = el.id;
                    if (id) {
                        const l = modal.querySelector(`label[for="${id}"]`);
                        if (l) return norm(l.innerText);
                    }
                    return norm(el.name || '');
                };
                const out = [];
                const sel = 'input[aria-required="true"], input[required], '
                          + 'textarea[required], textarea[aria-required="true"], '
                          + 'select[aria-required="true"], select[required]';
                modal.querySelectorAll(sel).forEach(el => {
                    const t = (el.type || '').toLowerCase();
                    if (t === 'hidden' || t === 'file') return;
                    const val = (el.value || '').trim();
                    if (val) return;
                    const invalid = el.getAttribute('aria-invalid') === 'true';
                    const label = labelFor(el);
                    if (label && (!val || invalid)) out.push(label);
                });
                return out;
            }"""
        )
    except Exception:
        return []


async def _linkedin_read_modal_fields(page) -> list[dict]:
    """Enumerate visible form fields inside the Easy Apply modal."""
    try:
        modal = _linkedin_modal(page)
        if not await modal.count():
            return []
        return await modal.evaluate(
            """(modal) => {
                const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                const norm = t => (t || '').replace(/\\s+/g, ' ').trim();
                const labelFor = el => {
                    const aria = el.getAttribute('aria-label');
                    if (aria) return norm(aria);
                    if (el.labels && el.labels[0]) return norm(el.labels[0].innerText);
                    const id = el.id;
                    if (id) {
                        const l = modal.querySelector(`label[for="${id}"]`);
                        if (l) return norm(l.innerText);
                    }
                    return '';
                };
                const out = [];
                const seen = new Set();
                const push = (label, kind, options) => {
                    if (!label) return;
                    const key = label + '|' + kind;
                    if (seen.has(key)) return;
                    seen.add(key);
                    out.push({label, kind, options: options || []});
                };
                modal.querySelectorAll('input').forEach(el => {
                    if (!visible(el)) return;
                    const type = (el.type || 'text').toLowerCase();
                    if (type === 'hidden' || type === 'file' || type === 'submit') return;
                    if (type === 'radio') {
                        const fieldset = el.closest('fieldset');
                        let label = '';
                        if (fieldset) {
                            const legend = fieldset.querySelector('legend');
                            if (legend) label = norm(legend.innerText);
                        }
                        if (!label) label = labelFor(el);
                        push(label, 'radio');
                        return;
                    }
                    push(labelFor(el), 'text');
                });
                modal.querySelectorAll('textarea').forEach(el => {
                    if (!visible(el)) return;
                    push(labelFor(el), 'textarea');
                });
                modal.querySelectorAll('select').forEach(el => {
                    if (!visible(el)) return;
                    const opts = Array.from(el.options || [])
                        .map(o => norm(o.label || o.text || ''))
                        .filter(Boolean);
                    push(labelFor(el), 'dropdown', opts);
                });
                return out;
            }"""
        )
    except Exception:
        return []


async def _maybe_linkedin_easy_apply(
    page,
    *,
    pdf: Path | None,
    company: str | None,
    role: str | None,
    report_context: dict | None,
    auto_submit: bool,
    artifact_dir: Path,
):
    """Run LinkedIn Easy Apply when the current URL is a LinkedIn job posting.

    Returns the :class:`EasyApplyResult` from the dispatcher (so the caller can
    branch on outcome / submitted), or ``None`` when the URL is not a LinkedIn
    job page. The caller is responsible for honoring the auto-submit gates that
    sit above the URL check (CLI flag + profile.yml + mode).
    """
    from job_hunt.services.linkedin.detect import is_linkedin_job_url
    from job_hunt.services.linkedin.easy_apply import Helpers, run_easy_apply

    if not is_linkedin_job_url(page.url):
        return None

    values = _apply_profile_values()

    def _answer_lookup(question: str, ctx: dict | None) -> str:
        return _answer_for_application_question(
            question,
            company=company,
            role=role,
            report_context=ctx or report_context,
        ) or ""

    helpers = Helpers(
        click_by_name=_linkedin_click_by_name,
        fill_by_label=_linkedin_fill_by_label,
        select_dropdown=_linkedin_select_dropdown,
        dropdown_options=_linkedin_dropdown_options,
        select_radio=_linkedin_select_radio,
        attach_resume=_linkedin_attach_resume,
        read_modal_heading=_linkedin_read_modal_heading,
        read_required_empty=_linkedin_read_required_empty,
        read_modal_fields=_linkedin_read_modal_fields,
        answer_lookup=_answer_lookup,
    )

    result = await run_easy_apply(
        page,
        values=values,
        pdf=pdf,
        company=company,
        role=role,
        report_context=report_context,
        helpers=helpers,
        auto_submit=auto_submit,
        page_url=page.url,
    )
    apply_run_log.emit(
        artifact_dir,
        "linkedin_easy_apply.completed",
        outcome=result.outcome,
        submitted=result.submitted,
        steps=result.steps_visited,
        filled_count=len(result.filled),
        skipped_count=len(result.skipped),
        required_empty_count=len(result.required_empty),
    )
    return result

















































