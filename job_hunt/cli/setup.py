from __future__ import annotations

import shutil
import json
import os
import re
import subprocess
from pathlib import Path
import typer
import yaml
from rich.table import Table
from job_hunt.config.models import Settings, load_settings
from job_hunt.repositories.tracker_repo import TrackerRepository
from job_hunt.services import onboarding as _onb

from ._render import console
from . import app, config_app


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
        # `configure_ai` is a Typer command, so an omitted argument keeps its
        # OptionInfo default rather than None/"" — pass every parameter explicitly.
        configure_ai(
            provider="minimax",
            api_key=None,
            base_url="https://api.minimax.chat",
            model="",
            endpoint_style="minimax",
        )

    if guided and not yes:
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
