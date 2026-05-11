from __future__ import annotations

import shutil
import asyncio
import json
import os
import re
import shlex
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.table import Table

from job_hunt.config.models import Settings, load_settings
from job_hunt.graphs.evaluate_job import build_evaluate_job_graph
from job_hunt.models.events import ApplicationEvent
from job_hunt.repositories.tracker_repo import TrackerRepository
from job_hunt.services.scan import scan_portals
from job_hunt.repositories.email_event_repo import EmailEventRepository
from job_hunt.repositories.review_repo import ReviewRepository
from job_hunt.services.activity import ActivityEvent, ActivityLogger, read_activity
from job_hunt.services.email.message_parser import ParsedEmail, classify_email_event
from job_hunt.services.email.poller import poll_gmail
from job_hunt.services.email.reconcile import reconcile_email_events
from job_hunt.services.email.review import approve_email_event, ignore_email_event, list_review_candidates
from job_hunt.services.llm.base import ChatMessage
from job_hunt.services.llm.factory import build_cheap_provider
from job_hunt.services.llm.traced import traced_chat
from job_hunt.services.observability import TraceManager
from job_hunt.services.profile_loader import (
    workday_education_entries as _load_workday_education_entries,
    workday_experience_entries as _load_workday_experience_entries,
)
from job_hunt.services.web import apply_ipc, apply_run_log
from job_hunt.services.web_extract import extract_url_text
from job_hunt.services.workday.employer_config import (
    select_employer_config as _select_workday_employer_config,
)
from job_hunt.services.workday.application_questions import (
    run_question_ops as _run_workday_question_ops_from_module,
)
from job_hunt.services.workday.required_empty import (
    dedupe_preserve_order as _dedupe_preserve_order,
    filter_non_blocking_workday_skips as _filter_non_blocking_workday_skips,
    filter_required_empty_fields as _filter_required_empty_fields,
)
from job_hunt.services.workday.review_gate import (
    ReviewIssue,
    detect_review_issues,
    issues_to_payload,
    review_needs_repair as _workday_review_needs_repair_from_module,
    review_validation_messages as _workday_review_validation_messages,
)


app = typer.Typer(help="Job Hunt operations CLI.")
config_app = typer.Typer(help="Configuration commands.")
trace_app = typer.Typer(help="LangSmith tracing commands.")
activity_app = typer.Typer(help="Activity log commands.")
tracker_app = typer.Typer(help="Tracker commands.")
schedule_app = typer.Typer(help="Scheduler commands.")
email_app = typer.Typer(help="Gmail ingestion commands.")
llm_app = typer.Typer(help="LLM provider commands.")
review_app = typer.Typer(help="Review queue commands.")
contacts_app = typer.Typer(help="Contact CRM commands.")
outreach_app = typer.Typer(help="Outreach tracking commands.")

app.add_typer(config_app, name="config")
app.add_typer(trace_app, name="trace")
app.add_typer(activity_app, name="activity")
app.add_typer(tracker_app, name="tracker")
app.add_typer(schedule_app, name="schedule")
app.add_typer(email_app, name="email")
app.add_typer(llm_app, name="llm")
app.add_typer(review_app, name="review")
app.add_typer(contacts_app, name="contacts")
app.add_typer(outreach_app, name="outreach")

pipeline_app = typer.Typer(help="Pipeline.md URL inbox commands.")
app.add_typer(pipeline_app, name="pipeline")

console = Console()


ONBOARDING_NEXT_STEPS = """Next:
  1. Import or confirm your resume:
     .venv/bin/job-hunt import-resume '<resume.pdf-or-md>'
  2. Configure AI:
     .venv/bin/job-hunt configure-ai
  3. Start searching:
     .venv/bin/job-hunt search --save
  4. Review candidates:
     .venv/bin/job-hunt shortlist

Optional closed-loop add-ons:
  .venv/bin/job-hunt activity slack-test
  .venv/bin/job-hunt email poll --live --max-results 20
"""


def default_gcloud_adc_path() -> Path:
    return Path.home() / ".config/gcloud/application_default_credentials.json"


