"""Writing down what an apply session did.

Split out of ``cli/apply.py`` (see docs/apply-seam-plan.md, Phase 1). The
apply-review summary, the tracker row an artifact belongs to, and the record of
a manual submission. Nothing here drives a browser.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from job_hunt.repositories.tracker_repo import TrackerRepository
from job_hunt.services.workday.review_gate import ReviewIssue, issues_to_payload


# Bumped when apply-review.json fields are renamed, removed, or change semantics.
# Additive fields do NOT require a bump. Downstream tooling (`jq`, dashboards)
# can read this to decide whether to apply migration logic.
APPLY_REVIEW_SCHEMA_VERSION = 1


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


def _link_artifacts_to_row(pdf: Path | None, entry, url: str | None) -> Path | None:
    """Stamp the tracker row number into the directory the PDF came from.

    Materials and tracker rows had nothing joining them, so an agent could
    build a résumé, the user could send it, and no later check could tell the
    directory had never been recorded. The marker makes that join exact for
    everything recorded from here on; `job-hunt checkup` reads it.
    """
    if pdf is None or entry is None:
        return None
    directory = pdf.resolve().parent
    if Path("output").resolve() not in directory.parents:
        return None
    marker = directory / ".tracker-row"
    marker.write_text(
        json.dumps(
            {
                "tracker_row": entry.number,
                "company": entry.company,
                "role": entry.role,
                "status": entry.status,
                "url": url,
                "recorded_at": datetime.now().isoformat(timespec="seconds"),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return marker


def _record_manual_submission(
    *,
    tracker: TrackerRepository,
    tracker_entry,
    company: str,
    role: str,
    url: str | None,
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

    from job_hunt.services.employer_match import EmployerMatcher, load_aliases

    matcher = EmployerMatcher(tracker.parse(), aliases=load_aliases())
    match = matcher.best(company=company, role=role, intent="mutate")
    if match:
        existing = match.entry
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
        note=(
            f"Submitted manually via apply assist; url={url}"
            if url
            else "Submitted manually via apply assist; no URL (recorded without one)"
        ),
        pdf_attached=bool(pdf),
    )


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


def _tracker_entry_blocks_apply(entry) -> bool:
    score_match = re.search(r"(\d+(?:\.\d+)?)\s*/\s*5", entry.score or "")
    if score_match and float(score_match.group(1)) < 3.0:
        return True
    return "skip" in f"{entry.notes} {entry.status}".lower()


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


def _select_pdf_for_entry(entry) -> Path | None:
    text = f"{entry.company} {entry.role} {entry.report} {entry.notes}"
    return _select_pdf_for_text(text)
