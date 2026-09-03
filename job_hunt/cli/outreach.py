from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
import typer
from rich.table import Table
from job_hunt.config.models import Settings, load_settings
from job_hunt.services.llm.base import ChatMessage
from job_hunt.services.llm.factory import build_cheap_provider
from job_hunt.services.llm.traced import traced_chat

from ._render import console
from . import app, contacts_app, outreach_app


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


def _gate_outward_artifact(
    *,
    artifact_path: Path,
    jd_text: str,
    company: str,
    role: str,
) -> None:
    """CLAUDE.md §1: "No artifact is delivered to the user until it has passed
    through the red team" names outreach emails and application-form answers
    in the same sentence as résumés and cover letters, but only those two ever
    ran the review — this closes that gap for the CLI paths. Mirrors
    `nodes/redteam.py`: UNREVIEWED (mmx unreachable, timeout, no verdict line)
    is never presented as a pass, and BLOCK is loud but does not delete or
    withhold the generated text — the operator adjudicates findings, the
    reviewer has no veto.

    The review is written beside the artifact under a paired name
    (``<stem>.redteam.md``) rather than the pipeline's plain ``redteam.md``:
    unlike a pipeline run directory, these CLI locations can already hold (or
    later hold) an unrelated ``redteam.md`` — e.g. an apply-answers file
    written into the same `output/<run>/` directory as a CV that already has
    its own review — and a fixed name would silently overwrite it.
    """
    from job_hunt.services.redteam import run_review

    result = run_review(artifacts=[artifact_path], jd_text=jd_text, company=company, role=role)
    style = {"SEND": "green", "REVISE": "yellow", "BLOCK": "bold red"}.get(
        result.verdict, "bold white"
    )
    if result.review:
        review_path = artifact_path.parent / f"{artifact_path.stem}.redteam.md"
        review_path.write_text(result.review, encoding="utf-8")
        console.print(f"\n[{style}]RED TEAM: {result.verdict}[/{style}] — findings in {review_path}")
    else:
        console.print(
            f"\n[{style}]RED TEAM: UNREVIEWED[/{style}] — not reviewed "
            f"({'; '.join(result.errors) or 'no reviewer output'}); this is not a pass."
        )
    if result.verdict == "BLOCK":
        console.print(
            "[red]Do not send until the findings above are addressed. The text "
            "above is not discarded — you adjudicate; the reviewer has no veto.[/red]"
        )


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
    # CLAUDE.md §1: outreach emails are named alongside résumés and cover
    # letters as requiring red team before delivery — this message goes to a
    # real employer contact under the user's name.
    _gate_outward_artifact(
        artifact_path=message_path, jd_text=jd_text, company=target_company, role=role
    )
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


@outreach_app.command("log")
def outreach_log(
    contact_id: str = typer.Argument(..., help="Contact id or unique prefix."),
    role: str = typer.Option(..., help="Role the message was about."),
    company: str = typer.Option("", help="Override company; defaults to contact company."),
    application_id: int | None = typer.Option(None, help="Tracker row number, if relevant."),
    channel: str = typer.Option("email", help="email / linkedin / other."),
    follow_up_at: str = typer.Option("", help="Follow-up date YYYY-MM-DD."),
    notes: str = typer.Option("", help="What was sent, in your own words."),
) -> None:
    """Record a message you already sent, without drafting one.

    `outreach draft` is the only other way to create an outreach event, and it
    calls the LLM to write a message first — no use when the reply has already
    gone out. Replies are sent from a mailbox this system never reads, so
    nothing observes them; this is how they get on the record.
    """
    from job_hunt.services.outreach import OutreachEvent, add_event, get_contact

    if channel not in {"email", "linkedin", "other"}:
        console.print("[red]Channel must be email, linkedin, or other.[/red]")
        raise typer.Exit(1)

    contact = get_contact(contact_id)
    if contact is None:
        console.print(f"[red]Contact not found:[/red] {contact_id}")
        raise typer.Exit(1)

    event = add_event(
        OutreachEvent(
            contact_id=contact.id,
            application_id=application_id,
            company=company or contact.company,
            role=role,
            channel=channel,  # type: ignore[arg-type]
            status="sent",
            follow_up_at=follow_up_at,
            notes=notes,
        )
    )
    console.print(f"[green]Logged sent[/green] outreach event {event.id}: {event.company} — {event.role}")
    if follow_up_at:
        console.print(f"Follow-up due {follow_up_at}; see `job-hunt outreach due`.")


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