def _copy_setup_examples() -> None:
    copies = [
        (Path("config/settings.example.yml"), Path("config/settings.yml")),
        (Path("config/sites.example.yml"), Path("config/sites.yml")),
        (Path("config/portals.example.yml"), Path("config/portals.yml")),
        (Path("config/scheduler.example.yml"), Path("config/scheduler.yml")),
        (Path(".env.example"), Path(".env")),
    ]
    for src, dst in copies:
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.exists():
            shutil.copyfile(src, dst)
        elif dst == Path("config/settings.yml"):
            dst.write_text(
                yaml.safe_dump(Settings().model_dump(mode="json"), sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
        elif dst == Path(".env"):
            dst.write_text("# Add AI provider keys here. Run: job-hunt configure-ai\n", encoding="utf-8")
        elif dst == Path("config/portals.yml"):
            dst.write_text(
                yaml.safe_dump(
                    {
                        "title_filter": {
                            "positive": ["ai", "llm", "machine learning", "data", "software engineer"],
                            "negative": ["intern", "unpaid"],
                        },
                        "tracked_companies": [
                            {
                                "name": "Cohere",
                                "enabled": True,
                                "careers_url": "https://jobs.ashbyhq.com/cohere",
                            },
                            {
                                "name": "Anthropic",
                                "enabled": True,
                                "careers_url": "https://job-boards.greenhouse.io/anthropic",
                            },
                            {
                                "name": "LangChain",
                                "enabled": True,
                                "careers_url": "https://jobs.ashbyhq.com/langchain",
                            },
                        ],
                    },
                    sort_keys=False,
                    allow_unicode=True,
                ),
                encoding="utf-8",
            )
        else:
            dst.write_text("# Optional configuration created by job-hunt init.\n", encoding="utf-8")


def _write_onboarding_profile(
    path: Path,
    *,
    full_name: str,
    email: str,
    target_role: str,
    target_location: str,
) -> None:
    profile = {
        "candidate": {
            "full_name": full_name,
            "email": email,
            "phone": "",
            "location": target_location,
            "portfolio_url": "",
            "linkedin": "",
            "github": "",
        },
        "target_roles": {
            "primary": [target_role],
            "secondary": [],
        },
        "location": {
            "country": "",
            "city": target_location,
            "open_to_relocation": "",
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(profile, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _update_env_file(path: Path, updates: dict[str, str]) -> None:
    existing: dict[str, str] = {}
    order: list[str] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                order.append(line)
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            existing[key] = value
            order.append(key)
    for key, value in updates.items():
        existing[key] = value
        if key not in order:
            order.append(key)
    lines = []
    for item in order:
        if item in existing:
            value = existing[item]
            lines.append(f"{item}={json.dumps(value) if value and any(ch.isspace() for ch in value) else value}")
        else:
            lines.append(item)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _import_resume_file(source: Path, *, keep_original: bool = True) -> None:
    if not source.exists():
        console.print(f"[red]Resume file not found:[/red] {source}")
        raise typer.Exit(1)
    storage_dir = Path("storage/resumes")
    storage_dir.mkdir(parents=True, exist_ok=True)
    if keep_original:
        target = storage_dir / source.name
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        console.print(f"[green]Stored original resume:[/green] {target}")
    suffix = source.suffix.lower()
    if suffix in {".md", ".markdown", ".txt"}:
        text = source.read_text(encoding="utf-8")
    elif suffix == ".pdf":
        text = _extract_pdf_text(source)
    else:
        console.print("[yellow]Unsupported resume type for text extraction. Stored the original only.[/yellow]")
        return
    if not text.strip():
        console.print("[yellow]Could not extract resume text. Stored the original only.[/yellow]")
        return
    cv_path = Path("profile/cv.md")
    cv_path.parent.mkdir(parents=True, exist_ok=True)
    cv_path.write_text(text.strip() + "\n", encoding="utf-8")
    console.print(f"[green]Updated[/green] {cv_path}")


def _extract_pdf_text(source: Path) -> str:
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        return ""
    try:
        result = subprocess.run(
            [pdftotext, "-layout", str(source), "-"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError):
        return ""
    return result.stdout


def _shortlist_rows(*, limit: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    pipeline = Path("data/pipeline.md")
    if pipeline.exists():
        for line in reversed(pipeline.read_text(encoding="utf-8").splitlines()):
            if len(rows) >= limit:
                break
            if not line.startswith("- [ ]"):
                continue
            parts = [part.strip() for part in line.removeprefix("- [ ]").split("|")]
            url = parts[0] if parts and parts[0].startswith("http") else ""
            rows.append(
                {
                    "source": "pipeline",
                    "company": parts[1] if len(parts) > 1 else "",
                    "role": parts[2] if len(parts) > 2 else "",
                    "status": "pending",
                    "url": url,
                }
            )
    if len(rows) < limit:
        for entry in TrackerRepository(Path("data/applications.md")).parse():
            if len(rows) >= limit:
                break
            haystack = f"{entry.status} {entry.notes}".lower()
            if entry.status != "Evaluated" or not any(token in haystack for token in ["apply", "maybe", "hold"]):
                continue
            rows.append(
                {
                    "source": f"tracker #{entry.number}",
                    "company": entry.company,
                    "role": entry.role,
                    "status": f"{entry.score} {entry.status}",
                    "url": entry.report,
                }
            )
    return rows[:limit]


@app.command("init")
def onboarding_init(
    yes: bool = typer.Option(False, "--yes", "-y", help="Use defaults and do not prompt."),
    role: str | None = typer.Option(None, help="Target role, e.g. AI Engineer."),
    location: str | None = typer.Option(None, help="Target location, e.g. Canada Remote."),
    resume: Path | None = typer.Option(None, help="Optional resume PDF/Markdown/text file to import."),
    configure_ai_now: bool = typer.Option(False, "--configure-ai", help="Prompt for AI configuration during init."),
    guided: bool = typer.Option(
        True,
        "--guided/--no-guided",
        help=(
            "Run the conversational onboarding questionnaire after the basic setup. "
            "Skipped under --yes. Captures narrative fields used by evaluator framing."
        ),
    ),
) -> None:
    """Create the minimum local setup so a new user can start searching."""
    _copy_setup_examples()
    Path("profile").mkdir(exist_ok=True)
    Path("data").mkdir(exist_ok=True)
    Path("output").mkdir(exist_ok=True)
    Path("reports").mkdir(exist_ok=True)
    TrackerRepository(Path("data/applications.md")).ensure_exists()

    target_role = role or ("AI Engineer" if yes else typer.prompt("Target role", default="AI Engineer"))
    target_location = location or ("Canada Remote" if yes else typer.prompt("Target location", default="Canada Remote"))
    profile_path = Path("profile/profile.yml")
    if not profile_path.exists():
        full_name = "Your Name" if yes else typer.prompt("Full name", default="Your Name")
        email = "you@example.com" if yes else typer.prompt("Email", default="you@example.com")
        _write_onboarding_profile(
            profile_path,
            full_name=full_name,
            email=email,
            target_role=target_role,
            target_location=target_location,
        )
        console.print(f"[green]Created[/green] {profile_path}")
    else:
        console.print(f"[yellow]Skipped existing[/yellow] {profile_path}")

    if resume:
        _import_resume_file(resume)
    elif not Path("profile/cv.md").exists():
        Path("profile/cv.md").write_text(
            f"# Resume\n\nPaste your resume here, or run:\n\n"
            f"```bash\n.venv/bin/job-hunt import-resume '<resume.pdf-or-md>'\n```\n",
            encoding="utf-8",
        )
        console.print("[green]Created[/green] profile/cv.md placeholder")

    if configure_ai_now:
        configure_ai()

    if guided and not yes:
        from job_hunt.services import onboarding as _onb

        if not _onb.has_narrative(profile_path):
            console.print(
                "\n[bold]A few more questions[/bold] — these populate the evaluator's "
                "framing so it knows how to position you. Press Enter to skip any."
            )

            def _ask(prompt: str, default: str) -> str:
                return typer.prompt(prompt, default=default, show_default=False)

            wrote = _onb.run_guided_questions(profile_path, _ask)
            if wrote:
                console.print(f"[green]Saved narrative to[/green] {profile_path}")
            else:
                console.print("[dim]Skipped narrative (no answers provided).[/dim]")
        else:
            console.print(
                "[dim]Narrative already present in profile/profile.yml; skipping guided questions.[/dim]"
            )

    cv_present = Path("profile/cv.md").exists()
    profile_present = Path("profile/profile.yml").exists()
    tracker_present = Path("data/applications.md").exists()
    from job_hunt.services import onboarding as _onb

    console.print("")
    console.print(
        _onb.final_message(
            cv_present=cv_present,
            profile_present=profile_present,
            tracker_present=tracker_present,
        )
    )
    console.print("\n[dim]" + ONBOARDING_NEXT_STEPS.rstrip() + "[/dim]")


@app.command("configure-ai")
def configure_ai(
    provider: str = typer.Option("minimax", help="Currently supported runtime provider: minimax."),
    api_key: str | None = typer.Option(None, help="API key. If omitted, prompt securely."),
    base_url: str = typer.Option("https://api.minimax.chat", help="Minimax or compatible base URL."),
    model: str = typer.Option("", help="Model name, e.g. MiniMax-M2.7."),
    endpoint_style: str = typer.Option("minimax", help="minimax, anthropic, or openai."),
) -> None:
    """Configure the AI provider used for job evaluation."""
    if provider != "minimax":
        console.print("[yellow]Only the minimax provider is currently implemented; writing minimax-compatible settings.[/yellow]")
        provider = "minimax"
    key = api_key or typer.prompt("AI API key", hide_input=True)
    _update_env_file(
        Path(".env"),
        {
            "MINIMAX_API_KEY": key,
            "MINIMAX_BASE_URL": base_url,
            "MINIMAX_MODEL": model,
            "MINIMAX_ENDPOINT_STYLE": endpoint_style,
        },
    )
    _copy_setup_examples()
    settings_path = Path("config/settings.yml")
    settings = yaml.safe_load(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    settings.setdefault("llm", {}).setdefault("cheap", {})
    cheap = settings["llm"]["cheap"]
    cheap.update(
        {
            "provider": "minimax",
            "invocation": "http",
            "base_url": "${MINIMAX_BASE_URL}",
            "model": "${MINIMAX_MODEL}",
            "endpoint_style": endpoint_style,
            "api_key_env": "MINIMAX_API_KEY",
        }
    )
    settings_path.write_text(yaml.safe_dump(settings, sort_keys=False, allow_unicode=True), encoding="utf-8")
    console.print("[green]AI configuration saved.[/green]")
    console.print("Run: .venv/bin/job-hunt llm cheap-test")


@app.command("import-resume")
def import_resume(
    source: Path = typer.Argument(..., help="Resume PDF, Markdown, or text file."),
    keep_original: bool = typer.Option(True, "--keep-original/--no-keep-original", help="Copy the original file into storage/resumes."),
) -> None:
    """Import a resume into profile/cv.md so search/evaluation can start from a simple upload."""
    _import_resume_file(source, keep_original=keep_original)


@app.command("search")
def search_jobs(
    role: str | None = typer.Option(None, help="Target role hint shown in output. Direct ATS scan still uses config/portals.yml filters."),
    location: str | None = typer.Option(None, help="Target location hint shown in output."),
    company: str | None = typer.Option(None, help="Only scan matching company name."),
    limit_companies: int | None = typer.Option(None, help="Limit number of direct ATS companies."),
    include_non_canada: bool = typer.Option(False, help="Include jobs outside Canada in results."),
    save: bool = typer.Option(True, "--save/--no-save", help="Write new jobs to data/pipeline.md and scan history."),
) -> None:
    """Begin searching configured direct ATS portals and write a shortlist inbox."""
    if role or location:
        console.print(f"Search target: {role or 'configured roles'} / {location or 'configured locations'}")
        console.print("[dim]Current scanner uses config/portals.yml title and Canada filters; role/location are onboarding hints.[/dim]")
    scan(
        company=company,
        limit_companies=limit_companies,
        include_non_canada=include_non_canada,
        apply=save,
    )
    console.print("\nNext: .venv/bin/job-hunt shortlist")


@app.command("shortlist")
def shortlist(limit: int = typer.Option(20, help="Number of items to show.")) -> None:
    """Show jobs that are ready for review from the local pipeline/tracker."""
    rows = _shortlist_rows(limit=limit)
    table = Table("#", "Source", "Company", "Role", "Score/Status", "URL or Report")
    for index, row in enumerate(rows, start=1):
        table.add_row(str(index), row["source"], row["company"], row["role"], row["status"], _short(row["url"], 80))
    console.print(table)
    if rows:
        console.print("\nEvaluate a URL: .venv/bin/job-hunt evaluate '<url>'")
        console.print("Prepare apply flow: .venv/bin/job-hunt loop '<url>'")
    else:
        console.print("No shortlist items found. Run: .venv/bin/job-hunt search --save")


@config_app.command("validate")
def config_validate(path: Path = Path("config/settings.yml")) -> None:
    settings = load_settings(path)
    console.print("[green]Configuration is valid.[/green]")
    console.print(f"Environment: {settings.app.env}")
    console.print(f"LangSmith enabled: {settings.observability.langsmith.enabled}")


@config_app.command("doctor")
def config_doctor() -> None:
    from job_hunt.services.profile_loader import current_mode as _read_mode

    settings = load_settings()
    checks = [
        ("settings", Path("config/settings.yml").exists(), "config/settings.yml"),
        ("sites", Path("config/sites.yml").exists(), "config/sites.yml"),
        ("scheduler", Path("config/scheduler.yml").exists(), "config/scheduler.yml"),
        ("env", Path(".env").exists(), ".env"),
        (
            "gcloud adc",
            default_gcloud_adc_path().exists() or bool(os.getenv("GOOGLE_APPLICATION_CREDENTIALS")),
            str(default_gcloud_adc_path()),
        ),
        ("activity log parent", settings.activity.sinks.local_log.path.parent.exists(), str(settings.activity.sinks.local_log.path.parent)),
        ("gitignore", Path(".gitignore").exists(), ".gitignore"),
    ]
    table = Table("Check", "Status", "Detail")
    for name, ok, detail in checks:
        table.add_row(name, "ok" if ok else "missing", detail, style="green" if ok else "yellow")
    console.print(table)
    mode = _read_mode()
    console.print(f"\n[bold]Operator mode:[/bold] [cyan]{mode}[/cyan] "
                  f"(set in profile/profile.yml; see docs/design-notes.md §N)")
    console.print("Doctor finished. Missing local config files are expected until you copy the examples.")


@config_app.command("set-mode")
def config_set_mode(
    value: str = typer.Argument(..., help="Target mode: student or full."),
    force: bool = typer.Option(False, "--force", help="Allow setting to current value (no-op)."),
) -> None:
    """Atomically flip ``profile/profile.yml`` ``mode`` between student and full.

    The value is the only thing the system reads to switch discovery, scoring,
    and apply behaviour. See docs/design-notes.md §N.2.
    """
    target = value.strip().lower()
    if target not in ("student", "full"):
        console.print(f"[red]Invalid mode:[/red] {value!r}. Allowed values: student, full.")
        raise typer.Exit(2)

    profile_path = Path("profile/profile.yml")
    if not profile_path.exists():
        console.print(
            f"[red]profile/profile.yml not found.[/red] Run `job-hunt init` or "
            "copy config/profile.example.yml first."
        )
        raise typer.Exit(2)

    raw = profile_path.read_text(encoding="utf-8")
    try:
        parsed = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        console.print(f"[red]profile/profile.yml is malformed:[/red] {exc}")
        raise typer.Exit(2)

    current = (parsed.get("mode") or "").strip().lower() if isinstance(parsed.get("mode"), str) else ""
    if current == target and not force:
        console.print(
            f"[yellow]No change:[/yellow] mode is already {target!r}. "
            "Pass --force to rewrite anyway."
        )
        raise typer.Exit(0)

    # Surgical text edit — preserve comments and ordering. Match the first
    # ``mode:`` line at column 0 (top-level only); fall back to inserting at
    # the top of the file when no such line exists.
    pattern = re.compile(r"^mode:\s*.*$", re.MULTILINE)
    if pattern.search(raw):
        new_text = pattern.sub(f'mode: "{target}"', raw, count=1)
    else:
        header = (
            f'# Top-level mode switch — see docs/design-notes.md Section N.\n'
            f'mode: "{target}"\n\n'
        )
        new_text = header + raw

    tmp_path = profile_path.with_suffix(profile_path.suffix + ".tmp")
    tmp_path.write_text(new_text, encoding="utf-8")
    tmp_path.replace(profile_path)

    console.print(f"[green]Mode set to {target!r}.[/green]")
    console.print(
        "Subsystems that will pick up the change on next invocation: "
        "scan (title filter + tracked-companies eligibility), "
        "evaluate (eligibility gate + scoring weights + thresholds), "
        "cover_letter (narrative variant), apply (auto-submit gate)."
    )


@config_app.command("init")
def config_init() -> None:
    copies = [
        (Path("config/settings.example.yml"), Path("config/settings.yml")),
        (Path("config/sites.example.yml"), Path("config/sites.yml")),
        (Path("config/portals.example.yml"), Path("config/portals.yml")),
        (Path("config/scheduler.example.yml"), Path("config/scheduler.yml")),
        (Path(".env.example"), Path(".env")),
    ]
    for src, dst in copies:
        if dst.exists():
            console.print(f"[yellow]Skipped existing[/yellow] {dst}")
            continue
        shutil.copyfile(src, dst)
        console.print(f"[green]Created[/green] {dst}")


@trace_app.command("status")
def trace_status() -> None:
    manager = TraceManager(load_settings())
    status = manager.status()
    console.print(f"LangSmith enabled: {status.enabled}")
    console.print(f"Project: {status.project}")
    console.print(f"Example local trace id: {status.trace_id}")


@trace_app.command("on")
def trace_on() -> None:
    console.print("Set JOB_HUNT_LANGSMITH_ENABLED=true or edit config/settings.yml.")


@trace_app.command("off")
def trace_off() -> None:
    console.print("Set JOB_HUNT_LANGSMITH_ENABLED=false or edit config/settings.yml.")


@trace_app.command("smoke-test")
def trace_smoke_test() -> None:
    settings = load_settings()
    if not settings.observability.langsmith.enabled:
        console.print("[yellow]LangSmith is disabled. Set JOB_HUNT_LANGSMITH_ENABLED=true to send traces.[/yellow]")
        raise typer.Exit(1)

    from langsmith import traceable

    @traceable(
        name="job-hunt.langsmith_smoke_test",
        run_type="chain",
        metadata={"app": "job-hunt", "test": "smoke"},
    )
    def smoke() -> dict:
        return {"ok": True, "message": "langsmith smoke test"}

    result = smoke()
    console.print(f"Sent smoke trace to project: {settings.observability.langsmith.project}")
    console.print_json(data=result)


@activity_app.command("list")
def activity_list(
    limit: int = typer.Option(20, help="Maximum number of events to show."),
    since: str | None = typer.Option(None, help="Only show events since a duration like 1d/12h/30m or an ISO timestamp."),
) -> None:
    settings = load_settings()
    try:
        events = read_activity(settings.activity.sinks.local_log.path, limit=limit, since=since)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--since") from None
    table = Table("Time", "Level", "Type", "Summary")
    for event in events:
        table.add_row(event.ts.isoformat(), event.level, event.type, event.summary)
    console.print(table)


@activity_app.command("tail")
def activity_tail(limit: int = 20) -> None:
    activity_list(limit=limit, since=None)


@activity_app.command("slack-test")
def activity_slack_test() -> None:
    settings = load_settings()
    logger = ActivityLogger(settings.activity)
    logger.emit(ActivityEvent(type="activity.slack_test", level="info", summary="Slack test from job-hunt"))
    console.print("Slack test event emitted. Check local activity log and Slack config.")


@tracker_app.command("init")
def tracker_init(path: Path = Path("data/applications.md")) -> None:
    repo = TrackerRepository(path)
    repo.ensure_exists()
    console.print(f"[green]Tracker ready:[/green] {path}")


@tracker_app.command("stats")
def tracker_stats(path: Path = Path("data/applications.md")) -> None:
    repo = TrackerRepository(path)
    stats = repo.stats()
    console.print(f"Total: {stats['total']}")
    console.print(f"With PDF: {stats['with_pdf']}")
    table = Table("Status", "Count")
    for status, count in sorted(stats["by_status"].items()):
        table.add_row(status, str(count))
    console.print(table)


@tracker_app.command("verify")
def tracker_verify(
    path: Path = Path("data/applications.md"),
    additions_dir: Path = typer.Option(
        Path("data/tracker-additions"), help="Tracker TSV staging directory."
    ),
    reports_dir: Path = typer.Option(
        None, help="Resolve report links against this directory (skip if omitted)."
    ),
) -> None:
    from job_hunt.services import tracker_ops

    result = tracker_ops.verify_pipeline(
        applications_md=path,
        additions_dir=additions_dir,
        reports_dir=reports_dir,
    )
    console.print(f"Entries: {result.entries}")
    for warning in result.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")
    for error in result.errors:
        console.print(f"[red]error:[/red] {error}")
    if result.errors:
        raise typer.Exit(1)
    console.print("[green]Tracker verification passed.[/green]")


@tracker_app.command("merge")
def tracker_merge(
    additions_dir: Path = Path("data/tracker-additions"),
    path: Path = Path("data/applications.md"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report actions without writing."),
) -> None:
    """Merge pending TSV additions into applications.md."""
    from job_hunt.services import tracker_ops

    result = tracker_ops.merge(additions_dir=additions_dir, applications_md=path, dry_run=dry_run)
    if not result.actions and not result.added and not result.updated:
        console.print("[green]No pending additions.[/green]")
        return
    for action in result.actions:
        if action.kind == "added":
            console.print(f"[green]+ #{action.number}[/green] {action.company} — {action.role} ({action.detail})")
        elif action.kind == "updated":
            console.print(f"[cyan]~ #{action.number}[/cyan] {action.company} — {action.role} ({action.detail})")
        else:
            console.print(f"[dim]· skip[/dim] {action.company} — {action.role} ({action.detail})")
    console.print(
        f"Summary: +{result.added} added, ~{result.updated} updated, ·{result.skipped} skipped"
        + (" (dry-run)" if dry_run else "")
    )


@tracker_app.command("dedup")
def tracker_dedup(
    path: Path = Path("data/applications.md"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report actions without writing."),
) -> None:
    """Remove duplicate (company, role) entries; promote status if a duplicate is further along."""
    from job_hunt.services import tracker_ops

    result = tracker_ops.dedup(applications_md=path, dry_run=dry_run)
    for kept_num, old_status, new_status in result.promoted:
        console.print(f"[cyan]promoted #{kept_num}[/cyan] {old_status} → {new_status}")
    for entry in result.removed_entries:
        console.print(f"[yellow]drop #{entry.number}[/yellow] {entry.company} — {entry.role} ({entry.score})")
    console.print(f"Removed: {result.removed}" + (" (dry-run)" if dry_run else ""))


@tracker_app.command("normalize")
def tracker_normalize(
    path: Path = Path("data/applications.md"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report actions without writing."),
) -> None:
    """Rewrite status field of every row to the canonical label per states.yml."""
    from job_hunt.services import tracker_ops

    result = tracker_ops.normalize_statuses(applications_md=path, dry_run=dry_run)
    for num, old, new in result.changed_entries:
        console.print(f"#{num}: {old!r} → {new!r}")
    for num, raw in result.unknowns:
        console.print(f"[yellow]unknown[/yellow] #{num}: {raw!r}")
    console.print(f"Changes: {result.changes}" + (" (dry-run)" if dry_run else ""))


@contacts_app.command("add")
def contacts_add(
    company: str = typer.Option(..., help="Company name."),
    name: str = typer.Option("", help="Contact name."),
    title: str = typer.Option("", help="Contact title."),
    linkedin_url: str = typer.Option("", help="LinkedIn profile URL."),
    email: str = typer.Option("", help="Email address, if known."),
    relationship: str = typer.Option(
        "unknown", help="recruiter / hiring_manager / peer / referral / unknown."
    ),
    source: str = typer.Option("manual", help="manual / linkedin / email / web_search."),
    notes: str = typer.Option("", help="Optional notes."),
) -> None:
    """Add a recruiter, hiring manager, peer, or referral contact."""
    from job_hunt.services.outreach import Contact, add_contact

    contact = add_contact(
        Contact(
            company=company,
            name=name,
            title=title,
            linkedin_url=linkedin_url,
            email=email,
            relationship=relationship,
            source=source,
            notes=notes,
        )
    )
    label = contact.name or contact.title or "unknown"
    console.print(f"[green]+ contact[/green] {contact.id} {contact.company} — {label}")


@contacts_app.command("search")
def contacts_search(company: str = typer.Option("", help="Filter by company substring.")) -> None:
    """Search local contacts."""
    from job_hunt.services.outreach import find_contacts

    contacts = find_contacts(company)
    if not contacts:
        console.print("[dim]No contacts found.[/dim]")
        return
    table = Table("ID", "Company", "Name", "Title", "Relationship", "LinkedIn", "Notes")
    for contact in contacts:
        table.add_row(
            contact.id,
            contact.company,
            contact.name,
            contact.title,
            contact.relationship,
            contact.linkedin_url,
            contact.notes,
        )
    console.print(table)


@outreach_app.command("draft")
def outreach_draft(
    contact_id: str = typer.Argument(..., help="Contact id or unique prefix."),
    company: str = typer.Option("", help="Override company; defaults to contact company."),
    role: str = typer.Option(..., help="Target role."),
    application_id: int | None = typer.Option(None, help="Tracker row number, if relevant."),
    jd: str | None = typer.Option(None, "--jd", help="Path to JD file or pasted JD text."),
    output: Path | None = typer.Option(None, help="Optional path for the draft message."),
    max_tokens: int = typer.Option(900, help="LLM max tokens."),
) -> None:
    """Draft a LinkedIn outreach message and record a drafted outreach event."""
    from job_hunt.services.outreach import OutreachEvent, add_event, get_contact

    contact = get_contact(contact_id)
    if contact is None:
        console.print(f"[red]Contact not found:[/red] {contact_id}")
        raise typer.Exit(1)
    target_company = company or contact.company
    jd_text = ""
    if jd:
        jd_path = Path(jd)
        jd_text = jd_path.read_text(encoding="utf-8") if jd_path.exists() else jd
    body = _run_one_shot_prompt(
        template="linkedin_outreach.md",
        node_name="outreach_draft",
        graph_name="outreach_cli",
        max_tokens=max_tokens,
        temperature=0.4,
        company=target_company,
        role=role,
        jd_text=jd_text,
        cv_excerpt=_load_cv_excerpt(),
    )
    console.print(body)
    message_path = output
    if message_path is None:
        company_slug = re.sub(r"[^a-z0-9]+", "-", target_company.lower()).strip("-")
        role_slug = re.sub(r"[^a-z0-9]+", "-", role.lower()).strip("-")
        message_path = Path("data/outreach-drafts") / (
            f"{datetime.now().date()}-{company_slug}-{role_slug}.md"
        )
    message_path.parent.mkdir(parents=True, exist_ok=True)
    message_path.write_text(body, encoding="utf-8")
    event = add_event(
        OutreachEvent(
            contact_id=contact.id,
            application_id=application_id,
            company=target_company,
            role=role,
            channel="linkedin",
            status="drafted",
            message_path=str(message_path),
            notes=f"Drafted for {contact.name or contact.title or contact.id}",
        )
    )
    console.print(f"\n[green]Recorded draft[/green] outreach event {event.id}; wrote {message_path}")


@outreach_app.command("mark-sent")
def outreach_mark_sent(
    event_id: str = typer.Argument(..., help="Outreach event id or unique prefix."),
    follow_up_at: str = typer.Option("", help="Optional follow-up date YYYY-MM-DD."),
    notes: str = typer.Option("", help="Optional notes."),
) -> None:
    """Mark an outreach event as sent."""
    from job_hunt.services.outreach import update_event

    event = update_event(event_id, status="sent", follow_up_at=follow_up_at, notes=notes)
    if event is None:
        console.print(f"[red]Outreach event not found:[/red] {event_id}")
        raise typer.Exit(1)
    console.print(f"[green]Marked sent[/green] {event.id} {event.company} — {event.role}")


@outreach_app.command("mark")
def outreach_mark(
    event_id: str = typer.Argument(..., help="Outreach event id or unique prefix."),
    status: str = typer.Argument(..., help="responded / follow_up_due / closed."),
    notes: str = typer.Option("", help="Optional notes."),
) -> None:
    """Mark an outreach event as responded, follow_up_due, or closed."""
    from job_hunt.services.outreach import update_event

    if status not in {"responded", "follow_up_due", "closed"}:
        console.print("[red]Status must be responded, follow_up_due, or closed.[/red]")
        raise typer.Exit(1)
    event = update_event(event_id, status=status, notes=notes)
    if event is None:
        console.print(f"[red]Outreach event not found:[/red] {event_id}")
        raise typer.Exit(1)
    console.print(f"[green]Marked {status}[/green] {event.id} {event.company} — {event.role}")


@outreach_app.command("due")
def outreach_due() -> None:
    """List sent outreach events whose follow-up date is due."""
    from job_hunt.services.outreach import due_events

    events = due_events()
    if not events:
        console.print("[dim]No outreach follow-ups due.[/dim]")
        return
    table = Table("ID", "Company", "Role", "Contact", "Follow-up", "Notes")
    for event in events:
        table.add_row(
            event.id,
            event.company,
            event.role,
            event.contact_id,
            event.follow_up_at,
            event.notes,
        )
    console.print(table)


@pipeline_app.command("add")
def pipeline_add(
    url: str = typer.Argument(..., help="Job posting URL to drop in the inbox."),
    company: str = typer.Option("", help="Company name (optional, helps tracker dedup)."),
    role: str = typer.Option("", help="Role title (optional)."),
    path: Path = typer.Option(Path("data/pipeline.md"), help="Pipeline file path."),
) -> None:
    """Append a URL to the pending inbox in pipeline.md."""
    from job_hunt.services import pipeline_inbox

    if pipeline_inbox.add(url, company=company, role=role, path=path):
        console.print(f"[green]+ pending[/green] {url}")
    else:
        console.print(f"[yellow]Already in pipeline:[/yellow] {url}")


@pipeline_app.command("list")
def pipeline_list(
    status: str = typer.Option("all", help="Filter: all / pending / processed / error."),
    path: Path = typer.Option(Path("data/pipeline.md"), help="Pipeline file path."),
) -> None:
    """List entries in pipeline.md (default: all)."""
    from job_hunt.services import pipeline_inbox

    status_filter = None
    if status != "all":
        try:
            status_filter = pipeline_inbox.EntryStatus(status)
        except ValueError:
            console.print(f"[red]Unknown status:[/red] {status}")
            raise typer.Exit(1) from None
    entries = pipeline_inbox.list_entries(status=status_filter, path=path)
    if not entries:
        console.print("[dim]No entries.[/dim]")
        return
    for e in entries:
        marker = {
            pipeline_inbox.EntryStatus.PENDING: "[ ]",
            pipeline_inbox.EntryStatus.PROCESSED: "[x]",
            pipeline_inbox.EntryStatus.ERROR: "[!]",
        }[e.status]
        head = f"#{e.tracker_id} | " if e.tracker_id else ""
        suffix_parts = [p for p in (e.score, f"PDF {e.pdf_check}".strip() if e.pdf_check else "", e.note) if p]
        suffix = (" | " + " | ".join(suffix_parts)) if suffix_parts else ""
        console.print(f"{marker} {head}{e.url} | {e.company} | {e.role}{suffix}")


@pipeline_app.command("process")
def pipeline_process(
    limit: int = typer.Option(0, help="Process at most N entries (0 = all)."),
    cover_letter: bool | None = typer.Option(
        None,
        "--cover-letter/--no-cover-letter",
        help="Pass-through to evaluate; defaults to apply.cover_letter_default.",
    ),
    path: Path = typer.Option(Path("data/pipeline.md"), help="Pipeline file path."),
) -> None:
    """Run `evaluate` on each pending URL, then move it to Processed (or Error)."""
    from job_hunt.services import pipeline_inbox

    pending = pipeline_inbox.list_entries(
        status=pipeline_inbox.EntryStatus.PENDING, path=path
    )
    if not pending:
        console.print("[green]No pending entries.[/green]")
        return
    if limit > 0:
        pending = pending[:limit]

    for entry in pending:
        console.print(f"\n[bold]→[/bold] Evaluating {entry.url}")
        try:
            evaluate(target=entry.url, source_type="auto", trace=None, cover_letter=cover_letter)
        except SystemExit as exc:
            # evaluate() may exit on cv-sync-check errors etc; treat as inbox error.
            note = f"evaluate exited (code={exc.code})"
            pipeline_inbox.mark_error(entry.url, note=note, path=path)
            console.print(f"[red]error:[/red] {note}")
            continue
        except Exception as exc:  # noqa: BLE001 — surface unexpected failures into inbox
            note = str(exc)[:200]
            pipeline_inbox.mark_error(entry.url, note=note, path=path)
            console.print(f"[red]error:[/red] {note}")
            continue

        # Look up the freshly-written tracker row by URL match (best-effort).
        tracker = TrackerRepository(Path("data/applications.md"))
        latest = sorted(tracker.parse(), key=lambda e: e.number, reverse=True)
        match = next((e for e in latest if entry.url in e.report or entry.url in e.notes), None)
        if match:
            pdf_check = "✅" if "✅" in match.pdf else "❌"
            pipeline_inbox.mark_processed(
                entry.url,
                tracker_id=match.number,
                score=match.score,
                pdf_check=pdf_check,
                company=match.company,
                role=match.role,
                path=path,
            )
            console.print(f"[green]✓ processed[/green] #{match.number} {match.company} — {match.role}")
        else:
            pipeline_inbox.mark_error(
                entry.url, note="evaluate finished but tracker row not found", path=path
            )


@tracker_app.command("dashboard")
def tracker_dashboard(
    path: Path = typer.Option(Path("data/applications.md"), help="Tracker source file."),
    output: Path = typer.Option(
        Path("data/dashboard.html"),
        help="Output HTML path. Open in a browser after generation.",
    ),
) -> None:
    """Generate a self-contained HTML dashboard from the tracker."""
    from job_hunt.services import dashboard_html

    count = dashboard_html.generate(apps_path=path, output_path=output)
    console.print(f"[green]Wrote[/green] {output} ({count} applications)")


@tracker_app.command("check-sync")
def tracker_check_sync() -> None:
    """Run the cv/profile/prompts/digest consistency check."""
    from job_hunt.services import cv_sync_check

    result = cv_sync_check.run()
    for warning in result.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")
    for error in result.errors:
        console.print(f"[red]error:[/red] {error}")
    if result.errors:
        raise typer.Exit(1)
    console.print("[green]CV sync check passed.[/green]")


@schedule_app.command("list")
def schedule_list() -> None:
    console.print("Scheduler implementation is planned for Phase 6. See docs/design.md.")


@schedule_app.command("run")
def schedule_run(job_id: str) -> None:
    console.print(f"Scheduler job placeholder: {job_id}")


@email_app.command("poll")
def email_poll(
    since: str = "30d",
    max_results: int = 25,
    live: bool = False,
    include_unknown: bool = False,
) -> None:
    settings = load_settings()
    console.print(f"Gmail polling for since={since}.")
    console.print(f"Query: {settings.email_ingest.query.strip()[:160]}...")
    console.print(f"Token path: {settings.email_ingest.token_path}")
    if not live:
        console.print("Dry-run only. Pass --live to call Gmail API.")
        return
    result = poll_gmail(settings, max_results=max_results, include_unknown=include_unknown)
    ActivityLogger(settings.activity).emit(
        ActivityEvent(
            type="email.poll_completed",
            level="info",
            summary=f"Gmail poll completed: {result.events_created} events from {result.scanned} messages",
            payload={
                "scanned": result.scanned,
                "skipped_seen": result.skipped_seen,
                "events_created": result.events_created,
            },
        )
    )
    console.print(f"Scanned: {result.scanned}")
    console.print(f"Skipped seen: {result.skipped_seen}")
    console.print(f"Events created: {result.events_created}")
    for event in result.events:
        console.print(f"- {event.event_type}: {event.company or '?'} / {event.role or '?'} ({event.confidence:.2f})")


@email_app.command("events")
def email_events(limit: int = 20, needs_review: bool = False) -> None:
    repo = EmailEventRepository()
    events = repo.list(limit=limit, needs_review=needs_review)
    table = Table("Time", "Type", "Company", "Role", "Confidence", "Review", "Subject")
    for event in events:
        table.add_row(
            event.event_time.isoformat(),
            event.event_type,
            event.company or "",
            event.role or "",
            f"{event.confidence:.2f}",
            "yes" if event.needs_review else "no",
            event.subject[:60],
        )
    console.print(table)


@email_app.command("parse-sample")
def email_parse_sample(
    subject: str = typer.Option(..., help="Email subject to classify."),
    sender: str = typer.Option("recruiting@example.com", help="Email sender."),
    body: str = typer.Option("", help="Email body/snippet."),
    save: bool = typer.Option(False, help="Persist the parsed event to data/email-events.jsonl."),
) -> None:
    parsed = ParsedEmail(
        message_id="sample",
        thread_id=None,
        sender=sender,
        subject=subject,
        snippet=body[:240],
        body=body,
        date=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    event = classify_email_event(parsed)
    if save:
        EmailEventRepository().append(event)
    console.print_json(event.model_dump_json())


@email_app.command("import-events")
def email_import_events(
    apply: bool = False,
    limit: int = 20,
    import_new: bool = False,
    new_only: bool = False,
    skip_review: bool = False,
) -> None:
    result = reconcile_email_events(
        apply=apply,
        limit=limit,
        import_new=import_new,
        update_existing=not new_only,
        create_review=not skip_review,
    )
    console.print(f"Scanned: {result.scanned}")
    console.print(f"Matched existing: {result.matched}")
    console.print(f"Updated existing: {result.updated}")
    console.print(f"Imported new: {result.imported}")
    console.print(f"Review created: {result.review_created}")
    console.print(f"Skipped: {result.skipped}")


@email_app.command("reconcile")
def email_reconcile(
    apply: bool = False,
    limit: int = 50,
    import_new: bool = False,
    new_only: bool = False,
    skip_review: bool = False,
) -> None:
    email_import_events(
        apply=apply,
        limit=limit,
        import_new=import_new,
        new_only=new_only,
        skip_review=skip_review,
    )


@email_app.command("review-candidates")
def email_review_candidates(limit: int = 50) -> None:
    events = list_review_candidates(limit=limit)
    table = Table("ID", "Type", "Company", "Role", "Confidence", "Subject")
    table.columns[0].no_wrap = True
    for event in events:
        table.add_row(
            event.id[:12],
            event.event_type,
            _short(event.company or "", 28),
            _short(event.role or "", 44),
            f"{event.confidence:.2f}",
            _short(event.subject, 56),
        )
    console.print(table)


@email_app.command("approve-event")
def email_approve_event(
    event_id: str,
    company: str | None = None,
    role: str | None = None,
    status: str | None = None,
    force_new: bool = False,
    note: str = "Approved from Gmail review",
) -> None:
    try:
        result = approve_email_event(
            event_id,
            company=company,
            role=role,
            status=status,
            force_new=force_new,
            note=note,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    if result.tracker_entry:
        entry = result.tracker_entry
        console.print(f"Imported tracker row #{entry.number}: {entry.company} / {entry.role} -> {entry.status}")
        return
    if result.matched_existing:
        entry = result.matched_existing
        console.print(
            f"Matched existing tracker row #{entry.number}: {entry.company} / {entry.role} "
            f"({result.match_score:.2f})"
        )


@email_app.command("ignore-event")
def email_ignore_event(event_id: str, note: str = "") -> None:
    try:
        event = ignore_email_event(event_id, note=note)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    console.print(f"Ignored email event {event.id[:12]}: {event.company or '?'} / {event.role or '?'}")


@review_app.command("list")
def review_list(limit: int = 20, status: str = "open") -> None:
    items = ReviewRepository().list(limit=limit, status=status)
    table = Table("ID", "Type", "Priority", "Status", "Summary")
    for item in items:
        table.add_row(item.id[:12], item.type, item.priority, item.status, item.summary)
    console.print(table)


@llm_app.command("cheap-test")
def llm_cheap_test(
    prompt: str = "Say ok in one short sentence.",
    max_tokens: int = 1024,
) -> None:
    settings = load_settings()
    provider = build_cheap_provider(settings)

    async def run() -> None:
        result = await traced_chat(
            provider,
            settings=settings,
            messages=[ChatMessage(role="user", content=prompt)],
            model=settings.llm.cheap.model,
            node_name="cheap_test",
            graph_name="llm_smoke_test",
            model_tier="cheap",
            temperature=settings.llm.cheap.temperature,
            max_tokens=max_tokens,
        )
        console.print(result.content)
        console.print(f"provider={result.provider} tier={result.tier}")
        console.print(f"tokens={result.total_tokens} estimated={result.usage_estimated}")

    asyncio.run(run())


@app.command("evaluate")
def evaluate(
    target: str,
    source_type: str = typer.Option("auto", help="auto, url, jd_text, or local_file."),
    trace: bool | None = typer.Option(None, help="Temporarily enable/disable LangSmith for this run."),
    cover_letter: bool | None = typer.Option(
        None,
        "--cover-letter/--no-cover-letter",
        help="Generate a standalone one-page cover letter PDF. Defaults to apply.cover_letter_default in profile.yml.",
    ),
) -> None:
    if trace is not None:
        os.environ["JOB_HUNT_LANGSMITH_ENABLED"] = "true" if trace else "false"

    if os.environ.get("JOB_HUNT_SKIP_CV_SYNC_CHECK") != "1":
        from job_hunt.services import cv_sync_check

        sync_result = cv_sync_check.run()
        for warning in sync_result.warnings:
            console.print(f"[yellow]cv-sync-check:[/yellow] {warning}")
        if sync_result.errors:
            for error in sync_result.errors:
                console.print(f"[red]cv-sync-check:[/red] {error}")
            console.print("[red]Aborting evaluate.[/red] Run `job-hunt tracker check-sync` for details.")
            raise typer.Exit(1)

    resolved_source = _resolve_source_type(target, source_type)
    run_id = uuid.uuid4().hex
    profile_values = _apply_profile_values()
    if cover_letter is None:
        generate_cover_letter_flag = bool(profile_values.get("apply_cover_letter_default"))
    else:
        generate_cover_letter_flag = cover_letter
    state = {
        "run_id": run_id,
        "thread_id": run_id,
        "input": target,
        "source_type": resolved_source,
        "url": target if resolved_source == "url" else None,
        "generate_cover_letter": generate_cover_letter_flag,
        "errors": [],
    }

    async def run() -> None:
        graph = build_evaluate_job_graph()
        result = await graph.ainvoke(
            state,
            config={"configurable": {"thread_id": run_id}},
        )
        scores = result.get("scores")
        console.print(f"Run: {run_id}")
        console.print(f"Recommendation: {result.get('recommendation', 'skip')}")
        if scores:
            console.print(f"Score: {scores.weighted_total:.2f}/5.0")
        if result.get("report_path"):
            console.print(f"Report: {result['report_path']}")
        if result.get("pdf_path"):
            console.print(f"PDF: {result['pdf_path']}")
        if result.get("cover_letter_path"):
            console.print(f"Cover letter: {result['cover_letter_path']}")
        errors = result.get("errors") or []
        for error in errors:
            console.print(f"[yellow]warning:[/yellow] {error}")

    asyncio.run(run())


def _resolve_source_type(target: str, source_type: str) -> str:
    if source_type != "auto":
        if source_type not in {"url", "jd_text", "local_file"}:
            raise typer.BadParameter("source_type must be auto, url, jd_text, or local_file")
        return source_type
    if target.startswith(("http://", "https://")):
        return "url"
    # P2-10: `local:jds/foo.md` is treated as a URL — web_extract intercepts the
    # `local:` scheme and reads the file directly.
    if target.startswith("local:"):
        return "url"
    if Path(target).exists() or (Path("jds") / target).exists():
        return "local_file"
    return "jd_text"


def _short(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _load_cv_excerpt() -> str:
    """Load CV markdown from the canonical profile path."""
    candidate = Path("profile/cv.md")
    if candidate.exists():
        try:
            return candidate.read_text(encoding="utf-8")
        except OSError:
            return ""
    return ""


def _build_research_context_for_prompt(
    *,
    company: str,
    role: str,
    enabled: bool,
    purpose: str,
) -> str:
    """Run a small set of grounding queries and return a formatted block.

    Returns ``""`` when ``--with-search`` was not requested, the WebSearch
    provider is unconfigured, or every query yielded zero hits. Callers
    pass the value into prompts that conditionally render
    ``{% if research_context %}{{ research_context }}{% endif %}``.

    ``purpose`` selects the query bundle: ``"research"`` favours strategy /
    recent moves / engineering culture; ``"linkedin"`` favours news the
    sender can reference as a hook.
    """
    if not enabled:
        return ""
    from job_hunt.services.web_search import (
        build_web_search_provider,
        format_search_hits,
    )

    settings = load_settings()
    provider = build_web_search_provider(settings)
    if provider is None:
        console.print(
            "[dim]--with-search ignored: web_search.provider is unset or "
            "BRAVE_API_KEY is missing.[/dim]"
        )
        return ""

    company_clean = company.strip()
    role_clean = role.strip()
    if purpose == "linkedin":
        queries = [
            f"{company_clean} news {role_clean}",
            f"{company_clean} engineering blog {role_clean}",
            f"{company_clean} product announcement",
        ]
    else:  # research / default
        queries = [
            f"{company_clean} {role_clean} engineering team",
            f"{company_clean} recent product news",
            f"{company_clean} engineering blog",
            f"{company_clean} glassdoor culture",
        ]
    return format_search_hits(provider, queries)


def _run_one_shot_prompt(
    *,
    template: str,
    node_name: str,
    graph_name: str,
    max_tokens: int,
    temperature: float,
    **template_kwargs,
) -> str:
    """Render `template` and call the cheap LLM tier. Returns the response text."""
    from job_hunt.nodes._prompts import render

    prompt = render(template, **template_kwargs)
    settings = load_settings()
    provider = build_cheap_provider(settings)

    async def run() -> str:
        result = await traced_chat(
            provider,
            settings=settings,
            messages=[ChatMessage(role="user", content=prompt)],
            model=settings.llm.cheap.model,
            node_name=node_name,
            graph_name=graph_name,
            model_tier="cheap",
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return result.content

    return asyncio.run(run())


@app.command("linkedin")
def linkedin_outreach(
    company: str = typer.Argument(..., help="Target company."),
    role: str = typer.Argument(..., help="Target role."),
    jd: str | None = typer.Option(
        None, "--jd", help="Path to JD file or pasted JD text. Optional."
    ),
    output: Path | None = typer.Option(
        None, help="Optional path to write the message draft. Stdout always prints."
    ),
    max_tokens: int = typer.Option(900, help="LLM max tokens."),
    with_search: bool = typer.Option(
        False,
        "--with-search",
        help=(
            "Run live Brave WebSearch queries to ground the connection-request "
            "hook in real recent signals. No-op when web_search.provider is unset."
        ),
    ),
) -> None:
    """Draft a 300-char LinkedIn connection-request message + targets list."""
    jd_text = ""
    if jd:
        jd_path = Path(jd)
        jd_text = jd_path.read_text(encoding="utf-8") if jd_path.exists() else jd

    research_context = _build_research_context_for_prompt(
        company=company,
        role=role,
        enabled=with_search,
        purpose="linkedin",
    )

    body = _run_one_shot_prompt(
        template="linkedin_outreach.md",
        node_name="linkedin_outreach",
        graph_name="linkedin_outreach_cli",
        max_tokens=max_tokens,
        temperature=0.4,
        company=company,
        role=role,
        jd_text=jd_text,
        cv_excerpt=_load_cv_excerpt(),
        research_context=research_context,
    )
    console.print(body)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(body, encoding="utf-8")
        console.print(f"\n[green]Wrote[/green] {output}")


@app.command("research")
def research_prompt(
    company: str = typer.Argument(..., help="Target company."),
    role: str = typer.Argument(..., help="Target role."),
    jd: str | None = typer.Option(
        None, "--jd", help="Path to JD file or pasted JD text. Optional."
    ),
    output: Path | None = typer.Option(
        None, help="Optional path to write the research prompt. Stdout always prints."
    ),
    max_tokens: int = typer.Option(2000, help="LLM max tokens."),
    with_search: bool = typer.Option(
        False,
        "--with-search",
        help=(
            "Run live Brave WebSearch queries and inject the snippets into "
            "the research prompt. No-op when web_search.provider is unset."
        ),
    ),
) -> None:
    """Generate a 6-axis deep-research prompt for Perplexity / Claude / ChatGPT."""
    jd_text = ""
    if jd:
        jd_path = Path(jd)
        jd_text = jd_path.read_text(encoding="utf-8") if jd_path.exists() else jd

    research_context = _build_research_context_for_prompt(
        company=company,
        role=role,
        enabled=with_search,
        purpose="research",
    )

    body = _run_one_shot_prompt(
        template="deep_research.md",
        node_name="deep_research",
        graph_name="deep_research_cli",
        max_tokens=max_tokens,
        temperature=0.3,
        company=company,
        role=role,
        jd_text=jd_text,
        cv_excerpt=_load_cv_excerpt(),
        research_context=research_context,
    )
    console.print(body)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(body, encoding="utf-8")
        console.print(f"\n[green]Wrote[/green] {output}")


@app.command("project-eval")
def project_eval(
    project_idea: str = typer.Argument(..., help="Portfolio project idea to evaluate."),
    role_context: str = typer.Option("", "--context", help="Optional target role/company context."),
    output: Path | None = typer.Option(
        None, help="Optional path to write the evaluation. Stdout always prints."
    ),
    max_tokens: int = typer.Option(1800, help="LLM max tokens."),
) -> None:
    """Evaluate whether a portfolio project is worth building."""
    body = _run_one_shot_prompt(
        template="project_eval.md",
        node_name="project_eval",
        graph_name="career_strategy_cli",
        max_tokens=max_tokens,
        temperature=0.25,
        project_idea=project_idea,
        role_context=role_context,
        cv_excerpt=_load_cv_excerpt(),
    )
    console.print(body)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(body, encoding="utf-8")
        console.print(f"\n[green]Wrote[/green] {output}")


@app.command("training-eval")
def training_eval(
    training_option: str = typer.Argument(..., help="Course, certificate, or training to evaluate."),
    role_context: str = typer.Option("", "--context", help="Optional target role/company context."),
    output: Path | None = typer.Option(
        None, help="Optional path to write the evaluation. Stdout always prints."
    ),
    max_tokens: int = typer.Option(1800, help="LLM max tokens."),
) -> None:
    """Evaluate whether a course or certification is worth the time."""
    body = _run_one_shot_prompt(
        template="training_eval.md",
        node_name="training_eval",
        graph_name="career_strategy_cli",
        max_tokens=max_tokens,
        temperature=0.25,
        training_option=training_option,
        role_context=role_context,
        cv_excerpt=_load_cv_excerpt(),
    )
    console.print(body)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(body, encoding="utf-8")
        console.print(f"\n[green]Wrote[/green] {output}")


@app.command("search-test")
def search_test(
    query: str = typer.Argument(..., help="Search query to send to the configured provider."),
    count: int | None = typer.Option(None, help="Override result count (default from settings)."),
    freshness: str | None = typer.Option(
        None, help="Override freshness: pd (past day) / pw (week) / pm (month) / py (year)."
    ),
) -> None:
    """Smoke-test the configured WebSearch provider (Brave by default)."""
    from job_hunt.services.web_search import build_web_search_provider

    settings = load_settings()
    provider = build_web_search_provider(settings)
    if provider is None:
        console.print(
            "[red]No web search provider configured.[/red]\n"
            "Set `web_search.provider: brave` in config/settings.yml and "
            "BRAVE_API_KEY in .env, then retry."
        )
        raise typer.Exit(1)

    hits = provider.search(query, count=count, freshness=freshness)
    if not hits:
        console.print("[yellow]No hits returned.[/yellow]")
        return

    table = Table("#", "Title", "URL", "Age")
    for i, hit in enumerate(hits, 1):
        table.add_row(str(i), hit.title[:60], hit.url[:70], hit.age or "")
    console.print(table)
    console.print(f"\n{len(hits)} result(s) for: {query!r}")


@app.command("search-usage")
def search_usage(
    month: str | None = typer.Option(
        None,
        help="UTC month in YYYY-MM. Defaults to the current month.",
    ),
) -> None:
    """Show WebSearch quota usage (api_calls / cache_hits / errors) for a month.

    Brave's free tier is ~2k queries/month — this command lets the operator
    eyeball the counter before kicking off a wide scan or batch evaluation.
    """
    from job_hunt.services.web_search import (
        CachingProvider,
        WebSearchCache,
        _resolve_cache_dir,
        build_web_search_provider,
    )

    settings = load_settings()
    provider = build_web_search_provider(settings)
    cache: WebSearchCache | None = None
    if isinstance(provider, CachingProvider):
        cache = provider.cache
    else:
        cache_dir = _resolve_cache_dir(settings, "brave")
        if cache_dir.exists():
            cache = WebSearchCache(
                cache_dir,
                ttl_seconds=settings.web_search.cache_ttl_seconds,
            )
    if cache is None:
        console.print(
            "[yellow]No cache directory found.[/yellow] "
            "Either WebSearch is disabled or no queries have run yet."
        )
        return

    usage = cache.usage(month=month)
    table = Table("Metric", "Count")
    table.add_row("API calls", str(usage.api_calls))
    table.add_row("Cache hits", str(usage.cache_hits))
    table.add_row("Errors / empty", str(usage.errors))
    table.add_row(
        "[bold]Total provider lookups[/bold]",
        f"[bold]{usage.api_calls + usage.cache_hits + usage.errors}[/bold]",
    )
    console.print(f"WebSearch usage for {usage.month} (UTC):")
    console.print(table)
    console.print(f"\nUsage file: {cache.usage_path()}")


@app.command("compare")
def compare_offers(
    tracker_ids: list[int] = typer.Argument(
        ..., help="Tracker entry numbers to compare (2 or more)."
    ),
    output: Path | None = typer.Option(
        None, help="Optional path to write the comparison markdown. Stdout always prints."
    ),
    max_tokens: int = typer.Option(2400, help="LLM max tokens for the comparison."),
) -> None:
    """Compare 2+ offers from the tracker on a 10-dimension weighted matrix."""
    if len(tracker_ids) < 2:
        console.print("[red]compare requires at least 2 tracker IDs.[/red]")
        raise typer.Exit(1)

    from job_hunt.services import compare_offers as svc

    offers, missing = svc.load_offers(tracker_ids)
    if missing:
        console.print(f"[yellow]Tracker IDs not found:[/yellow] {', '.join(missing)}")
    if len(offers) < 2:
        console.print("[red]Fewer than 2 offers resolved; aborting.[/red]")
        raise typer.Exit(1)

    prompt = svc.render_prompt(offers)
    settings = load_settings()
    provider = build_cheap_provider(settings)

    async def run() -> str:
        result = await traced_chat(
            provider,
            settings=settings,
            messages=[ChatMessage(role="user", content=prompt)],
            model=settings.llm.cheap.model,
            node_name="compare_offers",
            graph_name="compare_offers_cli",
            model_tier="cheap",
            temperature=0.2,
            max_tokens=max_tokens,
        )
        return result.content

    body = asyncio.run(run())
    console.print(body)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(body, encoding="utf-8")
        console.print(f"\n[green]Wrote[/green] {output}")


@app.command("scan")
def scan(
    company: str | None = typer.Option(None, help="Only scan matching company name."),
    limit_companies: int | None = typer.Option(None, help="Limit number of direct ATS companies."),
    include_non_canada: bool = typer.Option(False, help="Include jobs outside Canada in results."),
    apply: bool = typer.Option(False, help="Write new jobs to scan history and pipeline."),
    channel: str | None = typer.Option(
        None,
        "--channel",
        help=(
            "Restrict tier-3 discovery to one channel id "
            "(linkedin / indeed / glassdoor / waterlooworks / talentegg / ...)."
        ),
    ),
) -> None:
    from job_hunt.services.web_search import build_web_search_provider

    settings = load_settings()
    web_search_provider = build_web_search_provider(settings)
    if web_search_provider is None:
        console.print(
            "[dim]WebSearch tier disabled (set web_search.provider=brave + BRAVE_API_KEY "
            "to scan companies with scan_method: websearch and discovery channels).[/dim]"
        )
    result = scan_portals(
        company=company,
        limit_companies=limit_companies,
        include_non_canada=include_non_canada,
        apply=apply,
        web_search_provider=web_search_provider,
        discovery_channel=channel,
    )
    console.print(f"Scanned companies: {result.scanned_companies}")
    console.print(f"Fetched jobs: {result.fetched_jobs}")
    console.print(f"Matched jobs: {result.matched_jobs}")
    console.print(f"Skipped by filters: {result.skipped_filtered}")
    console.print(f"New jobs: {result.new_jobs}")
    console.print(f"Skipped duplicates: {result.skipped_duplicates}")
    for error in result.errors:
        console.print(f"[yellow]warning:[/yellow] {error}")
    table = Table("Company", "Title", "Location", "Portal", "URL")
    for job in result.jobs[:30]:
        table.add_row(job.company, job.title, job.location, job.portal, job.url)
    if result.jobs:
        console.print(table)
    if result.new_jobs > 30:
        console.print(f"... {result.new_jobs - 30} more new jobs")


@app.command("apply")
def apply_assist(
    url: str = typer.Argument(..., help="Application or job form URL to open."),
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
            "row with weighted_total < 4.0 aborts. Set this flag if you have a "
            "specific reason to apply anyway (e.g. learning experience, network signal)."
        ),
    ),
) -> None:
    """Assist with an application. By default never clicks the final submit button.

    ``--auto-submit`` opts in to one-click submission, but the click only fires
    when every safety gate passes (see flag help). Any gate failure falls back
    to the normal "user submits manually" flow without partial state.
    """
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

    _enforce_low_score_gate(report_context, override=low_score_override)

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
        result = asyncio.run(
            graph.ainvoke(
                {"input": url, "run_id": run_id, "thread_id": run_id},
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
    apply_ipc.submit_command_with_compat(
        art_dir,
        apply_ipc.COMMAND_TYPE_REPLACE_PDF,
        {"pdf": str(pdf.resolve())},
        compat_filename=".replace_pdf",
        compat_payload=str(pdf.resolve()),
    )
    console.print(f"Replace request sent → {art_dir.name}")
    console.print(f"New PDF: {pdf}")
    console.print("Browser will update in ~2 seconds and take a new screenshot.")


@app.command("apply-capture-page")
def apply_capture_page() -> None:
    """Capture the current page in a running --fill-only browser session."""
    art_dir = _active_apply_artifact_dir()
    apply_ipc.submit_command_with_compat(
        art_dir,
        apply_ipc.COMMAND_TYPE_CAPTURE_PAGE,
        compat_filename=".capture_page",
        compat_payload=datetime.now(timezone.utc).isoformat(),
    )
    console.print(f"Capture request sent → {art_dir.name}")
    console.print("Browser will capture the current page in ~2 seconds.")


@app.command("apply-refill-current-page")
def apply_refill_current_page() -> None:
    """Re-run auto-fill and PDF attachment on the current page of an active fill-only session."""
    art_dir = _active_apply_artifact_dir()
    apply_ipc.submit_command_with_compat(
        art_dir,
        apply_ipc.COMMAND_TYPE_REFILL_CURRENT_PAGE,
        compat_filename=".refill_current_page",
        compat_payload=datetime.now(timezone.utc).isoformat(),
    )
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
            report_full = report_path.read_text(encoding="utf-8")
            section_g = report_context.get("application_section") or ""
    if not report_full:
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
        console.print(result.content)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(result.content + "\n", encoding="utf-8")
            console.print(f"\n[green]Wrote answers to[/green] {output}")
        for error in result.errors:
            console.print(f"[yellow]warning:[/yellow] {error}")

    asyncio.run(run())


@app.command("apply-close-session")
def apply_close_session() -> None:
    """Gracefully close an active fill-only browser so login cookies/profile state are saved."""
    art_dir = _active_apply_artifact_dir()
    apply_ipc.submit_command_with_compat(
        art_dir,
        apply_ipc.COMMAND_TYPE_CLOSE_SESSION,
        compat_filename=".close_session",
        compat_payload=datetime.now(timezone.utc).isoformat(),
    )
    console.print(f"Graceful close request sent → {art_dir.name}")
    console.print("Browser will close after saving the persistent profile.")


def _active_apply_artifact_dir() -> Path:
    """Find the most-recent active session and warn if its heartbeat is stale.

    Phase 3.3: prefer ``.session.json`` heartbeat freshness; fall back to
    ``.cdp`` for sessions started by an older runner that doesn't write a
    heartbeat yet.
    """
    art_dir = apply_ipc.find_active_session_dir(Path("artifacts/apply"))
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


def _build_agent_apply_prompt(
    *,
    url: str,
    company: str | None,
    role: str | None,
    pdf: Path | None,
    tracker_id: int | None,
) -> str:
    parts = [".venv/bin/job-hunt", "apply", url]
    if tracker_id is not None:
        parts.extend(["--tracker-id", str(tracker_id)])
    if company:
        parts.extend(["--company", company])
    if role:
        parts.extend(["--role", role])
    if pdf:
        parts.extend(["--pdf", str(pdf)])
    base_command = " ".join(shlex.quote(part) for part in parts)
    fill_command = base_command + " --fill-only"
    record_command = base_command + " --no-browser --confirmed"
    smoke_command = "printf 'n\\n' | " + base_command + " --headless"
    replace_command = ".venv/bin/job-hunt apply-replace-pdf '<new-resume.pdf>'"
    capture_command = ".venv/bin/job-hunt apply-capture-page"

    return f"""# Agent Apply Runbook

You are operating the job-hunt application assistant from this repository.

Goal: open the application form, fill it with the candidate's real profile and selected PDF, let the user review and request edits, and only record the application after the user manually submits it.

Hard safety rules:
- Never click the final Submit/Apply button yourself.
- Do not invent candidate facts. Use `profile/profile.yml`, `profile/cv.md`, the selected PDF, and any matching report under `reports/`.
- If a required question cannot be answered truthfully, pause and ask the user.
- Do not expose secrets, cookies, OAuth tokens, or webhook URLs in the conversation.
- Do not record the application as Applied until the user explicitly confirms they clicked Submit.

Fill command, run this in the background so the browser stays open:

```bash
{fill_command}
```

Execution protocol:
1. Run `pwd` and confirm you are in the job-hunt repository.
2. Run `.venv/bin/job-hunt config doctor` if configuration looks stale.
3. Confirm the PDF exists if `--pdf` is present.
4. Run the fill command in visible browser mode.
5. Read the terminal output. It should list attached PDF, auto-filled fields, skipped fields, visible actions, artifact dir, and a review screenshot path.
6. Inspect the browser or the newest `apply-review-*.png` in the artifact dir. Summarize for the user:
   - company and role
   - fields filled
   - fields needing attention
   - PDF filename shown in the form
   - any risk, missing answer, or work-authorization question
7. Ask the user to review the visible browser. If the user requests edits, update the open form when possible; otherwise tell the user the exact field/value to change.
8. If the user asks to swap the PDF, run:

```bash
{replace_command}
```

9. Wait a few seconds, then inspect the newest screenshot to verify the replacement.
10. When the user says it is ready, instruct the user to manually click the final Submit/Apply button in the browser.
11. After the user confirms they submitted, capture the current confirmation page while the browser is still open:

```bash
{capture_command}
```

12. Wait a few seconds, then inspect the newest screenshot for a confirmation such as "Thank you for applying" or "application received".
13. Record the application:

```bash
{record_command}
```

14. Verify the terminal reports `Recorded Applied`. Then run:

```bash
.venv/bin/job-hunt activity list --since 1d
```

Optional headless smoke test, use only when you are not submitting:

```bash
{smoke_command}
```

Expected smoke behavior: it fills safe fields, captures a screenshot, answers `n`, and makes no tracker changes.
"""


def _infer_loop_target(*, url: str, description: str) -> dict:
    tracker = TrackerRepository(Path("data/applications.md"))
    metadata = _extract_loop_url_metadata(url)
    inferred_text = " ".join(
        part
        for part in [
            description,
            metadata.get("company", ""),
            metadata.get("title", ""),
            metadata.get("location", ""),
            metadata.get("text", "")[:1200],
        ]
        if part
    )
    company, role = _parse_company_role_from_description(description)
    company = company or metadata.get("company") or None
    role = role or metadata.get("title") or None
    entry = None
    score = 0.0
    if company:
        entry, score = tracker.find_match(company=company, role=role)
    if (not entry or score < 0.70) and inferred_text:
        entry, score = _best_tracker_text_match(tracker.parse(), inferred_text)
    if entry and score >= 0.55:
        company = company or entry.company
        role = role or entry.role
    if not company or not role:
        ats_company = _company_from_apply_url(url)
        if ats_company:
            company = company or ats_company
    if company and role:
        role = re.sub(rf"^{re.escape(company)}\s+", "", role, flags=re.IGNORECASE).strip() or role
    if entry and score >= 0.70:
        role = entry.role
    if entry:
        pdf = _select_pdf_for_entry(entry)
    else:
        pdf = _select_pdf_for_text(" ".join(part for part in [company or "", role or "", description] if part))
    return {
        "company": company,
        "role": role,
        "pdf": pdf,
        "tracker_entry": entry if entry and score >= 0.55 else None,
        "metadata": metadata,
    }


def _extract_loop_url_metadata(url: str) -> dict[str, str]:
    try:
        result = asyncio.run(extract_url_text(url, min_chars=50))
    except Exception:
        return {}
    return {
        "title": result.title.strip(),
        "company": result.company.strip(),
        "location": result.location.strip(),
        "ats": result.ats.strip(),
        "adapter": result.adapter,
        "text": result.text.strip(),
    }


def _parse_company_role_from_description(description: str) -> tuple[str | None, str | None]:
    text = " ".join(description.split())
    if not text:
        return None, None
    separators = [" — ", " - ", " at ", " @ ", " for "]
    for sep in separators:
        if sep in text:
            left, right = text.split(sep, 1)
            if sep.strip() in {"at", "for"}:
                return right.strip() or None, left.strip() or None
            return left.strip() or None, right.strip() or None
    return None, text


def _company_from_apply_url(url: str) -> str | None:
    patterns = [
        r"jobs\.ashbyhq\.com/([^/?#]+)",
        r"job-boards\.greenhouse\.io/([^/?#]+)",
        r"boards\.greenhouse\.io/([^/?#]+)",
        r"jobs\.lever\.co/([^/?#]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1).replace("-", " ").replace("_", " ").title()
    return None


def _best_tracker_text_match(entries: list, description: str):
    from rapidfuzz import fuzz

    best = None
    best_score = 0.0
    desc = description.lower()
    for entry in entries:
        haystack = f"{entry.company} {entry.role} {entry.notes}".lower()
        score = fuzz.token_set_ratio(desc, haystack) / 100
        if score > best_score:
            best_score = score
            best = entry
    return best, best_score


def _select_pdf_for_entry(entry) -> Path | None:
    text = f"{entry.company} {entry.role} {entry.report} {entry.notes}"
    return _select_pdf_for_text(text)


def _select_pdf_for_text(text: str) -> Path | None:
    pdfs = [path for path in Path("output").rglob("*.pdf") if path.is_file()]
    generic = Path("output/ai-engineer-resume-preview/yi-xin-ai-engineer-resume.pdf")
    if not pdfs:
        return generic if generic.exists() else None
    tokens = [token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) >= 3]
    best = None
    best_score = -1
    for pdf in pdfs:
        name = str(pdf).lower()
        score = sum(2 for token in tokens if token in name)
        if "resume" in name or name.startswith("cv"):
            score += 2
        if "candidate" in name:
            score -= 1
        if "cover" in name:
            score -= 4
        if "ai-engineer-resume-preview" in str(pdf):
            score += 1
        if score > best_score:
            best_score = score
            best = pdf
    if best and best_score > 0:
        return best
    return generic if generic.exists() else best


def _tracker_entry_blocks_apply(entry) -> bool:
    score_match = re.search(r"(\d+(?:\.\d+)?)\s*/\s*5", entry.score or "")
    if score_match and float(score_match.group(1)) < 3.0:
        return True
    return "skip" in f"{entry.notes} {entry.status}".lower()


def _loop_agent_apply_command(*, url: str, company: str | None, role: str | None, pdf: Path | None) -> str:
    parts = [".venv/bin/job-hunt", "agent-apply", url]
    if company:
        parts.extend(["--company", company])
    if role:
        parts.extend(["--role", role])
    if pdf:
        parts.extend(["--pdf", str(pdf)])
    return " ".join(shlex.quote(part) for part in parts)


_BROWSER_PROFILE = Path("storage/browser-profile")
_CDP_PORT = 9222


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
        await _maybe_workday_login(page, artifact_dir=art_dir)
        await _advance_application_start(page)
        await _recover_workday_error_page(page, url)
        await _wait_for_application_ready(page)
        title = await page.title()
        role_warnings = await _page_identity_warnings(page, company=company, role=role)
        role_warnings.extend(_report_fit_warnings(report_context))
        file_inputs = page.locator("input[type=file]")
        file_count = await file_inputs.count()

        # Auto-fill text fields first so React components finish mounting,
        # then attach the PDF so file upload state is set on a stable form.
        filled: list[str] = []
        skipped: list[str] = []
        answers: list[dict[str, str]] = []
        if auto_fill:
            filled, skipped, answers = await _auto_fill_application(
                page,
                company=company,
                role=role,
                report_context=report_context,
            )

        attached = False
        if pdf and "myworkdayjobs.com" not in page.url:
            attached = await _attach_resume(page, pdf)
            if attached:
                await page.wait_for_timeout(2000)
                if not (art_dir / pdf.name).exists():
                    shutil.copy2(pdf, art_dir / pdf.name)

        cover_letter_attached = False
        if cover_letter_pdf and "myworkdayjobs.com" not in page.url:
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
        validation_issues = await _collect_workday_review_issues(page) if "myworkdayjobs.com" in page.url else []

        # --- Auto-submit (Phase 4 — gated) ----------------------------------
        # Only fires when ALL of the following are true:
        #  - caller passed auto_submit=True (CLI flag + profile.yml gate already
        #    AND-ed by apply_assist before we got here)
        #  - URL is a Workday host (the only ATS where we have a structured
        #    Review gate; other sites stay manual until they have one too)
        #  - validation_issues is empty (Review-gate clean)
        #  - required_empty is empty (no required field still missing)
        # When any gate fails we leave the page exactly as-is for manual review.
        auto_submit_clicked = False
        if auto_submit:
            workday_host = "myworkdayjobs.com" in page.url
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

        screenshot = art_dir / f"apply-review-{uuid.uuid4().hex[:8]}.png"
        await page.screenshot(path=str(screenshot), full_page=True)
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
            apply_ipc.write_heartbeat(art_dir, started_at=session_started)
            apply_run_log.emit(
                art_dir, "session.started", url=url, company=company, role=role,
                pdf=str(pdf) if pdf else None,
            )
            console.print(f"\n[yellow]Browser open — review and submit manually.[/yellow]")
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
                    apply_ipc.write_heartbeat(art_dir, started_at=session_started)
                    last_heartbeat_at = now
                await asyncio.sleep(2)

                # Phase 3.3: drain the v2 ``.cmd-*.json`` queue first (race-free),
                # then fall through to the compatibility sentinels for backward compat
                # with old subcommands started against a freshly-upgraded loop.
                pending = apply_ipc.consume_pending_commands(art_dir)
                # Map compatibility sentinels to the same Command shape so the rest of the
                # body has one code path.
                replace_compat = art_dir / ".replace_pdf"
                if replace_compat.exists():
                    payload = {"pdf": replace_compat.read_text().strip()}
                    replace_compat.unlink(missing_ok=True)
                    pending.append(apply_ipc.Command(
                        id="compat", kind=apply_ipc.COMMAND_TYPE_REPLACE_PDF,
                        payload=payload, sent_at=0.0,
                    ))
                capture_compat = art_dir / ".capture_page"
                if capture_compat.exists():
                    capture_compat.unlink(missing_ok=True)
                    pending.append(apply_ipc.Command(
                        id="compat", kind=apply_ipc.COMMAND_TYPE_CAPTURE_PAGE,
                        payload={}, sent_at=0.0,
                    ))
                refill_compat = art_dir / ".refill_current_page"
                if refill_compat.exists():
                    refill_compat.unlink(missing_ok=True)
                    pending.append(apply_ipc.Command(
                        id="compat", kind=apply_ipc.COMMAND_TYPE_REFILL_CURRENT_PAGE,
                        payload={}, sent_at=0.0,
                    ))
                close_compat = art_dir / ".close_session"
                if close_compat.exists():
                    close_compat.unlink(missing_ok=True)
                    pending.append(apply_ipc.Command(
                        id="compat", kind=apply_ipc.COMMAND_TYPE_CLOSE_SESSION,
                        payload={}, sent_at=0.0,
                    ))

                for cmd in pending:
                    last_activity_at = asyncio.get_event_loop().time()
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
                            last_screenshot = art_dir / f"apply-review-{uuid.uuid4().hex[:8]}.png"
                            await page.screenshot(path=str(last_screenshot), full_page=True)
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
                        last_screenshot = art_dir / f"apply-page-{uuid.uuid4().hex[:8]}.png"
                        await page.screenshot(path=str(last_screenshot), full_page=True)
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
    if pdf and "myworkdayjobs.com" not in page.url:
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
        if "myworkdayjobs.com" in page.url else []
    )
    labels = await page.locator("button, a[role=button], input[type=submit]").all_inner_texts()
    actions = [_short(label.strip(), 80) for label in labels if label.strip()]
    last_screenshot = art_dir / f"apply-review-{uuid.uuid4().hex[:8]}.png"
    await page.screenshot(path=str(last_screenshot), full_page=True)
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


async def _enter_application_form(page) -> None:
    """Navigate from a JD landing page into the editable application form.

    Three strategies, in order:

    1. **URL already on the form** — `/application` / `/apply` suffix → return.
    2. **Workday `adventureButton`** — direct link to the apply route.
    3. **Ashby `/application` suffix** — Ashby renders the form as a sibling
       tab whose URL is just the JD URL with `/application` appended. When
       a JD URL like `jobs.ashbyhq.com/<co>/<uuid>` is opened, the apply
       form is one nav away — go there directly instead of relying on the
       operator to click the tab.
    4. **Generic "Apply" button/link/tab** — last-resort click on a visible
       control whose accessible name matches "Apply for this Job" or
       "Application". Covers ATS templates we haven't profiled yet.

    Each strategy is best-effort; failures fall through to the next one
    rather than raising. The fill loop downstream re-detects form fields
    after this returns and reports zero filled if none were found, so a
    completely missed navigation is visible in the apply-review artifact
    rather than silently broken.
    """
    if page.url.rstrip("/").endswith(("/application", "/apply")):
        return
    workday_apply = page.locator('a[data-automation-id="adventureButton"][href$="/apply"]').first
    try:
        await workday_apply.wait_for(timeout=8000)
    except Exception:
        pass
    if await workday_apply.count():
        href = await workday_apply.get_attribute("href")
        if href:
            await page.goto(href, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(1500)
            return

    # Ashby — JD URL + `/application` is the form route. Confirmed against
    # https://jobs.ashbyhq.com/<company>/<uuid> 2026-05-11.
    if "jobs.ashbyhq.com" in page.url:
        base = page.url.split("?")[0].rstrip("/")
        if not base.endswith(("/application", "/apply")):
            target = f"{base}/application"
            try:
                await page.goto(target, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(1500)
                return
            except Exception:
                # Fall through to the generic button click below.
                pass

    # Generic fallback: click a visible "Apply" / "Application" control.
    # Limited to exact-match labels so we don't click random "Apply with
    # LinkedIn" / "Apply with Indeed" deep-link buttons.
    apply_label = re.compile(
        r"^\s*(Apply for this Job|Application|Apply Now)\s*$", re.IGNORECASE
    )
    candidates = [
        page.get_by_role("tab", name=apply_label),
        page.get_by_role("link", name=apply_label),
        page.get_by_role("button", name=apply_label),
    ]
    for control in candidates:
        try:
            if not await control.count():
                continue
            target = control.first
            await target.click(timeout=5000, force=True)
            await page.wait_for_timeout(1500)
            return
        except Exception:
            continue


async def _advance_application_start(page) -> None:
    """Move past non-final ATS start screens into the editable application form."""
    start_label = re.compile(r"^\s*(Apply Manually|Continue Application)\s*$", re.IGNORECASE)
    controls = [
        page.get_by_role("button", name=start_label),
        page.get_by_role("link", name=start_label),
        page.locator("button, a, [role=button]").filter(has_text=start_label),
    ]
    for control in controls:
        try:
            await control.first.wait_for(timeout=5000)
        except Exception:
            pass
        if not await control.count():
            continue
        before_url = page.url
        first = control.first
        href = await first.get_attribute("href")
        await first.click(timeout=10000, force=True)
        try:
            await page.wait_for_url(lambda current: current != before_url, timeout=8000)
        except Exception:
            if href:
                await page.goto(href, wait_until="domcontentloaded", timeout=45000)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        await page.wait_for_timeout(2000)
        return


# Login + diagnostic dump live in `services.workday.login`. This wrapper keeps
# the long historical call-site signature stable while delegating the actual
# orchestration. The body below the early returns was moved verbatim into the
# module; see ADR-011 §3.5 for the design.
async def _maybe_workday_login(page, *, artifact_dir: Path | None = None) -> None:
    from job_hunt.services.workday.login import maybe_login

    values = _apply_profile_values()
    return await maybe_login(
        page,
        email=values.get("email", ""),
        artifact_dir=artifact_dir,
        fallback_fill=_fill_by_label_or_placeholder,
        warn=lambda msg: console.print(f"[yellow]{msg}[/yellow]"),
    )


async def _recover_workday_error_page(page, original_url: str) -> None:
    if "myworkdayjobs.com" not in page.url:
        return
    try:
        text = await page.locator("body").inner_text(timeout=5000)
    except Exception:
        return
    if "Something went wrong" not in text:
        return

    for attempt in range(2):
        try:
            if attempt == 0:
                await page.reload(wait_until="domcontentloaded", timeout=45000)
            else:
                await page.goto(original_url, wait_until="domcontentloaded", timeout=45000)
                await _enter_application_form(page)
                await _advance_application_start(page)
            await page.wait_for_timeout(5000)
            text = await page.locator("body").inner_text(timeout=5000)
            if "Something went wrong" not in text:
                return
        except Exception:
            continue

    apply_label = re.compile(r"^\s*(Apply for this Job|Apply|申请)\s*$", re.IGNORECASE)
    candidates = [
        page.get_by_role("link", name=apply_label),
        page.get_by_role("button", name=apply_label),
        page.locator("button, a, [role=button]").filter(has_text=apply_label),
    ]
    for button in candidates:
        if await button.count():
            before_url = page.url
            first = button.first
            href = await first.get_attribute("href")
            await first.click(timeout=10000)
            try:
                await page.wait_for_url(lambda current: current != before_url, timeout=8000)
            except Exception:
                if href:
                    await page.goto(href, wait_until="domcontentloaded", timeout=45000)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            await page.wait_for_timeout(1500)
            return


async def _try_workday_final_submit(page) -> bool:
    """Attempt to click Workday's final ``Submit`` button on the Review step.

    Workday wraps the visible Submit control in an overlay
    ``role="button" aria-label="Submit"`` div on top of a real ``<button>``;
    clicking the underlying button gets pointer-event-intercepted, so we
    explicitly target the overlay first and fall back to the role-based
    Playwright lookup. Returns True only when a click was actually dispatched.
    """
    if "myworkdayjobs.com" not in page.url:
        return False
    try:
        clicked = await page.evaluate(
            """() => {
                const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                const norm = text => (text || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const candidates = Array.from(document.querySelectorAll(
                    '[role="button"], button, input[type="submit"]'
                )).filter(visible).filter(el => {
                    const label = norm(
                        el.getAttribute('aria-label') || el.innerText || el.value || ''
                    );
                    return label === 'submit' || label === 'submit application';
                });
                if (!candidates.length) return false;
                const target = candidates[0];
                target.scrollIntoView({block: 'center'});
                target.click();
                return true;
            }"""
        )
        if clicked:
            return True
    except Exception:
        pass
    # Fallback: Playwright role lookup.
    try:
        button = page.get_by_role(
            "button", name=re.compile(r"^\s*submit( application)?\s*$", re.I)
        ).first
        if await button.count():
            await button.click(timeout=10000, force=True)
            return True
    except Exception:
        return False
    return False


async def _workday_resume_was_uploaded(page, pdf: Path) -> bool:
    """Return True when the Workday page surfaces ``pdf.name`` (a successful upload).

    Workday performs the resume upload inside the My Experience step, not via
    the earlier `_attach_resume` call in ``_open_apply_page``. The original
    ``attached`` flag therefore stays False on Workday flows, which made
    ``apply-review.json["pdf"]`` claim "not attached" even after a successful
    upload. Detecting the filename in the page text (Review card / "Successfully
    Uploaded" notice / My Experience attachment list) is the most stable
    cross-locale signal Workday provides.
    """
    if "myworkdayjobs.com" not in page.url:
        return False
    try:
        text = await page.locator("body").inner_text(timeout=3000)
    except Exception:
        return False
    return pdf.name in text


async def _wait_for_application_ready(page) -> None:
    candidates = [
        page.get_by_text("Name", exact=True),
        page.get_by_label("First Name", exact=False),
        page.get_by_label("Email", exact=False),
        page.locator('input:not([type="hidden"]):not([type="file"]), textarea, select').first,
    ]
    for locator in candidates:
        try:
            await locator.wait_for(timeout=5000)
            return
        except Exception:
            continue


async def _attach_resume(page, pdf: Path) -> bool:
    try:
        text = await page.locator("body").inner_text(timeout=3000)
        if pdf.name in text or ("myworkdayjobs.com" in page.url and "Successfully Uploaded" in text and "Resume/CV" in text):
            return True
    except Exception:
        pass

    # Strategy 1: click the exact-name upload button to trigger a native file
    # chooser — React components update correctly through this path.
    # Ashby uses "Upload File" (capital F) for Resume, "Upload file" (lower) for autofill.
    for btn_name in ["Upload File", "Attach file", "Select files", "Replace"]:
        btn = page.get_by_role("button", name=btn_name, exact=True)
        if not await btn.count():
            continue
        try:
            async with page.expect_file_chooser(timeout=4000) as fc_info:
                await btn.first.click(timeout=4000)
            fc = await fc_info.value
            await fc.set_files(str(pdf))
            await _finish_pending_upload_dialog(page)
            return True
        except Exception:
            pass

    # Airtable forms render file uploads as a custom drop zone with a small
    # "browse" link instead of a visible file input.
    for upload_trigger in [
        page.get_by_role("link", name="browse", exact=True),
        page.get_by_text("browse", exact=True),
        page.get_by_text("Select files", exact=True),
        page.get_by_text("Drop files here", exact=False),
    ]:
        try:
            if not await upload_trigger.count():
                continue
            async with page.expect_file_chooser(timeout=4000) as fc_info:
                await upload_trigger.first.click(timeout=4000)
            fc = await fc_info.value
            await fc.set_files(str(pdf))
            await _finish_pending_upload_dialog(page)
            return True
        except Exception:
            continue

    # Strategy 2: set_input_files on the first usable file input (works on non-Ashby forms
    # and on Ashby when the form is fully initialized post-autofill).
    # Break after the first success to avoid uploading to multiple hidden inputs.
    file_inputs = page.locator("input[type=file]")
    count = await file_inputs.count()
    attached = False
    for index in range(count):
        try:
            await file_inputs.nth(index).set_input_files(str(pdf))
            await _finish_pending_upload_dialog(page)
            attached = True
            break
        except Exception:
            continue
    return attached


_COVER_LETTER_LABEL_RE = re.compile(r"cover\s*letter", re.IGNORECASE)


async def _attach_cover_letter(page, cover_letter_pdf: Path) -> bool:
    """Attach a cover-letter PDF to a file input that is unambiguously labeled cover letter.

    Conservative on purpose: only matches inputs whose own label / aria-label / nearby
    text contains "cover letter". A generic resume input is left alone so a cover-letter
    PDF never overwrites the resume slot.
    """
    try:
        text = await page.locator("body").inner_text(timeout=3000)
        if cover_letter_pdf.name in text:
            return True
    except Exception:
        pass

    try:
        labeled = page.get_by_label(_COVER_LETTER_LABEL_RE)
        count = await labeled.count()
        for index in range(count):
            handle = labeled.nth(index)
            try:
                tag = (await handle.evaluate("el => el.tagName") or "").lower()
            except Exception:
                tag = ""
            if tag != "input":
                continue
            try:
                input_type = (await handle.get_attribute("type") or "").lower()
            except Exception:
                input_type = ""
            if input_type != "file":
                continue
            try:
                await handle.set_input_files(str(cover_letter_pdf))
                await _finish_pending_upload_dialog(page)
                return True
            except Exception:
                continue
    except Exception:
        pass

    for trigger in [
        page.get_by_role("button", name=_COVER_LETTER_LABEL_RE),
        page.get_by_role("link", name=_COVER_LETTER_LABEL_RE),
    ]:
        try:
            if not await trigger.count():
                continue
            async with page.expect_file_chooser(timeout=4000) as fc_info:
                await trigger.first.click(timeout=4000)
            fc = await fc_info.value
            await fc.set_files(str(cover_letter_pdf))
            await _finish_pending_upload_dialog(page)
            return True
        except Exception:
            continue

    return False


async def _finish_pending_upload_dialog(page) -> None:
    await page.wait_for_timeout(800)
    try:
        upload = page.get_by_role("button", name=re.compile(r"^Upload \d+ file", re.IGNORECASE))
        if await upload.count():
            await upload.first.click(timeout=5000)
            await page.wait_for_timeout(2000)
            return
    except Exception:
        pass
    await page.wait_for_timeout(700)


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


async def _fill_workday_phone_code(page, search_term: str = "Canada") -> bool:
    """Fill Workday Country Phone Code by removing any auto-filled chip then re-selecting.

    Workday profile auto-fill may pre-populate a chip visually, but that chip is often
    not "confirmed" in React's controlled state, causing form validation to reject it.
    Removing the chip and re-selecting forces Workday to register a fresh selection event.
    """
    try:
        # Step 1: Remove any existing phone code chips (click all × buttons near phone section).
        # Use JavaScript to find and click × buttons scoped to the Country Phone Code section.
        await page.evaluate(
            """() => {
                const normalize = t => (t || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                // Find the Country Phone Code label
                const labels = Array.from(document.querySelectorAll('label, div, span, p'))
                    .filter(el => visible(el) && normalize(el.innerText) === 'country phone code');
                for (const lbl of labels) {
                    let scope = lbl.parentElement;
                    for (let d = 0; scope && d < 5; d++, scope = scope.parentElement) {
                        // Click any × / remove buttons inside this section
                        const removeBtns = Array.from(scope.querySelectorAll('button'))
                            .filter(b => visible(b) && /^[×✕✗x]$/i.test(normalize(b.innerText)));
                        removeBtns.forEach(b => b.click());
                        if (removeBtns.length) break;
                    }
                }
            }"""
        )
        await page.wait_for_timeout(500)

        # Step 2: Find the text input inside the Country Phone Code combobox and type to search.
        # Workday's chip combobox has an underlying <input> for text entry.
        input_found = bool(
            await page.evaluate(
                """(searchTerm) => {
                    const normalize = t => (t || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    const labels = Array.from(document.querySelectorAll('label, div, span, p'))
                        .filter(el => visible(el) && normalize(el.innerText) === 'country phone code');
                    for (const lbl of labels) {
                        let scope = lbl.parentElement;
                        for (let d = 0; scope && d < 5; d++, scope = scope.parentElement) {
                            const inputs = Array.from(scope.querySelectorAll('input:not([type=hidden])'))
                                .filter(visible);
                            for (const inp of inputs) {
                                inp.focus();
                                // Use React's native setter to trigger onChange
                                const setter = Object.getOwnPropertyDescriptor(
                                    window.HTMLInputElement.prototype, 'value').set;
                                if (setter) setter.call(inp, searchTerm);
                                inp.dispatchEvent(new Event('input', {bubbles: true}));
                                inp.dispatchEvent(new Event('change', {bubbles: true}));
                                return true;
                            }
                        }
                    }
                    return false;
                }""",
                search_term,
            )
        )
        await page.wait_for_timeout(1200)

        # Step 3: Click the matching option from autocomplete.
        for option_text in [f"{search_term} (+1)", "Canada (+1)", search_term]:
            pat = re.compile(re.escape(option_text), re.IGNORECASE)
            for locator in [
                page.get_by_role("option", name=pat),
                page.get_by_role("menuitem", name=pat),
                page.locator('[role="option"], [role="menuitem"], li').filter(has_text=pat),
            ]:
                if await locator.count():
                    await locator.first.click(timeout=4000)
                    await page.wait_for_timeout(500)
                    return True

        # Fallback: keyboard navigation (ArrowDown + Enter)
        if input_found:
            await page.keyboard.press("ArrowDown")
            await page.wait_for_timeout(300)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(500)
            return True

        # Last resort: dropdown-button approach
        return await _select_workday_dropdown_by_label(page, "Country Phone Code", ["Canada (+1)", "Canada", "+1"])
    except Exception:
        return False


async def _fill_workday_current_step(
    page,
    values: dict[str, str],
    *,
    company: str | None = None,
    role: str | None = None,
    report_context: dict | None = None,
) -> tuple[list[str], list[str], list[dict[str, str]]]:
    """Fill safe fields on Workday multi-step application pages.

    Workday uses custom combobox/listbox controls that are not covered by the
    generic label filler. This adapter intentionally avoids final submission and
    avoids legal, sponsorship, demographic, and eligibility answers.

    The third returned tuple element is a list of free-form Q&A pairs filled on
    the Application Questions step (when this filler happens to be invoked on
    that page). Callers should merge it into the master ``answers`` list.
    """
    if "myworkdayjobs.com" not in page.url:
        return [], [], []
    try:
        text = await page.locator("body").inner_text(timeout=3000)
    except Exception:
        return [], [], []
    current_step = await _workday_current_step(page)

    filled: list[str] = []
    skipped: list[str] = []
    answers: list[dict[str, str]] = []

    if current_step == "My Information":
        for label, value in [
            ("First Name", values["first_name"]),
            ("Last Name", values["last_name"]),
            ("Address Line 1", values["address"]),
            ("City", values["city"]),
            ("Postal Code", values["postal_code"]),
            ("Phone Number", values["phone"]),
        ]:
            if await _fill_by_label_or_placeholder(page, label, value) or await _fill_workday_field_containing(page, label, value, force=True):
                filled.append(f"Workday {label}")

        for label, choices in [
            ("How Did You Hear About Us?", [values["source"], "Company Website", "Website", "Careers Site", "LinkedIn", "Other"]),
            ("Country", [values["country"], "Canada"]),
            ("Province or Territory", [values["province"], "Ontario"]),
        ]:
            if await _select_workday_dropdown_by_label(page, label, choices):
                filled.append(f"Workday {label}")
        if await _select_workday_dropdown_by_label(page, "Phone Device Type", [values["phone_device_type"], "Mobile", "Cell", "Home"], force=True):
            filled.append("Workday Phone Device Type")

        # Press Escape BEFORE Country Phone Code to close any lingering dropdown.
        # Pressing Escape AFTER phone code selection may dismiss the chip.
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
        except Exception:
            pass

        # Country Phone Code: force=True bypasses 'already-selected' check so we always
        # re-open the dropdown and re-select, properly committing to Workday's React state.
        # Profile auto-fill creates a visual chip that fails validation until explicitly re-selected.
        if await _select_workday_dropdown_by_label(page, "Country Phone Code", ["Canada (+1)", "Canada", "+1"], force=True):
            filled.append("Workday Country Phone Code")
            # Workday may cascade-clear address fields after phone code re-selection.
            # Re-fill City (and re-select Province) to restore values that may have been reset.
            await page.wait_for_timeout(800)
            if await _fill_by_label_or_placeholder(page, "City", values["city"]):
                filled.append("Workday City (post-phone-code)")
            await _select_workday_dropdown_by_label(page, "Province or Territory", [values["province"], "Ontario"])
        required = await _required_empty_fields(page)
        blockers = [item for item in required if _workday_required_blocks_my_information_continue(item)]
        if blockers:
            skipped.extend(_short(f"Workday required: {item}", 120) for item in blockers)
        elif await _click_workday_save_and_continue(page):
            filled.append("Workday Save and Continue")

    elif current_step == "My Experience":
        # Workday commonly has a resume upload section on this step. Actual PDF
        # attachment is handled by _attach_resume after auto-fill returns.
        if await _fill_workday_social_network_url(page, values["linkedin"]):
            filled.append("Workday Social Network URLs")

    elif current_step == "Application Questions":
        question_filled, question_skipped, question_answers = await _fill_workday_application_questions(
            page, values, company=company, role=role, report_context=report_context
        )
        filled.extend(question_filled)
        skipped.extend(question_skipped)
        answers.extend(question_answers)

    elif current_step == "Voluntary Disclosures":
        skipped.append("Workday Voluntary Disclosures require user review.")

    return filled, skipped, answers


async def _workday_current_step(page) -> str:
    try:
        headings = await page.locator("h1, h2, h3").all_inner_texts()
    except Exception:
        headings = []
    known = ["My Information", "My Experience", "Application Questions", "Voluntary Disclosures", "Review", "Create Account"]
    for heading in headings:
        normalized = re.sub(r"\s+", " ", heading).strip()
        for step in known:
            if normalized == step:
                return step
    try:
        text = await page.locator("body").inner_text(timeout=2000)
    except Exception:
        return ""
    for step in known:
        if re.search(rf"(?:^|\n){re.escape(step)}(?:\n|$)", text):
            return step
    return ""


def _workday_required_blocks_my_information_continue(label: str) -> bool:
    """Back-compat wrapper. Logic lives in services.workday.my_information."""
    from job_hunt.services.workday.my_information import (
        required_blocks_my_information_continue,
    )

    return required_blocks_my_information_continue(label)


async def _select_workday_dropdown_by_label(page, label: str, choices: list[str], force: bool = False) -> bool:
    """Select a Workday dropdown by its exact label text.

    force=True skips the 'already-selected' check and always re-opens the dropdown to
    re-select. Use for Country Phone Code where the profile auto-fill chip is visually
    present but not committed to React's validation state until explicitly re-selected.
    """
    choices = [choice for choice in choices if choice]
    if not choices:
        return False
    try:
        result = await page.evaluate(
                """({label, choices, force}) => {
                    const normalize = text => (text || '').replace(/\\*/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    const wanted = normalize(label);
                    const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    const labels = Array.from(document.querySelectorAll('label, div, span'))
                        .filter(el => visible(el) && normalize(el.innerText) === wanted);
                    for (const node of labels) {
                        let scope = node.parentElement;
                        for (let depth = 0; scope && depth < 5; depth++, scope = scope.parentElement) {
                            const button = scope.querySelector('button[aria-haspopup="listbox"], button[aria-haspopup="true"], [role="combobox"], button');
                            if (!force) {
                                // Only trust the actual control/chip text. Some Workday labels
                                // include option words like "Other", which must not count as selected.
                                const buttonText = normalize(button?.innerText || button?.getAttribute('aria-label') || button?.value || '');
                                const nonDefault = buttonText && buttonText !== 'select one' && buttonText !== 'select...' && buttonText !== '';
                                if (nonDefault && choices.some(choice => buttonText.includes(normalize(choice)))) return 'already-selected';
                                const chipEls = Array.from(scope.querySelectorAll('[data-automation-id*="chip"], [class*="Chip"], [class*="chip"], [class*="Tag"]'));
                                const chipText = chipEls.map(chip => normalize(chip.innerText)).join(' ');
                                if (chipText && choices.some(choice => chipText.includes(normalize(choice)))) return 'already-selected';
                            }
                            if (button && visible(button) && !button.disabled) {
                                button.scrollIntoView({block: 'center'});
                                button.click();
                                return 'clicked';
                            }
                        }
                    }
                    return '';
                }""",
                {"label": label, "choices": choices, "force": force},
            )
        if not result:
            return False
        if result == "already-selected":
            return True
        return await _choose_workday_option(page, choices)
    except Exception:
        return False


async def _select_workday_dropdown_containing_label(page, label_fragment: str, choices: list[str]) -> bool:
    """Like _select_workday_dropdown_by_label but matches if the label text CONTAINS label_fragment.

    Useful for long Workday question texts where the exact normalized string is hard to reproduce.
    """
    choices = [choice for choice in choices if choice]
    if not choices:
        return False
    try:
        result = await page.evaluate(
                """({fragment, choices}) => {
                    const normalize = text => (text || '').replace(/\\*/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    const wanted = normalize(fragment);
                    const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    const nodes = Array.from(document.querySelectorAll('label, p, div, span, li'))
                        .filter(el => visible(el) && normalize(el.innerText).includes(wanted));
                    for (const node of nodes) {
                        let scope = node.parentElement;
                        for (let depth = 0; scope && depth < 6; depth++, scope = scope.parentElement) {
                            const btn = scope.querySelector('button[aria-haspopup="listbox"], button[aria-haspopup="true"], [role="combobox"], button');
                            if (btn && visible(btn) && !btn.disabled) {
                                // Check button's own displayed text (works for simple dropdowns)
                                const btnText = normalize(btn.innerText);
                                const nonDefault = btnText && btnText !== 'select one' && btnText !== 'select...' && btnText !== '';
                                if (nonDefault && choices.some(c => btnText.includes(normalize(c)))) return 'already-selected';
                                // Check chips/tags for multi-select controls
                                const chipEls = Array.from(scope.querySelectorAll('[data-automation-id*="chip"], [class*="Chip"], [class*="chip"], [class*="Tag"]'));
                                const chipText = chipEls.map(c => normalize(c.innerText)).join(' ');
                                if (chipText && choices.some(c => chipText.includes(normalize(c)))) return 'already-selected';
                                btn.scrollIntoView({block: 'center'});
                                btn.click();
                                return 'clicked';
                            }
                        }
                    }
                    return '';
                }""",
                {"fragment": label_fragment, "choices": choices},
            )
        if not result:
            return False
        if result == "already-selected":
            return True
        return await _choose_workday_option(page, choices)
    except Exception:
        return False


async def _select_workday_dropdown_in_question(page, label_fragment: str, choices: list[str]) -> bool:
    """Select the dropdown inside a Workday question block containing label_fragment."""
    choices = [choice for choice in choices if choice]
    if not choices:
        return False
    try:
        result = await page.evaluate(
            """({fragment, choices}) => {
                const wanted = (fragment || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                const norm = text => (text || '').replace(/\\*/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const normalizedChoices = choices.map(norm);
                const isDropdownControl = el => {
                    if (!visible(el) || el.disabled) return false;
                    const aria = norm(el.getAttribute('aria-haspopup') || '');
                    const role = norm(el.getAttribute('role') || '');
                    const text = norm(el.innerText || el.getAttribute('aria-label') || el.value || '');
                    if (role === 'combobox' || aria === 'listbox' || aria === 'true') return true;
                    if (text === 'select one' || text === 'select...' || text === '') return true;
                    return normalizedChoices.some(choice => choice && text.includes(choice));
                };
                const nodes = Array.from(document.querySelectorAll('label, p, div, span'))
                    .filter(el => {
                        if (!visible(el)) return false;
                        const text = norm(el.innerText);
                        if (!text.includes(wanted)) return false;
                        if (text.startsWith('error -') || text.startsWith('the field ')) return false;
                        if (el.closest('[data-automation-id="errorSummary"], [role="alert"]')) return false;
                        return text.length <= 900;
                    })
                    .sort((a, b) => {
                        const ar = a.getBoundingClientRect();
                        const br = b.getBoundingClientRect();
                        return (ar.top - br.top) || (norm(a.innerText).length - norm(b.innerText).length);
                    });
                for (const node of nodes) {
                    const nodeRect = node.getBoundingClientRect();
                    const belowControls = Array.from(document.querySelectorAll('[role="button"], button[aria-haspopup], [role="combobox"], button'))
                        .filter(isDropdownControl)
                        .map(btn => ({
                            btn,
                            rect: btn.getBoundingClientRect(),
                            text: norm(btn.innerText || btn.getAttribute('aria-label') || btn.value || ''),
                        }))
                        .filter(item => item.rect.top >= nodeRect.bottom - 12 && item.rect.top - nodeRect.bottom < 420)
                        .sort((a, b) => (a.rect.top - b.rect.top) || (a.rect.left - b.rect.left));
                    if (belowControls.length) {
                        const first = belowControls[0];
                        if (normalizedChoices.some(choice => choice && first.text.includes(choice))) return 'already-selected';
                        first.btn.scrollIntoView({block: 'center'});
                        first.btn.click();
                        return 'clicked';
                    }
                    let scope = node.parentElement;
                    for (let depth = 0; scope && depth < 7; depth++, scope = scope.parentElement) {
                        const scopeText = norm(scope.innerText);
                        if (!scopeText.includes(wanted)) continue;
                        const controls = Array.from(scope.querySelectorAll('[role="button"], button[aria-haspopup], [role="combobox"], button'))
                            .filter(isDropdownControl)
                            .map(btn => ({btn, text: norm(btn.innerText || btn.getAttribute('aria-label') || btn.value || '')}));
                        const selected = controls.find(item => normalizedChoices.some(choice => choice && item.text.includes(choice)));
                        if (selected) return 'already-selected';
                        const dropdown = controls.find(item => item.text === 'select one' || item.text === 'select...' || item.text === '')?.btn || controls[0]?.btn;
                        if (dropdown) {
                            dropdown.scrollIntoView({block: 'center'});
                            dropdown.click();
                            return 'clicked';
                        }
                    }
                }
                return '';
            }""",
            {"fragment": label_fragment, "choices": choices},
        )
        if result == "already-selected":
            return True
        if result != "clicked":
            return False
        return await _choose_workday_option(page, choices)
    except Exception:
        return False


async def _fill_workday_textarea_containing_label(page, label_fragment: str, value: str) -> bool:
    """Fill the first textarea whose nearby label CONTAINS label_fragment (partial match)."""
    if not value:
        return False
    try:
        return bool(
            await page.evaluate(
                """({fragment, value}) => {
                    const normalize = text => (text || '').replace(/\\*/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    const wanted = normalize(fragment);
                    const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    const setVal = (el, val) => {
                        const proto = window.HTMLTextAreaElement.prototype;
                        const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                        if (setter) setter.call(el, val); else el.value = val;
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        el.dispatchEvent(new Event('blur', {bubbles: true}));
                    };
                    const areas = Array.from(document.querySelectorAll('textarea')).filter(visible);
                    for (const area of areas) {
                        let scope = area.parentElement;
                        for (let depth = 0; scope && depth < 8; depth++, scope = scope.parentElement) {
                            if (normalize(scope.innerText).includes(wanted)) {
                                setVal(area, value);
                                return true;
                            }
                        }
                    }
                    return false;
                }""",
                {"fragment": label_fragment, "value": value},
            )
        )
    except Exception:
        return False


async def _choose_workday_option(page, choices: list[str]) -> bool:
    await page.wait_for_timeout(700)
    for choice in choices:
        # Match role-based options first (most specific), then li/div with exact-text match
        # as fallback for Workday components that don't use ARIA roles on list items.
        locators = [
            page.get_by_role("option", name=re.compile(rf"{re.escape(choice)}", re.IGNORECASE)),
            page.get_by_role("menuitem", name=re.compile(rf"{re.escape(choice)}", re.IGNORECASE)),
            page.locator('[role="option"], [role="menuitem"], li').filter(
                has_text=re.compile(rf"^\s*{re.escape(choice)}\s*$", re.IGNORECASE)
            ),
            page.locator('[role="listbox"] div, [role="listbox"] span, [role="menu"] div, [role="menu"] span').filter(
                has_text=re.compile(rf"^\s*{re.escape(choice)}\s*$", re.IGNORECASE)
            ),
        ]
        for locator in locators:
            try:
                if await locator.count():
                    for index in range(min(await locator.count(), 12)):
                        item = locator.nth(index)
                        try:
                            if not await item.is_visible():
                                continue
                            await item.click(timeout=5000, force=True)
                            await page.wait_for_timeout(500)
                            try:
                                await page.keyboard.press("Escape")
                            except Exception:
                                pass
                            return True
                        except Exception:
                            continue
            except Exception:
                continue
        try:
            clicked = bool(
                await page.evaluate(
                    """(choice) => {
                        const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                        const norm = text => (text || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                        const wanted = norm(choice);
                        const inOpenMenu = el => !!el.closest('[role="listbox"], [role="menu"], [data-automation-id*="prompt"], [data-automation-id*="popup"]');
                        const candidates = Array.from(document.querySelectorAll('[role="option"], [role="menuitem"], [role="listbox"] li, [role="menu"] li, [role="listbox"] div, [role="listbox"] span, [role="menu"] div, [role="menu"] span, [data-automation-id*="prompt"] div, [data-automation-id*="prompt"] span'))
                            .filter(visible)
                            .map(el => {
                                const clickable = el.closest('[role="option"], [role="menuitem"], li, [data-automation-id*="promptOption"], [data-automation-id*="menuItem"]') || el;
                                return {el, clickable, rect: el.getBoundingClientRect(), text: norm(el.innerText || el.textContent || '')};
                            })
                            .filter(item => inOpenMenu(item.el) || item.clickable.getAttribute('role') === 'option' || item.clickable.getAttribute('role') === 'menuitem')
                            .filter(item => item.text === wanted || (wanted.length > 5 && item.text.includes(wanted)))
                            .filter(item => item.rect.width > 20 && item.rect.height > 8)
                            .sort((a, b) => {
                                const aRole = a.clickable.getAttribute('role') === 'option' || a.clickable.getAttribute('role') === 'menuitem' ? 0 : 1;
                                const bRole = b.clickable.getAttribute('role') === 'option' || b.clickable.getAttribute('role') === 'menuitem' ? 0 : 1;
                                return (aRole - bRole) || ((a.rect.width * a.rect.height) - (b.rect.width * b.rect.height));
                            });
                        const target = candidates[0]?.clickable;
                        if (!target) return false;
                        target.scrollIntoView({block: 'center'});
                        target.click();
                        return true;
                    }""",
                    choice,
                )
            )
            if clicked:
                    await page.wait_for_timeout(500)
                    try:
                        await page.keyboard.press("Escape")
                    except Exception:
                        pass
                    return True
        except Exception:
            continue
        try:
            await page.keyboard.type(choice, delay=10)
            await page.wait_for_timeout(300)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(500)
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            return True
        except Exception:
            continue
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass
    return False


async def _click_workday_save_and_continue(page) -> bool:
    try:
        clicked = bool(
            await page.evaluate(
                """() => {
                    const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    const norm = text => (text || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    const controls = Array.from(document.querySelectorAll('[role="button"], button, input[type="submit"]'))
                        .filter(visible);
                    const target = controls.find(el => norm(el.innerText || el.value || el.getAttribute('aria-label')) === 'save and continue');
                    if (!target) return false;
                    target.scrollIntoView({block: 'center'});
                    target.click();
                    return true;
                }"""
            )
        )
        if not clicked:
            button = page.get_by_role("button", name=re.compile(r"^\s*Save and Continue\s*$", re.IGNORECASE))
            if not await button.count():
                return False
            await button.first.click(timeout=10000, force=True)
        await page.wait_for_timeout(3500)
        return True
    except Exception:
        return False


async def _maybe_continue_workday_experience(page) -> bool:
    if "myworkdayjobs.com" not in page.url:
        return False
    try:
        text = await page.locator("body").inner_text(timeout=5000)
    except Exception:
        return False
    if "My Experience" not in text:
        return False
    if "Resume/CV" in text and "Successfully Uploaded" not in text:
        return False
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass
    return await _click_workday_save_and_continue(page)


# Phase 3.2: hard cap on the advancement loop. Workday has 5 known steps
# (My Information → My Experience → Application Questions → Voluntary Disclosures
# → Review); 8 leaves room for a single round-trip through Review repair without
# letting a misbehaving page spin forever.
_WORKDAY_MAX_STEPS = 8

# Standard step-change timeout. All Workday Save-and-Continue clicks should poll
# for the step label to change instead of using a fixed sleep.
_WORKDAY_STEP_CHANGE_TIMEOUT_MS = 25000


async def _workday_advance_all_steps(
    page,
    values: dict,
    pdf: "Path | None" = None,
    *,
    company: str | None = None,
    role: str | None = None,
    report_context: dict | None = None,
    artifact_dir: "Path | None" = None,
) -> tuple[list[str], list[str], list[dict[str, str]]]:
    """Walk through all Workday multi-step pages up to (but not including) the final Review submission.

    Handles My Experience → Application Questions → Voluntary Disclosures → Review.
    Stops at Review so the user can inspect and submit manually.

    The third returned tuple element is a list of free-form Q&A pairs
    ``[{"question": ..., "answer": ...}]`` filled on the Application Questions step;
    callers should merge this into the master ``answers`` list so saved-answer fuzzy
    match can reuse them on subsequent runs.
    """
    if "myworkdayjobs.com" not in page.url:
        return [], [], []

    filled: list[str] = []
    skipped: list[str] = []
    answers: list[dict[str, str]] = []
    prev_step: str = ""

    def _log(event: str, **fields) -> None:  # Phase 3.4 — local emit helper.
        if artifact_dir is not None:
            apply_run_log.emit(artifact_dir, event, **fields)

    # Allow the Save and Continue that _auto_fill_application just triggered to
    # finish loading before we start polling. Workday transitions can be slow;
    # poll for a step change instead of relying on one fixed sleep.
    initial_step = await _workday_current_step(page)
    if initial_step:
        await _wait_for_workday_step_change(page, initial_step, timeout_ms=20000)
    if await _workday_current_step(page) == "Review" and await _workday_review_needs_repair(page):
        if await _workday_go_back_to_step(page, "My Experience"):
            filled.append("Workday Review repair: returned to My Experience")
            prev_step = ""
        else:
            skipped.append("Workday Review repair needed but could not navigate back to My Experience.")

    for _ in range(_WORKDAY_MAX_STEPS):
        step = await _workday_current_step(page)
        if not step or step == "Review":
            if step == "Review":
                _log("step.entered", step="Review")
            break
        _log("step.entered", step=step, prev_step=prev_step)

        if step == "Create Account":
            # Session expired or not logged in. _maybe_workday_login should have handled
            # this before _auto_fill_application, but it may have failed.
            skipped.append(
                "Workday Create Account/Sign In step detected — session may have expired. "
                "Check browser and sign in manually, then run again."
            )
            break

        if step == "My Information":
            f, s, a = await _fill_workday_current_step(
                page, values, company=company, role=role, report_context=report_context
            )
            filled.extend(f)
            skipped.extend(s)
            answers.extend(a)
            await _wait_for_workday_step_change(page, step, timeout_ms=_WORKDAY_STEP_CHANGE_TIMEOUT_MS)
            if await _workday_current_step(page) == "My Information":
                skipped.append(
                    "My Information: Save and Continue did not advance after refill — validation errors likely prevent advancing."
                )
                break
            continue

        if step == prev_step:
            # Phase 3.2: surface the actual validation reasons so the user
            # doesn't have to inspect the page to figure out why we stalled.
            stuck_required = await _required_empty_fields(page)
            stuck_required = _filter_required_empty_fields(stuck_required, filled)
            reason = (
                f"Workday stuck on '{step}' after Save and Continue — "
                "validation errors likely prevent advancing; leaving for user review."
            )
            if stuck_required:
                reason += " Required-empty fields seen: " + ", ".join(stuck_required[:5])
            skipped.append(reason)
            break
        prev_step = step

        if step == "My Experience":
            f, s = await _fill_workday_my_experience(page, values)
            filled.extend(f)
            skipped.extend(s)
            if await _fill_workday_social_network_url(page, values["linkedin"]):
                filled.append("Workday Social Network URLs (validated)")

            if pdf:
                removed = await _workday_remove_duplicate_uploads(page, keep_filenames=[pdf.name])
                if removed:
                    filled.append(f"Workday removed duplicate resume upload(s): {removed}")
                await _attach_resume(page, pdf)
                removed = await _workday_remove_duplicate_uploads(page, keep_filenames=[pdf.name])
                if removed:
                    filled.append(f"Workday removed duplicate resume upload(s): {removed}")
                await page.wait_for_timeout(2000)

            try:
                text = await page.locator("body").inner_text(timeout=3000)
            except Exception:
                text = ""
            if "Resume/CV" in text and "Successfully Uploaded" not in text:
                skipped.append("My Experience: Resume/CV not yet uploaded — cannot advance past this step.")
                break

            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            if await _click_workday_save_and_continue(page):
                filled.append("Workday My Experience → Save and Continue")
                _log("save_and_continue.clicked", step=step)
                changed = await _wait_for_workday_step_change(
                    page, step, timeout_ms=_WORKDAY_STEP_CHANGE_TIMEOUT_MS
                )
                next_step = await _workday_current_step(page)
                if changed and next_step and next_step != step:
                    _log("step.changed", **{"from": step, "to": next_step})
                else:
                    _log("step.change_timeout", step=step,
                         timeout_ms=_WORKDAY_STEP_CHANGE_TIMEOUT_MS)

        elif step == "Application Questions":
            f, s, a = await _fill_workday_application_questions(
                page, values, company=company, role=role, report_context=report_context
            )
            filled.extend(f)
            skipped.extend(s)
            answers.extend(a)
            if await _click_workday_save_and_continue(page):
                filled.append("Workday Application Questions → Save and Continue")
                _log("save_and_continue.clicked", step=step)
                changed = await _wait_for_workday_step_change(
                    page, step, timeout_ms=_WORKDAY_STEP_CHANGE_TIMEOUT_MS
                )
                next_step = await _workday_current_step(page)
                if changed and next_step and next_step != step:
                    _log("step.changed", **{"from": step, "to": next_step})
                else:
                    _log("step.change_timeout", step=step,
                         timeout_ms=_WORKDAY_STEP_CHANGE_TIMEOUT_MS)

        elif step == "Voluntary Disclosures":
            skipped.append("Workday Voluntary Disclosures: demographic questions left blank per policy.")
            f, s = await _fill_workday_voluntary_disclosures(page, values)
            filled.extend(f)
            skipped.extend(s)
            if s:
                break
            if await _click_workday_save_and_continue(page):
                filled.append("Workday Voluntary Disclosures → Save and Continue (blank)")
                _log("save_and_continue.clicked", step=step)
                changed = await _wait_for_workday_step_change(
                    page, step, timeout_ms=_WORKDAY_STEP_CHANGE_TIMEOUT_MS
                )
                next_step = await _workday_current_step(page)
                if changed and next_step and next_step != step:
                    _log("step.changed", **{"from": step, "to": next_step})
                else:
                    _log("step.change_timeout", step=step,
                         timeout_ms=_WORKDAY_STEP_CHANGE_TIMEOUT_MS)

        else:
            skipped.append(f"Workday unrecognized step '{step}' — stopping auto-advance.")
            break

    if await _workday_current_step(page) == "Review" and await _workday_review_needs_repair(page):
        if await _workday_go_back_to_step(page, "My Experience"):
            filled.append("Workday Review repair: returned to My Experience after validation")
            f, s = await _fill_workday_my_experience(page, values)
            filled.extend(f)
            skipped.extend(s)
            if await _fill_workday_social_network_url(page, values["linkedin"]):
                filled.append("Workday Social Network URLs (validated)")
            if pdf:
                await _attach_resume(page, pdf)
                removed = await _workday_remove_duplicate_uploads(page, keep_filenames=[pdf.name])
                if removed:
                    filled.append(f"Workday removed duplicate resume upload(s): {removed}")
            if await _click_workday_save_and_continue(page):
                await _wait_for_workday_step_change(page, "My Experience", timeout_ms=_WORKDAY_STEP_CHANGE_TIMEOUT_MS)
            for _ in range(4):
                step = await _workday_current_step(page)
                if not step or step == "Review":
                    break
                if step == "Application Questions":
                    f, s, a = await _fill_workday_application_questions(
                        page, values, company=company, role=role, report_context=report_context
                    )
                    filled.extend(f)
                    skipped.extend(s)
                    answers.extend(a)
                elif step == "Voluntary Disclosures":
                    f, s = await _fill_workday_voluntary_disclosures(page, values)
                    filled.extend(f)
                    skipped.extend(s)
                    if s:
                        break
                if await _click_workday_save_and_continue(page):
                    await _wait_for_workday_step_change(page, step, timeout_ms=_WORKDAY_STEP_CHANGE_TIMEOUT_MS)
                else:
                    break
        if await _workday_current_step(page) == "Review":
            review_issues = await _collect_workday_review_issues(page)
            for issue in review_issues:
                _log(
                    "review.validation",
                    issue_code=issue.code,
                    message=issue.message,
                    details=dict(issue.details) if issue.details else None,
                )
            skipped.extend(issue.message for issue in review_issues)

    return filled, _dedupe_preserve_order(skipped), answers


async def _workday_review_needs_repair(page) -> bool:
    return await _workday_review_needs_repair_from_module(
        page,
        experience_entries=_workday_experience_entries(),
        education_entries=_workday_education_entries(_apply_profile_values()),
    )


async def _workday_review_validation_issues(page) -> list[str]:
    """Return human-readable Review-gate messages (backwards-compatible signature).

    Phase 3.1: the structured ``ReviewIssue`` records are also captured via
    ``_collect_workday_review_issues`` so they can be written to apply-review.json.
    """
    return await _workday_review_validation_messages(
        page,
        experience_entries=_workday_experience_entries(),
        education_entries=_workday_education_entries(_apply_profile_values()),
    )


async def _collect_workday_review_issues(page) -> list[ReviewIssue]:
    """Phase 3.1: structured Review-gate output for apply-review.json::validation_issues[]."""
    return await detect_review_issues(
        page,
        experience_entries=_workday_experience_entries(),
        education_entries=_workday_education_entries(_apply_profile_values()),
    )


async def _workday_go_back_to_step(page, desired_step: str) -> bool:
    for _ in range(5):
        step = await _workday_current_step(page)
        if step == desired_step:
            return True
        try:
            back = page.get_by_role("button", name=re.compile(r"^\s*(Back|后退)\s*$", re.I)).first
            if not await back.count():
                return False
            previous = step or ""
            await back.click(timeout=10000, force=True)
            if previous:
                await _wait_for_workday_step_change(page, previous, timeout_ms=15000)
            else:
                await page.wait_for_timeout(2500)
        except Exception:
            return False
    return await _workday_current_step(page) == desired_step


async def _fill_workday_my_experience(page, values: dict) -> tuple[list[str], list[str]]:
    filled: list[str] = []
    skipped: list[str] = []
    exp = _workday_experience_entries()[0]
    if await _ensure_workday_section_item(page, "Work Experience") and await _fill_workday_structured_experience(page, exp):
        filled.append("Workday structured work experience")
    else:
        skipped.append("Workday structured work experience: section not found or not editable.")
    edu = _workday_education_entries(values)[0]
    if await _ensure_workday_section_item(page, "Education") and await _fill_workday_structured_education(page, edu):
        filled.append("Workday structured education")
    else:
        skipped.append("Workday structured education: section not found or not editable.")
    if os.getenv("JOB_HUNT_WORKDAY_DEBUG"):
        await _write_workday_my_experience_debug(page)
    try:
        await page.keyboard.press("Tab")
        await page.wait_for_timeout(800)
    except Exception:
        pass
    return filled, skipped


async def _write_workday_my_experience_debug(page) -> None:
    """Back-compat wrapper. Body lives in services.workday.my_experience."""
    from job_hunt.services.workday.my_experience import write_debug_field_dump

    await write_debug_field_dump(page)


async def _ensure_workday_section_item(page, section_label: str) -> bool:
    try:
        return bool(
            await page.evaluate(
                """async (sectionLabel) => {
                    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
                    const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    const norm = text => (text || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    const wanted = norm(sectionLabel);
                    const markers = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,[role="heading"],div,span'))
                        .filter(visible)
                        .map(el => ({el, text: norm(el.innerText), rect: el.getBoundingClientRect()}))
                        .filter(item => item.text === wanted);
                    const marker = markers.sort((a, b) => a.rect.top - b.rect.top)[0];
                    if (!marker) return false;
                    const next = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,[role="heading"],div,span'))
                        .filter(visible)
                        .map(el => ({el, text: norm(el.innerText), rect: el.getBoundingClientRect()}))
                        .filter(item => item.rect.top > marker.rect.top + 8)
                        .filter(item => ['work experience','education','skills','resume/cv','websites','social network urls'].includes(item.text))
                        .sort((a, b) => a.rect.top - b.rect.top)[0];
                    const bottom = next ? next.rect.top : Number.POSITIVE_INFINITY;
                    const sectionText = document.body.innerText.slice(0);
                    if (wanted.includes('work') && sectionText.includes('Job Title')) return true;
                    if (wanted.includes('education') && sectionText.includes('School or University')) return true;
                    const add = Array.from(document.querySelectorAll('button,[role="button"]'))
                        .filter(visible)
                        .map(btn => ({btn, text: norm(btn.innerText || btn.getAttribute('aria-label')), rect: btn.getBoundingClientRect()}))
                        .filter(item => item.rect.top > marker.rect.top && item.rect.top < bottom)
                        .find(item => item.text === 'add' || item.text === 'add another');
                    if (!add) return false;
                    add.btn.scrollIntoView({block: 'center'});
                    add.btn.click();
                    await sleep(1200);
                    return wanted.includes('work')
                        ? document.body.innerText.includes('Job Title')
                        : document.body.innerText.includes('School or University');
                }""",
                section_label,
            )
        )
    except Exception:
        return False


async def _fill_workday_structured_experience(page, entry: dict[str, str]) -> bool:
    fields_ok = False
    fields_ok = await _force_fill_by_accessible_label(page, "Job Title", entry["title"]) or fields_ok
    fields_ok = await _force_fill_by_accessible_label(page, "Company", entry["company"]) or fields_ok
    fields_ok = await _force_fill_by_accessible_label(page, "Location", entry["location"]) or fields_ok
    fields_ok = await _force_fill_by_accessible_label(page, "Role Description", entry["description"]) or fields_ok
    fields_ok = await _fill_workday_scoped_field(page, "Work Experience 1", "Job Title", entry["title"]) or fields_ok
    fields_ok = await _fill_workday_scoped_field(page, "Work Experience 1", "Company", entry["company"]) or fields_ok
    fields_ok = await _fill_workday_scoped_field(page, "Work Experience 1", "Location", entry["location"]) or fields_ok
    fields_ok = await _fill_workday_scoped_field(page, "Work Experience 1", "Role Description", entry["description"]) or fields_ok
    fields_ok = await _fill_workday_experience_card_by_order(page, entry) or fields_ok
    dates_ok = await _fill_workday_experience_dates_by_title(page, entry)
    return fields_ok and dates_ok and await _workday_any_input_has_value(page, entry["title"])


async def _force_fill_by_accessible_label(page, label: str, value: str) -> bool:
    if not value:
        return False
    try:
        locator = page.get_by_label(label, exact=False)
        count = await locator.count()
        for index in range(min(count, 8)):
            field = locator.nth(index)
            try:
                if not await field.is_visible(timeout=1000):
                    continue
                tag = await field.evaluate("el => el.tagName.toLowerCase()")
                field_type = (await field.get_attribute("type") or "").lower()
                if tag not in {"input", "textarea"} or field_type in {"hidden", "file", "radio", "checkbox", "submit"}:
                    continue
                await field.fill(value, timeout=5000)
                try:
                    await field.press("Tab")
                except Exception:
                    pass
                return True
            except Exception:
                continue
    except Exception:
        return False
    return False


async def _fill_workday_experience_card_by_order(page, entry: dict[str, str]) -> bool:
    try:
        return bool(
            await page.evaluate(
                """(entry) => {
                    const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    const setValue = (input, val) => {
                        const proto = input.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
                        const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                        if (setter) setter.call(input, String(val)); else input.value = String(val);
                        input.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: String(val)}));
                        input.dispatchEvent(new Event('change', {bubbles: true}));
                        input.dispatchEvent(new Event('blur', {bubbles: true}));
                    };
                    const groups = Array.from(document.querySelectorAll('div, fieldset, [role="group"]'))
                        .filter(visible)
                        .filter(el => {
                            const text = el.innerText || '';
                            return text.includes('Work Experience 1')
                                && text.includes('Job Title')
                                && text.includes('Company')
                                && text.includes('From')
                                && text.includes('To')
                                && text.includes('Role Description');
                        })
                        .sort((a, b) => {
                            const ar = a.getBoundingClientRect();
                            const br = b.getBoundingClientRect();
                            return (ar.height * ar.width) - (br.height * br.width);
                        });
                    const group = groups[0];
                    if (!group) return false;
                    const inputs = Array.from(group.querySelectorAll('input:not([type="hidden"]):not([type="file"]):not([type="checkbox"]):not([type="radio"])'))
                        .filter(visible)
                        .filter(input => !/search/i.test(input.getAttribute('placeholder') || ''))
                        .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top || a.getBoundingClientRect().left - b.getBoundingClientRect().left);
                    const textInputs = inputs.filter(input => (input.getAttribute('role') || '') !== 'spinbutton');
                    const spins = inputs.filter(input => (input.getAttribute('role') || '') === 'spinbutton');
                    const textareas = Array.from(group.querySelectorAll('textarea'))
                        .filter(visible)
                        .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top || a.getBoundingClientRect().left - b.getBoundingClientRect().left);
                    if (textInputs.length < 3) return false;
                    setValue(textInputs[0], entry.title);
                    setValue(textInputs[1], entry.company);
                    setValue(textInputs[2], entry.location);
                    const months = spins.filter(input => /month/i.test(input.getAttribute('aria-label') || input.getAttribute('data-automation-id') || ''));
                    const years = spins.filter(input => /year/i.test(input.getAttribute('aria-label') || input.getAttribute('data-automation-id') || ''));
                    if (months.length >= 2 && years.length >= 2) {
                        setValue(months[0], String(Number(entry.start_month)).padStart(2, '0'));
                        setValue(years[0], entry.start_year);
                        setValue(months[1], String(Number(entry.end_month)).padStart(2, '0'));
                        setValue(years[1], entry.end_year);
                    } else if (spins.length >= 4) {
                        setValue(spins[0], String(Number(entry.start_month)).padStart(2, '0'));
                        setValue(spins[1], entry.start_year);
                        setValue(spins[2], String(Number(entry.end_month)).padStart(2, '0'));
                        setValue(spins[3], entry.end_year);
                    } else if (textInputs.length >= 5) {
                        setValue(textInputs[3], `${String(Number(entry.start_month)).padStart(2, '0')}/${entry.start_year}`);
                        setValue(textInputs[4], `${String(Number(entry.end_month)).padStart(2, '0')}/${entry.end_year}`);
                    }
                    if (textareas[0]) setValue(textareas[0], entry.description);
                    return true;
                }""",
                entry,
            )
        )
    except Exception:
        return False


async def _fill_workday_experience_dates_by_title(page, entry: dict[str, str]) -> bool:
    """Fill dates only inside the work-experience card for entry["title"].

    Workday repeats labels like "From" and "To" across work and education cards.
    Page-wide label matching can hit the wrong month/year widgets, so this helper
    scopes to the smallest visible card containing the job title and commits values
    through Playwright keyboard/fill actions.
    """
    group = None
    if (
        await _force_fill_by_accessible_label(page, "From", f"{int(entry['start_month']):02d}/{entry['start_year']}")
        and await _force_fill_by_accessible_label(page, "To", f"{int(entry['end_month']):02d}/{entry['end_year']}")
    ):
        await page.wait_for_timeout(500)
        if await _workday_experience_dates_match(page, entry):
            return True
    try:
        handle = await page.evaluate_handle(
            """(entry) => {
                    const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    const titleInput = Array.from(document.querySelectorAll('input'))
                        .filter(visible)
                        .find(input => (input.value || '').includes(entry.title));
                    let best = null;
                    if (titleInput) {
                        let group = titleInput.parentElement;
                        for (let depth = 0; group && depth < 10; depth++, group = group.parentElement) {
                            const text = group.innerText || '';
                            if (text.includes('From') && text.includes('To') && text.includes('Company')) {
                                best = group;
                                break;
                            }
                        }
                    }
                    if (!best) {
                        best = Array.from(document.querySelectorAll('div, fieldset, [role="group"]'))
                            .filter(visible)
                            .filter(el => {
                                const text = el.innerText || '';
                                return text.includes('Work Experience 1') && text.includes('From') && text.includes('To') && text.includes('Company');
                            })
                            .sort((a, b) => {
                                const ar = a.getBoundingClientRect();
                                const br = b.getBoundingClientRect();
                                return (ar.height * ar.width) - (br.height * br.width);
                            })[0] || null;
                    }
                    return best;
                }""",
            entry,
        )
        group = handle.as_element()
        if not group:
            return False
        month_inputs = [
            item
            for item in await group.query_selector_all('input[role="spinbutton"][aria-label="Month"], input[data-automation-id*="Month"]')
            if await item.is_visible()
        ]
        year_inputs = [
            item
            for item in await group.query_selector_all('input[role="spinbutton"][aria-label="Year"], input[data-automation-id*="Year"]')
            if await item.is_visible()
        ]
        if len(month_inputs) >= 2 and len(year_inputs) >= 2:
            values = [
                (month_inputs[0], f"{int(entry['start_month']):02d}"),
                (year_inputs[0], entry["start_year"]),
                (month_inputs[1], f"{int(entry['end_month']):02d}"),
                (year_inputs[1], entry["end_year"]),
            ]
            for field, value in values:
                await _replace_workday_element_value(field, value)
            await page.wait_for_timeout(500)
            return await _workday_experience_dates_match(page, entry)

        date_inputs = [
            item
            for item in await group.query_selector_all('input:not([type="hidden"]):not([type="file"])')
            if await item.is_visible()
        ]
        masked = []
        for item in date_inputs:
            placeholder = await item.get_attribute("placeholder") or ""
            aria = await item.get_attribute("aria-label") or ""
            current = await item.input_value()
            if re.search(r"mm\s*/\s*yyyy", f"{placeholder} {aria} {current}", re.I):
                masked.append(item)
        if len(masked) >= 2:
            await _replace_workday_element_value(masked[0], f"{int(entry['start_month']):02d}/{entry['start_year']}")
            await _replace_workday_element_value(masked[1], f"{int(entry['end_month']):02d}/{entry['end_year']}")
            await page.wait_for_timeout(500)
            return await _workday_experience_dates_match(page, entry)
        return False
    except Exception:
        return False
    finally:
        try:
            if group:
                await group.dispose()
        except Exception:
            pass


async def _replace_workday_element_value(field, value: str) -> None:
    await field.scroll_into_view_if_needed(timeout=3000)
    try:
        await field.click(timeout=5000, force=True)
        await field.press("Meta+A")
        await field.press("Backspace")
        await field.type(str(value), delay=20)
        try:
            await field.press("Enter")
        except Exception:
            pass
        await field.press("Tab")
        return
    except Exception:
        await field.fill(str(value), timeout=5000)


async def _workday_experience_dates_match(page, entry: dict[str, str]) -> bool:
    """Back-compat wrapper. Body lives in services.workday.my_experience."""
    from job_hunt.services.workday.my_experience import experience_dates_match

    return await experience_dates_match(page, entry)


async def _fill_workday_structured_education(page, entry: dict[str, str]) -> bool:
    ok = False
    ok = await _force_fill_by_accessible_label(page, "School or University", entry["school"]) or ok
    ok = await _fill_workday_scoped_field(page, "Education 1", "School or University", entry["school"]) or ok
    if (
        await _select_workday_dropdown_by_label(
            page,
            "Degree",
            ["Master of Science (M.S.)", "Master's Degree", "Master of Data Analytics", "Other"],
            force=True,
        )
        or await _select_workday_dropdown_containing_label(page, "Degree", ["Master of Science (M.S.)", "Other"])
    ):
        ok = True
    ok = await _force_fill_by_accessible_label(page, "Field of Study", entry["field"]) or ok
    ok = await _fill_workday_scoped_field(page, "Education 1", "Field of Study", entry["field"]) or ok
    if await _select_workday_dropdown_by_label(page, "Field of Study", [entry["field"], "Data Science", "Computer Science", "Information Technology", "Other"], force=True):
        ok = True
    ok = await _force_fill_by_accessible_label(page, "Overall Result", entry["gpa"]) or ok
    ok = await _fill_workday_scoped_field(page, "Education 1", "Overall Result", entry["gpa"]) or ok
    ok = await _fill_workday_education_card_by_order(page, entry) or ok
    return ok and await _workday_any_input_has_value(page, entry["school"])


async def _fill_workday_education_card_by_order(page, entry: dict[str, str]) -> bool:
    try:
        return bool(
            await page.evaluate(
                """(entry) => {
                    const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    const setValue = (input, val) => {
                        const proto = input.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
                        const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                        if (setter) setter.call(input, String(val)); else input.value = String(val);
                        input.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: String(val)}));
                        input.dispatchEvent(new Event('change', {bubbles: true}));
                        input.dispatchEvent(new Event('blur', {bubbles: true}));
                    };
                    const groups = Array.from(document.querySelectorAll('div, fieldset, [role="group"]'))
                        .filter(visible)
                        .filter(el => {
                            const text = el.innerText || '';
                            return text.includes('Education 1')
                                && text.includes('School or University')
                                && text.includes('Field of Study')
                                && text.includes('Overall Result');
                        })
                        .sort((a, b) => {
                            const ar = a.getBoundingClientRect();
                            const br = b.getBoundingClientRect();
                            return (ar.height * ar.width) - (br.height * br.width);
                        });
                    const group = groups[0];
                    if (!group) return false;
                    const inputs = Array.from(group.querySelectorAll('input:not([type="hidden"]):not([type="file"]):not([type="checkbox"]):not([type="radio"])'))
                        .filter(visible)
                        .filter(input => !/search/i.test(input.getAttribute('placeholder') || ''))
                        .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top || a.getBoundingClientRect().left - b.getBoundingClientRect().left);
                    if (!inputs.length) return false;
                    setValue(inputs[0], entry.school);
                    const fieldInput = inputs.find(input => /field|study|major/i.test(input.getAttribute('aria-label') || input.placeholder || input.parentElement?.innerText || ''));
                    if (fieldInput) setValue(fieldInput, entry.field);
                    const gpaInput = inputs.find(input => /overall|gpa|result/i.test(input.getAttribute('aria-label') || input.placeholder || input.parentElement?.innerText || ''));
                    if (gpaInput && entry.gpa) setValue(gpaInput, entry.gpa);
                    return true;
                }""",
                entry,
            )
        )
    except Exception:
        return False


async def _fill_workday_social_network_url(page, value: str) -> bool:
    if not value:
        return False
    try:
        return bool(
            await page.evaluate(
                """(value) => {
                    const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    const norm = text => (text || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    const setValue = (input, val) => {
                        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                        if (setter) setter.call(input, val); else input.value = val;
                        input.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: val}));
                        input.dispatchEvent(new Event('change', {bubbles: true}));
                        input.dispatchEvent(new Event('blur', {bubbles: true}));
                    };
                    const headings = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,[role="heading"], div, span'))
                        .filter(visible)
                        .map(el => ({el, text: norm(el.innerText), rect: el.getBoundingClientRect()}))
                        .filter(item => item.text.length <= 160)
                        .filter(item => item.text === 'social network urls' || item.text.includes('provide your linkedin url'))
                        .sort((a, b) => a.rect.top - b.rect.top);
                    const heading = headings[0];
                    if (!heading) return false;
                    const input = Array.from(document.querySelectorAll('input:not([type="hidden"]):not([type="file"])'))
                        .filter(visible)
                        .map(input => ({input, rect: input.getBoundingClientRect()}))
                        .filter(item => item.rect.top >= heading.rect.top && item.rect.top - heading.rect.bottom < 220)
                        .sort((a, b) => a.rect.top - b.rect.top)[0]?.input;
                    if (!input) return false;
                    setValue(input, value);
                    return true;
                }""",
                value,
            )
        )
    except Exception:
        return False


async def _fill_workday_scoped_field(page, marker: str, label_fragment: str, value: str) -> bool:
    if not value:
        return False
    try:
        return bool(
            await page.evaluate(
                """({marker, labelFragment, value}) => {
                    const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    const norm = text => (text || '').replace(/\\*/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    const markerText = norm(marker);
                    const wanted = norm(labelFragment);
                    const setValue = (input, val) => {
                        const proto = input.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
                        const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                        if (setter) setter.call(input, val); else input.value = val;
                        input.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: val}));
                        input.dispatchEvent(new Event('change', {bubbles: true}));
                        input.dispatchEvent(new Event('blur', {bubbles: true}));
                    };
                    const candidates = Array.from(document.querySelectorAll('div, fieldset, [role="group"]'))
                        .filter(visible)
                        .filter(el => {
                            const text = norm(el.innerText);
                            return text.includes(markerText) && text.includes(wanted);
                        })
                        .sort((a, b) => {
                            const ar = a.getBoundingClientRect();
                            const br = b.getBoundingClientRect();
                            return (ar.height * ar.width) - (br.height * br.width);
                        });
                    const group = candidates[0];
                    if (!group) return false;
                    const labels = Array.from(group.querySelectorAll('label, div, span'))
                        .filter(visible)
                        .map(el => ({el, text: norm(el.innerText), rect: el.getBoundingClientRect()}))
                        .filter(item => item.text.length <= 140)
                        .filter(item => item.text === wanted || item.text.startsWith(wanted + ' '))
                        .sort((a, b) => a.rect.top - b.rect.top);
                    for (const label of labels) {
                        const inputs = Array.from(group.querySelectorAll('input:not([type="hidden"]):not([type="file"]):not([type="checkbox"]):not([type="radio"]), textarea'))
                            .filter(visible)
                            .map(input => ({input, rect: input.getBoundingClientRect()}))
                            .filter(item => item.rect.top >= label.rect.bottom - 16 && item.rect.top - label.rect.bottom < 180)
                            .sort((a, b) => (a.rect.top - b.rect.top) || (a.rect.left - b.rect.left));
                        const field = inputs[0]?.input;
                        if (!field) continue;
                        setValue(field, value);
                        return true;
                    }
                    return false;
                }""",
                {"marker": marker, "labelFragment": label_fragment, "value": value},
            )
        )
    except Exception:
        return False


async def _fill_workday_input_near_text(page, label_fragment: str, value: str) -> bool:
    if not value:
        return False
    try:
        return bool(
            await page.evaluate(
                """({labelFragment, value}) => {
                    const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    const norm = text => (text || '').replace(/\\*/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    const wanted = norm(labelFragment);
                    const setValue = (input, val) => {
                        const proto = input.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
                        const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                        if (setter) setter.call(input, val); else input.value = val;
                        input.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: val}));
                        input.dispatchEvent(new Event('change', {bubbles: true}));
                        input.dispatchEvent(new Event('blur', {bubbles: true}));
                    };
                    const labels = Array.from(document.querySelectorAll('label, div, span'))
                        .filter(visible)
                        .map(el => ({el, text: norm(el.innerText), rect: el.getBoundingClientRect()}))
                        .filter(item => item.text.includes(wanted))
                        .sort((a, b) => a.rect.top - b.rect.top);
                    for (const label of labels) {
                        const inputs = Array.from(document.querySelectorAll('input:not([type="hidden"]):not([type="file"]):not([type="checkbox"]):not([type="radio"]), textarea'))
                            .filter(visible)
                            .map(input => ({input, rect: input.getBoundingClientRect()}))
                            .filter(item => item.rect.top >= label.rect.top - 8 && item.rect.top - label.rect.bottom < 120)
                            .sort((a, b) => Math.abs(a.rect.top - label.rect.bottom) - Math.abs(b.rect.top - label.rect.bottom));
                        if (inputs[0]) {
                            setValue(inputs[0].input, value);
                            return true;
                        }
                    }
                    return false;
                }""",
                {"labelFragment": label_fragment, "value": value},
            )
        )
    except Exception:
        return False


async def _fill_workday_month_year_containing(page, label_fragment: str, month: str, year: str) -> bool:
    masked_value = f"{int(month):02d}/{year}"
    try:
        month_spin = page.locator(
            "xpath="
            f"//*[self::label or self::div or self::span][normalize-space()='{label_fragment}' or normalize-space()='{label_fragment}*']"
            "/following::input[@role='spinbutton' and @aria-label='Month'][1]"
        )
        year_spin = page.locator(
            "xpath="
            f"//*[self::label or self::div or self::span][normalize-space()='{label_fragment}' or normalize-space()='{label_fragment}*']"
            "/following::input[@role='spinbutton' and @aria-label='Year'][1]"
        )
        if await month_spin.count() and await year_spin.count():
            await month_spin.first.fill(str(int(month)), timeout=5000)
            await year_spin.first.fill(str(year), timeout=5000)
            await page.wait_for_timeout(300)
            month_value = await month_spin.first.input_value(timeout=1000)
            year_value = await year_spin.first.input_value(timeout=1000)
            if str(int(month)) == str(int(month_value or 0)) and str(year) == str(year_value):
                return True
    except Exception:
        pass
    try:
        loc = page.locator(
            "xpath="
            f"//*[self::label or self::div or self::span][normalize-space()='{label_fragment}' or normalize-space()='{label_fragment}*']"
            "/following::input[not(@type='hidden') and not(@type='file')][1]"
        )
        if await loc.count():
            field = loc.first
            await field.scroll_into_view_if_needed(timeout=3000)
            try:
                await field.fill(masked_value, timeout=5000)
            except Exception:
                await field.click(timeout=5000, force=True)
                await page.keyboard.press("Meta+A")
                await page.keyboard.type(masked_value, delay=20)
            await page.wait_for_timeout(300)
            try:
                current = (await field.input_value(timeout=1000)).strip()
                if year in current and f"{int(month):02d}" in current:
                    return True
            except Exception:
                pass
    except Exception:
        pass
    try:
        return bool(
            await page.evaluate(
                """({labelFragment, month, year}) => {
                    const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    const norm = text => (text || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    const wanted = norm(labelFragment);
                    const setValue = (input, value) => {
                        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                        if (setter) setter.call(input, String(value)); else input.value = String(value);
                        input.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: String(value)}));
                        input.dispatchEvent(new Event('change', {bubbles: true}));
                        input.dispatchEvent(new Event('blur', {bubbles: true}));
                    };
                    const paddedMonth = String(Number(month)).padStart(2, '0');
                    const maskedValue = `${paddedMonth}/${year}`;
                    const labels = Array.from(document.querySelectorAll('label, div, span'))
                        .filter(visible)
                        .map(el => ({el, text: norm(el.innerText), rect: el.getBoundingClientRect()}))
                        .filter(item => item.text === wanted || item.text.startsWith(wanted + ' '))
                        .sort((a, b) => a.rect.top - b.rect.top);
                    for (const label of labels) {
                        const visibleDateInputs = Array.from(document.querySelectorAll('input:not([type="hidden"]):not([type="file"])'))
                            .filter(visible)
                            .filter(input => (input.getAttribute('role') || '') !== 'spinbutton')
                            .map(input => ({input, rect: input.getBoundingClientRect(), placeholder: input.placeholder || ''}))
                            .filter(item => item.rect.top >= label.rect.top - 12 && item.rect.top - label.rect.bottom < 140)
                            .sort((a, b) => a.rect.left - b.rect.left);
                        const masked = visibleDateInputs.find(item => /mm\\s*\\/\\s*yyyy/i.test(item.placeholder) || item.input.value.includes('/'));
                        if (masked) {
                            setValue(masked.input, maskedValue);
                            return true;
                        }
                        const inputs = Array.from(document.querySelectorAll('input[role="spinbutton"]'))
                            .filter(visible)
                            .map(input => ({input, rect: input.getBoundingClientRect()}))
                            .filter(item => item.rect.top >= label.rect.top - 12 && item.rect.top - label.rect.bottom < 120)
                            .sort((a, b) => a.rect.left - b.rect.left);
                        const monthInput = inputs.find(item => /month/i.test(item.input.getAttribute('aria-label') || ''))?.input;
                        const yearInput = inputs.find(item => /year/i.test(item.input.getAttribute('aria-label') || ''))?.input;
                        if (monthInput && yearInput) {
                            setValue(monthInput, Number(month));
                            setValue(yearInput, year);
                            return true;
                        }
                    }
                    return false;
                }""",
                {"labelFragment": label_fragment, "month": month, "year": year},
            )
        )
    except Exception:
        return False


async def _workday_any_input_has_value(page, value: str) -> bool:
    try:
        return bool(
            await page.evaluate(
                """(value) => Array.from(document.querySelectorAll('input, textarea')).some(el => (el.value || '').includes(value))""",
                value,
            )
        )
    except Exception:
        return False


def _workday_experience_entries() -> list[dict[str, str]]:
    return _load_workday_experience_entries()


def _workday_education_entries(values: dict) -> list[dict[str, str]]:
    return _load_workday_education_entries(values)


async def _workday_fill_repeating_section(page, *, section_keywords: list[str], entries: list[dict[str, str]], kind: str) -> bool:
    try:
        try:
            body_text = await page.locator("body").inner_text(timeout=2000)
        except Exception:
            body_text = ""
        marker = entries[0].get("title") if kind == "experience" else entries[0].get("school")
        if marker and marker not in body_text:
            for keyword in section_keywords:
                keyword_text = keyword.replace("'", "")
                add = page.locator(
                    "xpath="
                    f"//*[self::h3 or self::h4 or self::h5 or @role='heading'][contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{keyword_text.lower()}')]"
                    "/following::button[normalize-space()='Add' or normalize-space()='Add Another' or normalize-space()='添加' or normalize-space()='添加另一个'][1]"
                )
                try:
                    if await add.count():
                        await add.first.click(timeout=5000, force=True)
                        await page.wait_for_timeout(1200)
                        break
                except Exception:
                    continue
        result = await page.evaluate(
            """async ({sectionKeywords, entries, kind}) => {
                const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
                const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                const norm = text => (text || '').replace(/\\*/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const setInput = (el, value) => {
                    if (!el || value === undefined || value === null) return false;
                    const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
                    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                    if (setter) setter.call(el, String(value)); else el.value = String(value);
                    el.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: String(value)}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    el.dispatchEvent(new Event('blur', {bubbles: true}));
                    return true;
                };
                const headings = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,[role="heading"], div, span'))
                    .filter(visible)
                    .filter(h => {
                        const text = norm(h.innerText);
                        return text && text.length < 80;
                    });
                const heading = headings.find(h => {
                    const text = norm(h.innerText);
                    return sectionKeywords.some(k => text === norm(k) || text.includes(norm(k)));
                });
                if (!heading) return false;
                const headingRect = heading.getBoundingClientRect();
                const nextHeading = headings
                    .filter(h => h !== heading)
                    .map(h => ({h, rect: h.getBoundingClientRect(), text: norm(h.innerText)}))
                    .filter(item => item.rect.top > headingRect.top + 8)
                    .filter(item => {
                        const text = item.text;
                        return [
                            'work experience', 'professional experience', 'education', 'education history',
                            'skills', 'resume/cv', 'websites', 'social network', '专业经验', '教育背景', '技能', '网站'
                        ].some(k => text.includes(k));
                    })
                    .sort((a, b) => a.rect.top - b.rect.top)[0];
                const sectionTop = headingRect.top;
                const sectionBottom = nextHeading ? nextHeading.rect.top : Number.POSITIVE_INFINITY;
                const inSection = el => {
                    const rect = el.getBoundingClientRect();
                    return rect.bottom > sectionTop && rect.top < sectionBottom;
                };
                const findItemGroups = () => Array.from(document.querySelectorAll('[role="group"], fieldset, div'))
                    .filter(inSection)
                    .filter(visible)
                    .filter(el => {
                        const text = norm(el.innerText);
                        if (!text) return false;
                        if (kind === 'experience') {
                            return /(work experience|professional experience|专业经验)\\s*\\d+/.test(text)
                                || (text.includes('job title') && text.includes('company'))
                                || (text.includes('职务名称') && text.includes('公司'));
                        }
                        return /(education|education history|教育背景)\\s*\\d+/.test(text)
                            || text.includes('school or university')
                            || text.includes('学校或大学');
                    })
                    .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top)
                    .filter((el, idx, arr) => !arr.slice(0, idx).some(prev => prev.contains(el)));
                const addButton = () => Array.from(document.querySelectorAll('button,[role="button"]'))
                    .filter(inSection)
                    .filter(visible)
                    .filter(btn => /^(add|add another|添加|添加另一个)$/i.test((btn.innerText || btn.getAttribute('aria-label') || '').trim()))
                    .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top)
                    .at(0);
                for (let guard = 0; guard < entries.length + 2 && findItemGroups().length < entries.length; guard++) {
                    const btn = addButton();
                    if (!btn) break;
                    btn.scrollIntoView({block: 'center'});
                    btn.click();
                    await sleep(900);
                }
                const groups = findItemGroups().slice(0, entries.length);
                if (!groups.length) return false;
                for (let i = 0; i < Math.min(groups.length, entries.length); i++) {
                    const group = groups[i];
                    const entry = entries[i];
                    const inputs = Array.from(group.querySelectorAll('input:not([type="hidden"]):not([type="file"]):not([type="checkbox"]), textarea'))
                        .filter(visible)
                        .filter(input => (input.getAttribute('role') || '') !== 'spinbutton')
                        .filter(input => !/search/i.test(input.getAttribute('placeholder') || ''))
                        .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top || a.getBoundingClientRect().left - b.getBoundingClientRect().left);
                    const textInputs = inputs.filter(input => input.tagName !== 'TEXTAREA');
                    const textareas = inputs.filter(input => input.tagName === 'TEXTAREA');
                    const spins = Array.from(group.querySelectorAll('input[role="spinbutton"], input[data-automation-id*="Year"], input[data-automation-id*="Month"]'))
                        .filter(visible)
                        .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top || a.getBoundingClientRect().left - b.getBoundingClientRect().left);
                    const setMonthYearPairs = () => {
                        const months = spins.filter(input => /month/i.test(input.getAttribute('aria-label') || input.getAttribute('data-automation-id') || ''));
                        const years = spins.filter(input => /year/i.test(input.getAttribute('aria-label') || input.getAttribute('data-automation-id') || ''));
                        if (months.length >= 2 && years.length >= 2) {
                            setInput(months[0], entry.start_month);
                            setInput(years[0], entry.start_year);
                            setInput(months[1], entry.end_month);
                            setInput(years[1], entry.end_year);
                        } else {
                            setInput(spins[0], entry.start_month);
                            setInput(spins[1], entry.start_year);
                            setInput(spins[2], entry.end_month);
                            setInput(spins[3], entry.end_year);
                        }
                    };
                    if (kind === 'experience') {
                        setInput(textInputs[0], entry.title);
                        setInput(textInputs[1], entry.company);
                        setInput(textInputs[2], entry.location);
                        setInput(textareas[0], entry.description);
                    } else {
                        setInput(textInputs[0], entry.school);
                        const gpaInput = textInputs.find(input => /gpa|综合成绩/i.test(input.getAttribute('aria-label') || input.placeholder || input.parentElement?.innerText || ''));
                        if (gpaInput && entry.gpa) setInput(gpaInput, entry.gpa);
                        const majorInput = textInputs.find((input, idx) => idx > 0 && input !== gpaInput && /major|field|主修|专业/i.test(input.getAttribute('aria-label') || input.placeholder || input.parentElement?.innerText || ''));
                        if (majorInput) setInput(majorInput, entry.field);
                    }
                    setMonthYearPairs();
                }
                return true;
            }""",
            {"sectionKeywords": section_keywords, "entries": entries, "kind": kind},
        )
        if not result:
            return False
        if kind == "education":
            for entry in entries:
                await _select_workday_dropdown_containing_label(page, "Degree", [entry["degree"], "Masters", "Bachelor", "Other"])
                await _select_workday_dropdown_containing_label(page, "学位", [entry["degree"], "2级学位", "1级学位", "其他"])
        marker = entries[0].get("title") if kind == "experience" else entries[0].get("school")
        if marker:
            try:
                marker_visible = bool(
                    await page.evaluate(
                        """(marker) => {
                            const text = document.body.innerText || '';
                            if (text.includes(marker)) return true;
                            return Array.from(document.querySelectorAll('input, textarea')).some(el => (el.value || '').includes(marker));
                        }""",
                        marker,
                    )
                )
            except Exception:
                marker_visible = False
            if not marker_visible:
                return False
        return True
    except Exception:
        return False


async def _workday_remove_duplicate_uploads(page, *, keep_filenames: list[str]) -> int:
    removed = 0
    for filename in [name for name in keep_filenames if name]:
        try:
            while True:
                clicked = bool(
                    await page.evaluate(
                        """(filename) => {
                            const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                            const norm = text => (text || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                            const target = norm(filename);
                            const occurrences = norm(document.body.innerText || '').split(target).length - 1;
                            if (occurrences <= 2) return false;
                            let buttons = Array.from(document.querySelectorAll('button,[role="button"]'))
                                .filter(visible)
                                .map(btn => {
                                    let scope = btn.parentElement;
                                    let scopeText = '';
                                    for (let depth = 0; scope && depth < 5; depth++, scope = scope.parentElement) {
                                        scopeText = norm(scope.innerText);
                                        if (scopeText.includes(target)) break;
                                    }
                                    return {btn, rect: btn.getBoundingClientRect(), text: norm(btn.innerText || btn.getAttribute('aria-label') || btn.title || ''), scopeText};
                                })
                                .filter(item => item.scopeText.includes(target))
                                .filter(item => /(delete|remove|删除)/.test(item.text) || item.text === '' || item.btn.querySelector('svg'));
                            if (!buttons.length) {
                                const headings = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,[role="heading"], div, span, label')).filter(visible);
                                const uploadHeading = headings
                                  .filter(h => norm(h.innerText).length < 80)
                                  .find(h => {
                                    const text = norm(h.innerText);
                                    return text.includes('resume/cv') || text.includes('upload a file') || text.includes('transcript') || text.includes('上传文件');
                                  });
                                if (uploadHeading) {
                                    const top = uploadHeading.getBoundingClientRect().top;
                                    const next = headings
                                        .map(h => ({h, rect: h.getBoundingClientRect(), text: norm(h.innerText)}))
                                        .filter(item => item.rect.top > top + 8)
                                        .filter(item => ['websites', 'social network', 'application questions', 'skills', 'education', '网站', '社交'].some(k => item.text.includes(k)))
                                        .sort((a, b) => a.rect.top - b.rect.top)[0];
                                    const bottom = next ? next.rect.top : Number.POSITIVE_INFINITY;
                                    buttons = Array.from(document.querySelectorAll('button,[role="button"]'))
                                        .filter(visible)
                                        .map(btn => ({btn, rect: btn.getBoundingClientRect(), text: norm(btn.innerText || btn.getAttribute('aria-label') || btn.title || '')}))
                                        .filter(item => item.rect.top > top && item.rect.top < bottom)
                                        .filter(item => /(delete|remove|删除)/.test(item.text) || item.text === '' || item.btn.querySelector('svg'));
                                }
                            }
                            buttons = buttons.sort((a, b) => a.rect.top - b.rect.top);
                            if (buttons.length <= 1) return false;
                            const item = buttons.at(-1);
                            item.btn.scrollIntoView({block: 'center'});
                            item.btn.click();
                            return true;
                        }""",
                        filename,
                    )
                )
                if not clicked:
                    break
                removed += 1
                await page.wait_for_timeout(1000)
        except Exception:
            continue
    return removed


# `_fill_workday_voluntary_disclosures` is now in
# `job_hunt.services.workday.voluntary_disclosures`. The thin wrapper below
# keeps the existing call sites in `_workday_advance_all_steps` working
# without touching their bodies.
async def _fill_workday_voluntary_disclosures(page, values: dict) -> tuple[list[str], list[str]]:
    from job_hunt.services.workday.voluntary_disclosures import (
        fill_voluntary_disclosures,
    )

    return await fill_voluntary_disclosures(page, values)


async def _wait_for_workday_step(page, step_name: str, *, timeout_ms: int = 10000) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
    while asyncio.get_event_loop().time() < deadline:
        try:
            text = await page.locator("body").inner_text(timeout=2000)
            if step_name in text:
                return True
        except Exception:
            pass
        await page.wait_for_timeout(500)
    return False


async def _wait_for_workday_step_change(page, previous_step: str, *, timeout_ms: int = 10000) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
    while asyncio.get_event_loop().time() < deadline:
        try:
            current = await _workday_current_step(page)
            if current and current != previous_step:
                return True
        except Exception:
            pass
        await page.wait_for_timeout(750)
    return False


async def _run_workday_question_ops(
    page, values: dict[str, str], ops: list[dict]
) -> tuple[list[str], list[str]]:
    """Drive a list of employer-config ops via the extracted dispatcher.

    Phase 2.1: dispatcher logic lives in
    ``job_hunt.services.workday.application_questions.run_question_ops``; this
    wrapper just injects the cli-level Playwright helpers.
    """
    return await _run_workday_question_ops_from_module(
        page,
        values,
        ops,
        by_label=_select_workday_dropdown_by_label,
        in_question=_select_workday_dropdown_in_question,
        containing_label=_select_workday_dropdown_containing_label,
        by_index=_select_workday_dropdown_by_index,
        fill_text=_fill_workday_input_in_question,
        fill_date=_fill_workday_date_input,
        short=_short,
    )


async def _fill_workday_application_questions(
    page,
    values: dict[str, str],
    *,
    company: str | None = None,
    role: str | None = None,
    report_context: dict | None = None,
) -> tuple[list[str], list[str], list[dict[str, str]]]:
    filled: list[str] = []
    skipped: list[str] = []
    answers: list[dict[str, str]] = []

    config_name, config = _select_workday_employer_config(page.url)
    if config_name == "<embedded-fallback>":
        skipped.append(
            "Workday employer config: no matching yaml under profile/workday-employers/, "
            "using embedded generic fallback."
        )
    elif config_name.startswith("_default"):
        skipped.append(
            f"Workday employer config: no employer-specific yaml matched {page.url!r}; "
            f"using {config_name}. Add profile/workday-employers/<slug>.yml to automate this employer."
        )

    f, s = await _run_workday_question_ops(page, values, config.get("ops") or [])
    filled.extend(f)
    skipped.extend(s)

    # --- Free-form textarea / rich-text answers (LLM/saved-answer fallback) ---
    f2, s2, a2 = await _fill_workday_textarea_answers(
        page, company=company, role=role, report_context=report_context
    )
    filled.extend(f2)
    skipped.extend(s2)
    answers.extend(a2)

    # --- Transcript upload ---
    transcript = values.get("transcript_pdf", "")
    if not transcript:
        for candidate in Path("storage/private").glob("workday-transcript.*"):
            if candidate.is_file():
                transcript = str(candidate)
                break
    if transcript and Path(transcript).exists():
        transcript_name = Path(transcript).name
        removed = await _workday_remove_duplicate_uploads(page, keep_filenames=[transcript_name])
        if removed:
            filled.append(f"Workday removed duplicate transcript upload(s): {removed}")
        try:
            page_text = await page.locator("body").inner_text(timeout=3000)
        except Exception:
            page_text = ""
        uploaded = transcript_name in page_text or (
            "unofficial transcript" in page_text.lower() and "Successfully Uploaded" in page_text
        )
        if not uploaded:
            transcript_inputs = page.locator("input[type=file]")
            for i in range(await transcript_inputs.count()):
                try:
                    await transcript_inputs.nth(i).set_input_files(transcript)
                    await _finish_pending_upload_dialog(page)
                    uploaded = True
                    break
                except Exception:
                    continue
            removed = await _workday_remove_duplicate_uploads(page, keep_filenames=[transcript_name])
            if removed:
                filled.append(f"Workday removed duplicate transcript upload(s): {removed}")
        if uploaded:
            filled.append(f"Workday transcript uploaded: {Path(transcript).name}")
        else:
            skipped.append("Workday transcript: file found but upload failed — needs manual upload.")
    else:
        skipped.append(
            "Workday transcript: no transcript_pdf configured in profile — needs manual upload. "
            "Set transcript_pdf in profile/profile.yml to automate."
        )

    return filled, skipped, answers


async def _fill_workday_textarea_answers(
    page,
    *,
    company: str | None,
    role: str | None,
    report_context: dict | None,
) -> tuple[list[str], list[str], list[dict[str, str]]]:
    """Fill free-form textareas / rich-text inputs on the Workday Application Questions step.

    Reuses ``_answer_for_application_question`` which already prefers saved answers from
    ``apply-review.json`` over canned/report-derived prose, so re-runs of the same artifact
    dir reuse last session's answer instead of regenerating it.
    """
    filled: list[str] = []
    skipped: list[str] = []
    answers: list[dict[str, str]] = []

    textareas = page.locator("textarea")
    try:
        textarea_count = await textareas.count()
    except Exception:
        textarea_count = 0
    for index in range(textarea_count):
        area = textareas.nth(index)
        try:
            if not await area.is_visible():
                continue
            if (await area.input_value()).strip():
                continue  # already has content
            question = await _field_context(area)
        except Exception:
            continue
        answer = _answer_for_application_question(
            question, company=company, role=role, report_context=report_context
        )
        if not answer or not question:
            if question:
                skipped.append(_short(f"Workday textarea: {question} (no auto-answer)", 140))
            continue
        try:
            await area.fill(answer)
        except Exception:
            skipped.append(_short(f"Workday textarea: {question} (fill failed)", 140))
            continue
        if await _field_contains_text(area, answer):
            filled.append(_short(f"Workday textarea: {question}", 100))
            answers.append({"question": question, "answer": answer})
        else:
            skipped.append(_short(f"Workday textarea: {question} (fill did not persist)", 140))

    rich_textboxes = page.locator('[role="textbox"][contenteditable="plaintext-only"]')
    try:
        rich_count = await rich_textboxes.count()
    except Exception:
        rich_count = 0
    for index in range(rich_count):
        box = rich_textboxes.nth(index)
        try:
            if not await box.is_visible():
                continue
            if (await box.inner_text()).strip():
                continue
            question = await _field_context(box)
        except Exception:
            continue
        answer = _answer_for_application_question(
            question, company=company, role=role, report_context=report_context
        )
        if not answer or not question:
            if question:
                skipped.append(_short(f"Workday rich text: {question} (no auto-answer)", 140))
            continue
        if await _fill_contenteditable(box, answer):
            filled.append(_short(f"Workday rich text: {question}", 100))
            answers.append({"question": question, "answer": answer})
        else:
            skipped.append(_short(f"Workday rich text: {question} (fill did not persist)", 140))

    return filled, skipped, answers


async def _select_workday_dropdown_by_index(page, index: int, choices: list[str]) -> bool:
    try:
        buttons = page.locator('button').filter(has_text=re.compile(r"Select One|Other|Yes|No|Canada|Ontario|Mobile", re.IGNORECASE))
        if await buttons.count() <= index:
            buttons = page.locator('button[aria-haspopup="listbox"], button[aria-haspopup="true"], [role="combobox"]')
        if await buttons.count() <= index:
            return False
        button = buttons.nth(index)
        text = (await button.inner_text(timeout=2000)).strip()
        if any(choice.lower() in text.lower() for choice in choices):
            return True
        await button.scroll_into_view_if_needed(timeout=5000)
        await button.click(timeout=5000)
        return await _choose_workday_option(page, choices)
    except Exception:
        return False


async def _fill_workday_date_input(page, value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 8:
        month, day, year = digits[:2], digits[2:4], digits[4:8]
        try:
            filled_segments = bool(
                await page.evaluate(
                    """({month, day, year}) => {
                        const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                        const norm = text => (text || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                        const setSpin = (input, val) => {
                            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                            if (setter) setter.call(input, String(Number(val)));
                            else input.value = String(Number(val));
                            input.setAttribute('aria-valuenow', String(Number(val)));
                            input.setAttribute('aria-valuetext', String(Number(val)));
                            input.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: String(Number(val))}));
                            input.dispatchEvent(new Event('change', {bubbles: true}));
                            input.dispatchEvent(new Event('blur', {bubbles: true}));
                        };
                        const fields = Array.from(document.querySelectorAll('input')).filter(visible);
                        let scope = null;
                        for (const f of fields) {
                            let s = f.parentElement;
                            for (let d = 0; s && d < 9; d++, s = s.parentElement) {
                                if (norm(s.innerText).includes('expected graduation date')) {
                                    scope = s;
                                    break;
                                }
                            }
                            if (scope) break;
                        }
                        if (!scope) return false;
                        const monthInput = scope.querySelector('[data-automation-id="dateSectionMonth-input"], input[aria-label="Month"]');
                        const dayInput = scope.querySelector('[data-automation-id="dateSectionDay-input"], input[aria-label="Day"]');
                        const yearInput = scope.querySelector('[data-automation-id="dateSectionYear-input"], input[aria-label="Year"]');
                        if (!monthInput || !dayInput || !yearInput) return false;
                        setSpin(monthInput, month);
                        setSpin(dayInput, day);
                        setSpin(yearInput, year);
                        yearInput.focus();
                        yearInput.blur();
                        return true;
                    }""",
                    {"month": month, "day": day, "year": year},
                )
            )
            if filled_segments:
                await page.wait_for_timeout(800)
                return True
        except Exception:
            pass
    try:
        element = await _workday_element_in_question(page, "expected graduation date", "input")
        if element:
            await element.click(timeout=3000)
            await element.press("ControlOrMeta+A")
            await element.press("Backspace")
            await element.press_sequentially(digits or value, delay=60)
            await element.press("Tab")
            await page.wait_for_timeout(800)
            return True
    except Exception:
        pass
    try:
        fields = page.locator('input[placeholder="MM/DD/YYYY"], input[aria-label*="date" i]')
        for index in range(await fields.count()):
            field = fields.nth(index)
            if not await field.is_visible():
                continue
            await field.click(timeout=3000)
            await field.press("ControlOrMeta+A")
            await field.press("Backspace")
            await field.press_sequentially(digits or value, delay=60)
            await field.press("Tab")
            await page.wait_for_timeout(800)
            return True
    except Exception:
        return False
    return False


async def _fill_workday_input_in_question(page, label_fragment: str, value: str, force: bool = False) -> bool:
    """Fill a Workday input/textarea scoped to the question containing label_fragment.

    Workday question pages often have long labels and validation text in the same
    section. This keeps matching near a single visible question block so values
    like program, graduation date, and GPA do not leak into neighboring fields.
    """
    if not value:
        return False
    try:
        element = await _workday_element_in_question(page, label_fragment, "input, textarea")
        if element:
            current = ""
            try:
                current = await element.input_value()
            except Exception:
                pass
            if force or not current.strip():
                await element.click(timeout=3000)
                await element.press("ControlOrMeta+A")
                await element.press("Backspace")
                await element.press_sequentially(value, delay=0)
                await element.press("Tab")
                await page.wait_for_timeout(500)
            return True
    except Exception:
        pass
    try:
        return bool(
            await page.evaluate(
                """({fragment, value, force}) => {
                    const wanted = (fragment || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    const norm = text => (text || '').replace(/\\*/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    const setValue = (field, val) => {
                        const proto = field.tagName === 'TEXTAREA'
                            ? window.HTMLTextAreaElement.prototype
                            : window.HTMLInputElement.prototype;
                        const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                        if (setter) setter.call(field, val);
                        else field.value = val;
                        field.dispatchEvent(new Event('input', {bubbles: true}));
                        field.dispatchEvent(new Event('change', {bubbles: true}));
                        field.dispatchEvent(new Event('blur', {bubbles: true}));
                    };
                    const fieldSelector = 'input:not([type=hidden]):not([type=file]):not([type=radio]):not([type=checkbox]), textarea';
                    const nodes = Array.from(document.querySelectorAll('label, p, div, span'))
                        .filter(el => {
                            if (!visible(el)) return false;
                            const text = norm(el.innerText);
                            if (!text.includes(wanted)) return false;
                            if (text.startsWith('error -') || text.startsWith('the field ')) return false;
                            return text.length <= 1400;
                        })
                        .sort((a, b) => {
                            const ar = a.getBoundingClientRect();
                            const br = b.getBoundingClientRect();
                            return ar.top - br.top;
                        });
                    for (const node of nodes) {
                        let scope = node.parentElement;
                        for (let depth = 0; scope && depth < 6; depth++, scope = scope.parentElement) {
                            const scopeText = norm(scope.innerText);
                            if (!scopeText.includes(wanted)) continue;
                            const fields = Array.from(scope.querySelectorAll(fieldSelector)).filter(visible);
                            const usable = fields.filter(field => {
                                const type = (field.getAttribute('type') || '').toLowerCase();
                                if (['button', 'submit'].includes(type)) return false;
                                if (!force && String(field.value || '').trim()) return false;
                                return true;
                            });
                            if (usable.length === 1) {
                                setValue(usable[0], value);
                                return true;
                            }
                        }

                        // Fallback: use the first visible field below the question
                        // and before the next label-ish block that contains another question.
                        const nodeBottom = node.getBoundingClientRect().bottom + window.scrollY;
                        const fieldsBelow = Array.from(document.querySelectorAll(fieldSelector))
                            .filter(visible)
                            .map(field => ({field, top: field.getBoundingClientRect().top + window.scrollY}))
                            .filter(item => item.top >= nodeBottom - 8 && item.top - nodeBottom < 280)
                            .sort((a, b) => a.top - b.top);
                        for (const {field} of fieldsBelow) {
                            if (!force && String(field.value || '').trim()) continue;
                            setValue(field, value);
                            return true;
                        }
                    }
                    return false;
                }""",
                {"fragment": label_fragment, "value": value, "force": force},
            )
        )
    except Exception:
        return False


async def _workday_element_in_question(page, label_fragment: str, selector: str):
    try:
        handle = await page.evaluate_handle(
            """({fragment, selector}) => {
                const wanted = (fragment || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                const norm = text => (text || '').replace(/\\*/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const nodes = Array.from(document.querySelectorAll('label, p, div, span'))
                    .filter(el => {
                        if (!visible(el)) return false;
                        const text = norm(el.innerText);
                        if (!text.includes(wanted)) return false;
                        if (text.startsWith('error -') || text.startsWith('the field ')) return false;
                        return text.length <= 1400;
                    })
                    .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
                for (const node of nodes) {
                    let scope = node.parentElement;
                    for (let depth = 0; scope && depth < 7; depth++, scope = scope.parentElement) {
                        const scopeText = norm(scope.innerText);
                        if (!scopeText.includes(wanted)) continue;
                        const fields = Array.from(scope.querySelectorAll(selector)).filter(visible);
                        if (fields.length === 1) return fields[0];
                    }
                    const nodeBottom = node.getBoundingClientRect().bottom + window.scrollY;
                    const fieldsBelow = Array.from(document.querySelectorAll(selector))
                        .filter(visible)
                        .map(field => ({field, top: field.getBoundingClientRect().top + window.scrollY}))
                        .filter(item => item.top >= nodeBottom - 8 && item.top - nodeBottom < 320)
                        .sort((a, b) => a.top - b.top);
                    if (fieldsBelow.length) return fieldsBelow[0].field;
                }
                return null;
            }""",
            {"fragment": label_fragment, "selector": selector},
        )
        return handle.as_element()
    except Exception:
        return None


async def _fill_first_visible_textarea(page, value: str) -> bool:
    try:
        areas = page.locator("textarea")
        for index in range(await areas.count()):
            area = areas.nth(index)
            if not await area.is_visible():
                continue
            current = await area.input_value()
            if not current:
                await area.fill(value)
                await area.blur(timeout=2000)
                return True
    except Exception:
        return False
    return False


async def _fill_workday_field_containing(page, needle: str, value: str, force: bool = False) -> bool:
    """Fill the first visible text/textarea input whose surrounding text contains needle.

    By default skips fields that already have a value. Pass force=True to overwrite.
    """
    if not value:
        return False
    try:
        return bool(
            await page.evaluate(
                """({needle, value, force}) => {
                    const wanted = (needle || '').toLowerCase();
                    const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    const setValue = (input, val) => {
                        const proto = input.tagName === 'TEXTAREA'
                            ? window.HTMLTextAreaElement.prototype
                            : window.HTMLInputElement.prototype;
                        const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                        if (setter) setter.call(input, val);
                        else input.value = val;
                        input.dispatchEvent(new Event('input', {bubbles: true}));
                        input.dispatchEvent(new Event('change', {bubbles: true}));
                        input.dispatchEvent(new Event('blur', {bubbles: true}));
                    };
                    // When force=true, limit ancestor depth to 3 so we don't accidentally
                    // match a far-away question's text and overwrite an unrelated field.
                    const maxDepth = force ? 3 : 7;
                    const fields = Array.from(document.querySelectorAll('input:not([type=hidden]):not([type=file]):not([type=radio]):not([type=checkbox]), textarea'))
                        .filter(visible);
                    for (const field of fields) {
                        let scope = field.parentElement;
                        for (let depth = 0; scope && depth < maxDepth; depth++, scope = scope.parentElement) {
                            const text = (scope.innerText || '').toLowerCase();
                            if (text.includes(wanted)) {
                                if (force || !String(field.value || '').trim()) setValue(field, value);
                                return true;
                            }
                        }
                    }
                    return false;
                }""",
                {"needle": needle, "value": value, "force": force},
            )
        )
    except Exception:
        return False


async def _scroll_application_form(page) -> None:
    try:
        height = await page.evaluate("() => Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)")
        for y in range(0, int(height) + 900, 700):
            await page.evaluate("(y) => window.scrollTo(0, y)", y)
            await page.wait_for_timeout(200)
        await page.evaluate("() => window.scrollTo(0, 0)")
        await page.wait_for_timeout(300)
    except Exception:
        pass


async def _fill_by_label_or_placeholder(page, label: str, value: str) -> bool:
    if not value:
        return False
    candidates = [
        page.get_by_label(label, exact=False),
        page.get_by_placeholder(label, exact=False),
        page.locator(f'input[name*="{label.lower()}"]'),
    ]
    for locator in candidates:
        try:
            if await locator.count():
                field = locator.first
                tag = await field.evaluate("el => el.tagName.toLowerCase()")
                field_type = (await field.get_attribute("type") or "").lower()
                if tag in {"input", "textarea"} and field_type not in {"hidden", "file", "radio", "checkbox", "submit"}:
                    # Workday and similar ATS pages may render visible anti-bot fields
                    # such as "Website. This input is for robots only"; filling those
                    # can make account creation silently fail.
                    context = await _field_context(field)
                    if _looks_like_honeypot_context(context):
                        continue
                    current = await field.input_value()
                    if not current:
                        await field.fill(value)
                    return True
        except Exception:
            continue
    return await _fill_by_visible_label(page, label, value)


async def _fill_contenteditable(locator, value: str) -> bool:
    try:
        await locator.fill(value, timeout=5000)
        await locator.blur(timeout=2000)
        if await _field_contains_text(locator, value):
            return True
    except Exception:
        pass
    try:
        await locator.click(timeout=5000)
        await locator.press("ControlOrMeta+A")
        await locator.press("Backspace")
        await locator.press_sequentially(value, delay=0)
        await locator.blur(timeout=2000)
        if await _field_contains_text(locator, value):
            return True
    except Exception:
        pass
    try:
        await locator.evaluate(
            """(el, value) => {
                el.focus();
                document.execCommand('selectAll', false, null);
                document.execCommand('insertText', false, value);
                el.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: value}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new Event('blur', {bubbles: true}));
            }""",
            value,
        )
    except Exception:
        return False
    return await _field_contains_text(locator, value)


async def _field_contains_text(locator, expected: str) -> bool:
    expected_sample = re.sub(r"\s+", " ", expected).strip()[:80]
    if not expected_sample:
        return True
    try:
        actual = await locator.evaluate(
            """el => {
                const value = el.value || el.innerText || el.textContent || '';
                return String(value).replace(/\\s+/g, ' ').trim();
            }"""
        )
    except Exception:
        return False
    return expected_sample in actual


async def _fill_by_visible_label(page, label: str, value: str) -> bool:
    return bool(
        await page.evaluate(
            """({label, value}) => {
                const wanted = label.toLowerCase();
                const setValue = (input, val) => {
                    const proto = input.tagName === 'TEXTAREA'
                        ? window.HTMLTextAreaElement.prototype
                        : window.HTMLInputElement.prototype;
                    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                    if (setter) setter.call(input, val);
                    else input.value = val;
                    input.dispatchEvent(new Event('input', {bubbles: true}));
                    input.dispatchEvent(new Event('change', {bubbles: true}));
                    input.dispatchEvent(new Event('blur', {bubbles: true}));
                };
                const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                const normalize = text => (text || '').trim().toLowerCase().replace(/\\*/g, '').replace(/\\s+/g, ' ');
                const honeypot = text => /robots only|do not enter|leave.*blank|website\\. this input/i.test(text || '');
                const allInputs = Array.from(document.querySelectorAll('input:not([type=hidden]):not([type=file]):not([type=radio]):not([type=checkbox]), textarea'));
                const nodes = Array.from(document.querySelectorAll('label'))
                    .filter(el => visible(el) && normalize(el.innerText) === wanted);
                for (const node of nodes) {
                    const scopeText = normalize((node.closest('div') || node.parentElement || node).innerText);
                    if (honeypot(scopeText)) continue;
                    const scope = node.parentElement || node.closest('div');
                    const input = scope?.querySelector('input:not([type=hidden]):not([type=file]):not([type=radio]):not([type=checkbox]), textarea');
                    if (input) {
                        setValue(input, value);
                        return true;
                    }
                    const nodePosition = node.compareDocumentPosition.bind(node);
                    const nextInput = allInputs.find(input => nodePosition(input) & Node.DOCUMENT_POSITION_FOLLOWING);
                    if (nextInput) {
                        setValue(nextInput, value);
                        return true;
                    }
                    let sib = node.nextElementSibling;
                    for (let i = 0; sib && i < 4; i++, sib = sib.nextElementSibling) {
                        const nextInput = sib.querySelector?.('input:not([type=hidden]):not([type=file]):not([type=radio]):not([type=checkbox]), textarea');
                        if (nextInput) {
                            setValue(nextInput, value);
                            return true;
                        }
                    }
                }
                return false;
            }""",
            {"label": label, "value": value},
        )
    )


def _looks_like_honeypot_context(context: str) -> bool:
    value = re.sub(r"\s+", " ", (context or "").lower())
    return any(
        signal in value
        for signal in [
            "robots only",
            "do not enter if you're human",
            "do not enter if you are human",
            "leave this field blank",
            "this input is for robots",
        ]
    )


async def _fill_location(page, value: str) -> bool:
    if not value:
        return False
    filled = await _fill_by_label_or_placeholder(page, "Location", value)
    if not filled:
        return False

    # Ashby uses an async combobox for candidate location. If options appear,
    # select the first matching one so the text is committed as a real choice.
    try:
        await page.wait_for_timeout(1500)
        option = page.get_by_role("option").filter(has_text=value.split(",")[0]).first
        if await option.count():
            await option.click(timeout=3000)
    except Exception:
        pass
    return True


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


async def _required_empty_fields(page) -> list[str]:
    try:
        return await page.evaluate(
            """() => {
                const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                const normalize = text => (text || '').replace(/\\s+/g, ' ').trim();
                const labelFor = el => {
                    const id = el.getAttribute('id');
                    if (id) {
                        const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
                        if (label) return normalize(label.innerText);
                    }
                    const label = el.closest('label');
                    if (label) return normalize(label.innerText);
                    const aria = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('name');
                    if (aria) return normalize(aria);
                    const wrap = el.closest('div');
                    if (wrap) {
                        const text = normalize(wrap.innerText).split('\\n')[0];
                        if (text) return text;
                    }
                    const targetTop = el.getBoundingClientRect().top + window.scrollY;
                    const candidates = Array.from(document.querySelectorAll('div, label, p, span'))
                        .filter(node => node !== el && visible(node))
                        .map(node => {
                            const rect = node.getBoundingClientRect();
                            return {text: normalize(node.innerText), bottom: rect.bottom + window.scrollY};
                        })
                        .filter(item => item.text && item.text.length >= 12 && item.text.length <= 360)
                        .filter(item => item.bottom <= targetTop + 6 && targetTop - item.bottom < 320)
                        .sort((a, b) => b.bottom - a.bottom);
                    const question = candidates.find(item => item.text.includes('*') || item.text.startsWith('(Optional)') || item.text.includes('?'));
                    if (question) return question.text;
                    return el.tagName.toLowerCase();
                };
                const required = Array.from(document.querySelectorAll('input, textarea, select, [role="textbox"][contenteditable="plaintext-only"]'))
                    .filter(el => visible(el))
                    .filter(el => {
                        const type = (el.getAttribute('type') || '').toLowerCase();
                        if (['hidden', 'submit', 'button'].includes(type)) return false;
                        return el.required || el.getAttribute('aria-required') === 'true' || labelFor(el).includes('*');
                    });
                const empty = required.filter(el => {
                    const type = (el.getAttribute('type') || '').toLowerCase();
                    if (type === 'file') return !el.files || el.files.length === 0;
                    if (type === 'checkbox' || type === 'radio') {
                        const name = el.getAttribute('name');
                        if (!name) return !el.checked;
                        return !document.querySelector(`input[name="${CSS.escape(name)}"]:checked`);
                    }
                    return !String(el.value || el.innerText || el.textContent || '').trim();
                });
                return Array.from(new Set(empty.map(labelFor).filter(Boolean))).slice(0, 30);
            }"""
        )
    except Exception:
        return []


async def _field_context(locator) -> str:
    try:
        context = await locator.evaluate(
            """el => {
                const bits = [];
                const label = el.closest('label');
                if (label) bits.push(label.innerText);
                const wrap = el.closest('div');
                if (wrap) bits.push(wrap.innerText);
                if (el.getAttribute('aria-label')) bits.push(el.getAttribute('aria-label'));
                if (el.getAttribute('placeholder')) bits.push(el.getAttribute('placeholder'));
                return bits.join(' ').replace(/\\s+/g, ' ').trim();
            }"""
        )
        if context and len(context) <= 500:
            return context
    except Exception:
        pass
    try:
        return await locator.evaluate(
            """el => {
                const normalize = text => (text || '').replace(/\\s+/g, ' ').trim();
                const visible = node => {
                    const rect = node.getBoundingClientRect();
                    const style = window.getComputedStyle(node);
                    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                };
                const targetTop = el.getBoundingClientRect().top + window.scrollY;
                const candidates = Array.from(document.querySelectorAll('div, label, p, span'))
                    .filter(node => node !== el && visible(node))
                    .map(node => {
                        const rect = node.getBoundingClientRect();
                        return {
                            text: normalize(node.innerText),
                            bottom: rect.bottom + window.scrollY,
                        };
                    })
                    .filter(item => item.text && item.text.length >= 12 && item.text.length <= 360)
                    .filter(item => item.bottom <= targetTop + 6 && targetTop - item.bottom < 320)
                    .sort((a, b) => b.bottom - a.bottom);
                const question = candidates.find(item => item.text.includes('*') || item.text.startsWith('(Optional)') || item.text.includes('?'));
                return question ? question.text : (candidates[0]?.text || '');
            }"""
        )
    except Exception:
        return ""


async def _click_radio_near_text(page, name: str, choice: str) -> bool:
    escaped = name.replace('"', '\\"')
    radios = page.locator(f'input[type="radio"][name="{escaped}"]')
    for index in range(await radios.count()):
        radio = radios.nth(index)
        context = (await _field_context(radio)).lower()
        if choice.lower() in context:
            try:
                await radio.check(force=True)
                return True
            except Exception:
                try:
                    await radio.click(force=True)
                    return True
                except Exception:
                    return False
    return False


def _apply_artifact_dir(company: str | None, role: str | None) -> Path:
    """Return a per-application artifact directory under artifacts/apply/."""
    import re as _re
    today = datetime.now().date().isoformat()
    def _slug(s: str, max_len: int) -> str:
        return _re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:max_len]
    parts = [today]
    if company:
        parts.append(_slug(company, 25))
    if role:
        parts.append(_slug(" ".join(role.split()[:4]), 30))
    return Path("artifacts/apply") / "-".join(parts)


def _apply_profile_values() -> dict[str, object]:
    values = {
        "name": "Example Candidate",
        "first_name": "Example",
        "last_name": "Candidate",
        "email": "candidate@example.com",
        "phone": "555-0100",
        "linkedin": "https://www.linkedin.com/in/example-candidate/",
        "github": "https://github.com/example-candidate",
        "portfolio": "https://candidate.example.com",
        "location": "City, Region, Country",
        "country": "",
        "address": "123 Example Street",
        "city": "",
        "province": "",
        "postal_code": "",
        "phone_device_type": "Mobile",
        "source": "Company Website",
        "full_time_start": "",
        "graduation_date": "",
        "gpa_4_scale": "",
        # Co-op eligibility fields (used by Workday co-op forms)
        "cowork_eligibility_category": "",
        "cowork_eligibility_description": "",
        # Path to an unofficial transcript PDF for Workday upload; leave empty to skip
        "transcript_pdf": "",
        # Academic distinction/award proof is not a transcript. Use it only for
        # fields asking for honors or academic-achievement proof.
        "academic_distinction_pdf": "",
        # Legal/terms consent is never assumed. Set explicitly in profile.yml or
        # create storage/private/workday-consent-terms after the user approves.
        "workday_consent_terms_and_conditions": False,
        # Auto-submit profile gate. CLI ``--auto-submit`` is only honoured when
        # ``apply.auto_submit_enabled: true`` is also set in profile.yml.
        "apply_auto_submit_enabled": False,
        # Default to producing a one-page cover letter PDF on every evaluate run.
        # CLI ``--cover-letter`` overrides per-run.
        "apply_cover_letter_default": False,
    }
    profile_path = Path("profile/profile.yml")
    if not profile_path.exists():
        return values
    try:
        raw = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return values
    candidate = raw.get("candidate") or raw
    location = raw.get("location") or {}
    cowork = raw.get("cowork") or {}
    workday = raw.get("workday") or {}
    apply_section = raw.get("apply") or {}
    values.update(
        {
            "name": candidate.get("full_name") or candidate.get("name") or values["name"],
            "email": candidate.get("email") or values["email"],
            "phone": candidate.get("phone") or values["phone"],
            "linkedin": candidate.get("linkedin") or values["linkedin"],
            "github": candidate.get("github") or values["github"],
            "portfolio": candidate.get("portfolio_url") or candidate.get("website") or values["portfolio"],
            "location": candidate.get("location") or values["location"],
            "country": location.get("country") or values["country"],
            "address": location.get("address") or values["address"],
            "city": location.get("city") or values["city"],
            "province": location.get("province") or values["province"],
            "postal_code": location.get("postal_code") or values["postal_code"],
            "cowork_eligibility_category": cowork.get("eligibility_category") or values["cowork_eligibility_category"],
            "cowork_eligibility_description": cowork.get("eligibility_description") or values["cowork_eligibility_description"],
            "gpa_4_scale": cowork.get("gpa_4_scale")
            or candidate.get("gpa_4_scale")
            or values["gpa_4_scale"],
            "graduation_date": cowork.get("graduation_date")
            or candidate.get("graduation_date")
            or values["graduation_date"],
            "full_time_start": apply_section.get("full_time_start")
            or candidate.get("full_time_start")
            or values["full_time_start"],
            "transcript_pdf": cowork.get("transcript_pdf") or candidate.get("transcript_pdf") or values["transcript_pdf"],
            "academic_distinction_pdf": cowork.get("academic_distinction_pdf")
            or candidate.get("academic_distinction_pdf")
            or values.get("academic_distinction_pdf", ""),
            "workday_consent_terms_and_conditions": bool(
                workday.get("consent_terms_and_conditions")
                or candidate.get("workday_consent_terms_and_conditions")
            ),
            "apply_auto_submit_enabled": bool(
                apply_section.get("auto_submit_enabled")
            ),
            "apply_cover_letter_default": bool(
                apply_section.get("cover_letter_default")
            ),
        }
    )
    name_parts = values["name"].split()
    if name_parts:
        values["first_name"] = candidate.get("first_name") or name_parts[0]
        values["last_name"] = candidate.get("last_name") or " ".join(name_parts[1:]) or values["last_name"]
    return values


_LOW_SCORE_GATE_THRESHOLD = 4.0

# Bumped when apply-review.json fields are renamed, removed, or change semantics.
# Additive fields do NOT require a bump. Downstream tooling (`jq`, dashboards)
# can read this to decide whether to apply migration logic.
APPLY_REVIEW_SCHEMA_VERSION = 1


def _enforce_low_score_gate(report_context: dict | None, *, override: bool) -> None:
    """Abort the apply flow if the matched tracker row has weighted_total < 4.0.

    Per `prompts/shared.md` Ethical Use rules, applying to a low-score role costs
    recruiter attention. The gate fires only when (a) we have a tracker match
    with a parseable score and (b) that score is below the threshold. When no
    score is available (manual cases, fresh tracker rows, "N/A" / "DUP"), the
    gate stays silent rather than blocking legitimate manual workflows.
    """
    if not report_context:
        return
    score_str = (report_context.get("score") or "").strip()
    match = re.match(r"^([\d.]+)/5", score_str)
    if not match:
        return
    score = float(match.group(1))
    if score >= _LOW_SCORE_GATE_THRESHOLD:
        return
    if override:
        console.print(
            f"[yellow]warning:[/yellow] tracker score {score}/5 < "
            f"{_LOW_SCORE_GATE_THRESHOLD} — applying anyway (--low-score-override)."
        )
        return
    console.print(
        f"[red]Aborting:[/red] tracker score {score}/5 is below the ethical-use "
        f"threshold of {_LOW_SCORE_GATE_THRESHOLD}/5.\n"
        f"Recruiter time has cost. Re-evaluate with `job-hunt evaluate`, or pass "
        f"`--low-score-override` if you have a specific reason to apply anyway."
    )
    raise typer.Exit(1)


def _load_apply_report_context(
    *,
    tracker: TrackerRepository,
    tracker_entry,
    company: str | None,
    role: str | None,
) -> dict | None:
    entry = tracker_entry
    score = 1.0 if entry else 0.0
    if entry is None:
        entry, score = tracker.find_match(company=company, role=role)
    if not entry or score < 0.70:
        return None

    report_path = _resolve_report_path(entry.report)
    if not report_path or not report_path.exists():
        return {
            "tracker_id": entry.number,
            "company": entry.company,
            "role": entry.role,
            "score": entry.score,
            "status": entry.status,
            "path": "",
            "application_section": "",
        }
    text = report_path.read_text(encoding="utf-8")
    return {
        "tracker_id": entry.number,
        "company": entry.company,
        "role": entry.role,
        "score": entry.score,
        "status": entry.status,
        "path": str(report_path),
        "recommendation": _extract_report_recommendation(text),
        "application_section": _extract_application_section(text),
    }


def _resolve_report_path(report_ref: str) -> Path | None:
    if not report_ref:
        return None
    match = re.search(r"\((reports/[^)]+)\)", report_ref)
    raw = match.group(1) if match else report_ref.strip()
    raw = raw.strip("[]")
    if raw.startswith("manual:"):
        return None
    path = Path(raw)
    if path.exists():
        return path
    if not raw.startswith("reports/") and raw.endswith(".md"):
        path = Path("reports") / raw
        if path.exists():
            return path
    return None


def _extract_application_section(report_text: str) -> str:
    headings = [
        r"section g",
        r"application answers",
        r"application framing",
        r"draft answers",
        r"key talking points",
        r"application requirements",
    ]
    pattern = re.compile(rf"^##+\s+.*({'|'.join(headings)}).*$", re.IGNORECASE | re.MULTILINE)
    match = pattern.search(report_text)
    if not match:
        return ""
    start = match.start()
    next_heading = re.search(r"^##\s+", report_text[match.end():], re.MULTILINE)
    end = match.end() + next_heading.start() if next_heading else len(report_text)
    return report_text[start:end].strip()[:6000]


def _extract_report_recommendation(report_text: str) -> str:
    match = re.search(r"Recommendation\*\*:\s*([A-Za-z]+)", report_text)
    return match.group(1).upper() if match else ""


def _report_fit_warnings(report_context: dict | None) -> list[str]:
    if not report_context:
        return []
    warnings = []
    score_text = report_context.get("score") or ""
    recommendation = (report_context.get("recommendation") or "").upper()
    score_match = re.search(r"(\d+(?:\.\d+)?)\s*/\s*5", score_text)
    if recommendation == "SKIP":
        warnings.append("Matched report recommendation is SKIP; confirm with the user before applying.")
    if score_match and float(score_match.group(1)) < 3.0:
        warnings.append(f"Matched report score is low ({score_text}); treat this as a review blocker.")
    return warnings


def _find_report_answer(question: str, report_context: dict | None) -> str:
    if not report_context:
        return ""
    section = report_context.get("application_section") or ""
    if not section:
        return ""
    q = question.lower()
    candidates: list[str] = []
    if "why" in q:
        candidates = _section_blocks_matching(section, ["why", "role", "company"])
    elif "additional" in q or "anything else" in q or "other information" in q:
        candidates = _section_blocks_matching(section, ["additional", "talking points", "application"])
    elif "fit" in q or "great" in q:
        candidates = _section_blocks_matching(section, ["fit", "good fit", "great fit"])
    elif "achievement" in q or "experience" in q or "background" in q or "relevant" in q:
        candidates = _section_blocks_matching(section, ["achievement", "experience", "relevant"])
    if not candidates:
        return ""
    answer = _clean_report_answer(candidates[0])
    return answer[:1800]


def _find_saved_apply_answer(question: str, report_context: dict | None) -> str:
    if not report_context:
        return ""
    saved = report_context.get("saved_answers") or []
    if not saved:
        return ""
    from rapidfuzz import fuzz

    question_norm = _normalize_question(question)
    if not question_norm:
        return ""
    best_answer = ""
    best_score = 0.0
    for item in saved:
        candidate_q = _normalize_question(str(item.get("question") or ""))
        answer = str(item.get("answer") or "").strip()
        if not candidate_q or not answer:
            continue
        score = fuzz.token_set_ratio(question_norm, candidate_q) / 100
        if score > best_score:
            best_score = score
            best_answer = answer
    return best_answer if best_score >= 0.82 else ""


def _normalize_question(question: str) -> str:
    text = re.sub(r"\s+", " ", question or "").strip().lower()
    text = re.sub(r"\s*\*\s*", " ", text)
    text = re.sub(r"\bthis field is required\b", " ", text)
    text = re.sub(r"\b\d+\s*-\s*\d+\s*paragraphs?\b", " ", text)
    text = re.sub(r"\b\d+\s*paragraphs?\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _load_saved_apply_answers(artifact_dir: Path) -> list[dict[str, str]]:
    path = artifact_dir / "apply-review.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    answers = payload.get("answers") or []
    if not isinstance(answers, list):
        return []
    cleaned: list[dict[str, str]] = []
    for item in answers:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        if question and answer:
            cleaned.append({"question": question, "answer": answer})
    return cleaned


def _section_blocks_matching(section: str, keywords: list[str]) -> list[str]:
    blocks = re.split(r"\n(?=###?\s+|\*\*[^*\n]+:\*\*)", section)
    matches = []
    for block in blocks:
        lower = block.lower()
        if any(keyword in lower for keyword in keywords):
            matches.append(block)
    return matches


def _clean_report_answer(block: str) -> str:
    lines = []
    for line in block.splitlines():
        cleaned = re.sub(r"^#{2,4}\s*", "", line).strip()
        cleaned = re.sub(r"^\*\*([^*]+)\*\*:?\s*", "", cleaned).strip()
        cleaned = cleaned.lstrip("> ").strip()
        if cleaned and not cleaned.lower().startswith("section g"):
            lines.append(cleaned)
    return "\n".join(lines).strip()


def _answer_for_application_question(
    question: str,
    *,
    company: str | None,
    role: str | None,
    report_context: dict | None = None,
) -> str:
    """Return an answer for an application question.

    Sources, in order:
      1. Saved answer from a prior apply session (``saved_answers`` in report_context).
      2. Section G draft answers from the evaluation report (``application_section``).

    Returns "" when no source is available so the form is left blank for the user
    to fill manually. The function does not synthesise candidate facts.
    """
    q = question.lower()
    if "reference" in q:
        return ""
    saved_answer = _find_saved_apply_answer(question, report_context)
    if saved_answer:
        return saved_answer
    report_answer = _find_report_answer(question, report_context)
    if report_answer:
        return report_answer
    return ""


def _radio_choice_for_question(question: str) -> str:
    q = question.lower()
    if "emea" in q or "apac" in q:
        return "No"
    if "north america" in q or "located in" in q:
        return "Yes"
    if "legally" in q and "work" in q:
        return "Yes"
    if "sponsor" in q or "sponsorship" in q:
        return "No"
    return ""


def _write_apply_review_summary(
    *,
    artifact_dir: Path,
    url: str,
    final_url: str,
    title: str,
    company: str | None,
    role: str | None,
    report_context: dict | None,
    filled: list[str],
    skipped: list[str],
    answers: list[dict[str, str]],
    required_empty: list[str],
    actions: list[str],
    screenshot: Path,
    pdf: Path | None,
    role_warnings: list[str],
    validation_issues: list[ReviewIssue] | None = None,
) -> Path:
    payload = {
        "schema_version": APPLY_REVIEW_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "url": url,
        "final_url": final_url,
        "title": title,
        "company": company,
        "role": role,
        "matched_report": report_context,
        "filled": filled,
        "skipped": skipped,
        "answers": answers,
        "required_empty": required_empty,
        "actions": actions,
        "screenshot": str(screenshot),
        "pdf": str(pdf) if pdf else None,
        "warnings": role_warnings,
        "validation_issues": issues_to_payload(validation_issues or []),
    }
    json_path = artifact_dir / "apply-review.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = artifact_dir / "apply-review.md"
    lines = [
        f"# Apply Review — {company or 'Unknown'} / {role or 'Unknown'}",
        "",
        f"- URL: {url}",
        f"- Final URL: {final_url}",
        f"- Page title: {title}",
        f"- Screenshot: {screenshot}",
        f"- PDF: {pdf if pdf else 'not attached'}",
    ]
    if report_context and report_context.get("path"):
        lines.append(f"- Matched report: {report_context['path']}")
    if role_warnings:
        lines.extend(["", "## Warnings", *[f"- {item}" for item in role_warnings]])
    lines.extend(["", "## Filled Fields", *[f"- {item}" for item in filled or ["none"]]])
    lines.extend(["", "## Needs Review", *[f"- {item}" for item in skipped or ["none"]]])
    lines.extend(["", "## Required Empty Fields", *[f"- {item}" for item in required_empty or ["none detected"]]])
    if answers:
        lines.append("")
        lines.append("## Drafted Answers")
        for item in answers:
            lines.append(f"### {item['question']}")
            lines.append(item["answer"])
            lines.append("")
    lines.extend(["", "## Visible Actions", *[f"- {item}" for item in actions or ["none"]]])
    md_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return md_path


def _append_apply_review_event(*, artifact_dir: Path, event: str, screenshot: Path | None = None) -> None:
    path = artifact_dir / "apply-review.md"
    line = f"\n## Event — {datetime.now(timezone.utc).isoformat()}\n- {event}"
    if screenshot:
        line += f"\n- Screenshot: {screenshot}"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _tracker_entry_by_id(tracker: TrackerRepository, tracker_id: int | None):
    if tracker_id is None:
        return None
    for entry in tracker.parse():
        if entry.number == tracker_id:
            return entry
    console.print(f"[red]Tracker row #{tracker_id} not found.[/red]")
    raise typer.Exit(1)


def _record_manual_submission(
    *,
    tracker: TrackerRepository,
    tracker_entry,
    company: str,
    role: str,
    url: str,
    pdf: Path | None,
):
    today = datetime.now().date()
    note = f"submitted manually via apply assist {today}"
    if tracker_entry:
        updated = tracker_entry.model_copy(
            update={
                "status": "Applied",
                "pdf": "✅" if pdf else tracker_entry.pdf,
                "notes": (tracker_entry.notes + f"; {note}").strip("; "),
            }
        )
        tracker.update_entry(updated)
        return updated

    existing, score = tracker.find_match(company=company, role=role)
    if existing and score >= 0.70:
        updated = existing.model_copy(
            update={
                "status": "Applied",
                "pdf": "✅" if pdf else existing.pdf,
                "notes": (existing.notes + f"; {note}").strip("; "),
            }
        )
        tracker.update_entry(updated)
        return updated

    return tracker.add_imported_email_entry(
        company=company,
        role=role,
        status="Applied",
        email_ref=f"manual:{today}",
        note=f"Submitted manually via apply assist; url={url}",
        pdf_attached=bool(pdf),
    )


if __name__ == "__main__":
    app()
