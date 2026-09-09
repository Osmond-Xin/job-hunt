from __future__ import annotations

import asyncio
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
from job_hunt.services.web import apply_ipc, apply_ops, page_summary, submit_gate

from job_hunt.services.apply.agent_prompt import (
    _build_agent_apply_prompt,
    _infer_loop_target,
    _loop_agent_apply_command,
)
from job_hunt.services.apply.answers import (
    _load_saved_apply_answers,
)
from job_hunt.services.apply.artifacts import (
    _apply_artifact_dir,
)
from job_hunt.services.apply.linking import (
    _link_artifacts_to_row,
    _record_manual_submission,
    _tracker_entry_blocks_apply,
)
from job_hunt.services.apply.reporting import (
    _load_apply_report_context,
    low_score_verdict,
)

from job_hunt.services.web.form_fill import (
    _looks_like_submit_label,
)
from job_hunt.services.web.apply_session import (
    _open_apply_page,
)
from ._render import RichReporter, console
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

    # The three keys. One implementation, in services/web/submit_gate.py, for
    # every ATS -- there used to be one here for Workday and another inside the
    # LinkedIn flow. The reasoning for three lives with the rule; what stays
    # here is the wording, because the operator reads it.
    from job_hunt.services.profile_loader import current_mode as _read_mode

    operator_mode = _read_mode()
    authorisation = submit_gate.authorised(
        requested=auto_submit,
        profile_enabled=bool(
            _apply_profile_values().get("apply_auto_submit_enabled", False)
        ),
        mode=operator_mode,
    )
    auto_submit_active = authorisation.allowed
    if authorisation.reason == submit_gate.REASON_STUDENT_MODE:
        console.print(
            "[yellow]--auto-submit ignored:[/yellow] mode=student in profile.yml. "
            "Auto-submit is restricted to full mode. Falling back to manual submit."
        )
    elif authorisation.reason == submit_gate.REASON_PROFILE_DISABLED:
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
                reporter=RichReporter(),
                confirm_submitted=lambda prompt: typer.confirm(prompt, default=False),
                authorisation=authorisation,
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


# Session screenshots are agent/user evidence, not print material: full-page
# JPEG at this quality is ~5-10x smaller than the old PNG and cheaper for the
# agent to read, with no loss of legibility for form text.
_SCREENSHOT_JPEG_QUALITY = 60


# ---------------------------------------------------------------------------
# LinkedIn Easy Apply — Playwright helpers + dispatcher wrapper.
#
# The pure dispatcher (`run_easy_apply`) and field strategy helpers live in
# `job_hunt.services.linkedin.*`. The functions below are the live Playwright
# adapters injected into the dispatcher. They are intentionally small and
# unit-tested via the dispatcher's AsyncMock harness rather than a real
# browser. ADR-013 (LinkedIn Easy Apply) — see docs/design-notes.md.
# ---------------------------------------------------------------------------
