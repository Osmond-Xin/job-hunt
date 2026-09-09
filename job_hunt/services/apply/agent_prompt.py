"""Building the prompt that hands an application to an agent.

Split out of ``cli/apply.py`` (see docs/apply-seam-plan.md, Phase 1). Turns a
URL plus whatever the tracker knows into the instruction text, and works out
which tracker row and PDF a loop target refers to.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from job_hunt.repositories.tracker_repo import TrackerRepository
from job_hunt.services.employer_match import MATCH_THRESHOLD, EmployerMatcher
from job_hunt.services.web_extract import _extract_loop_url_metadata

from .linking import _best_tracker_text_match, _select_pdf_for_entry, _select_pdf_for_text


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


def _loop_agent_apply_command(*, url: str, company: str | None, role: str | None, pdf: Path | None) -> str:
    parts = [".venv/bin/job-hunt", "agent-apply", url]
    if company:
        parts.extend(["--company", company])
    if role:
        parts.extend(["--role", role])
    if pdf:
        parts.extend(["--pdf", str(pdf)])
    return " ".join(shlex.quote(part) for part in parts)


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
        entry, score = EmployerMatcher(tracker.parse()).raw_match(company=company, role=role)
    if (not entry or score < MATCH_THRESHOLD) and inferred_text:
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
    if entry and score >= MATCH_THRESHOLD:
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
    status_command = ".venv/bin/job-hunt apply-status"

    return f"""# Agent Apply Runbook

You are operating the job-hunt application assistant from this repository.

Goal: open the application form, fill it with the candidate's real profile and selected PDF, let the user review and request edits, and only record the application after the user manually submits it.

Hard safety rules:
- Never click the final Submit/Apply button yourself.
- Do not invent candidate facts. Use `profile/profile.yml`, `profile/cv.md`, the selected PDF, and any matching report under `reports/`.
- If a required question cannot be answered truthfully, pause and ask the user.
- Do not expose secrets, cookies, OAuth tokens, or webhook URLs in the conversation.
- Do not record the application as Applied until the user explicitly confirms they clicked Submit.

Token rules (cheapest source of truth first):
- Never drive the application page through a browser MCP (Playwright MCP etc.); all browser interaction goes through these CLI commands.
- Verify results from `apply-review.json` (and `{status_command}`) first. Read a screenshot image only when the JSON shows a problem (`required_empty`, `validation_issues`, `warnings`, or `pdf: null` when a PDF was expected).
- Fix a single missed field with `.venv/bin/job-hunt apply-do --fill 'label=value'` (also `--click/--select/--check`) instead of taking over the browser.

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
6. Read `apply-review.json` in the artifact dir (NOT the screenshot). Summarize for the user:
   - company and role
   - fields filled
   - fields needing attention (`required_empty`, `validation_issues`, `warnings`)
   - PDF filename (`pdf` key)
   - any risk, missing answer, or work-authorization question
   Only read the newest `apply-review-*.jpg` when the JSON shows a problem. For a live view of the page state, run `{status_command}` (add `--controls` for the full field list).
7. Ask the user to review the visible browser. If the user requests edits, fix single fields with `apply-do --fill 'label=value'`; otherwise tell the user the exact field/value to change.
8. If the user asks to swap the PDF, run:

```bash
{replace_command}
```

9. Wait a few seconds, then confirm the swap from the command output or `{status_command}`.
10. When the user says it is ready, instruct the user to manually click the final Submit/Apply button in the browser.
11. After the user confirms they submitted, capture the current confirmation page while the browser is still open:

```bash
{capture_command}
```

12. Wait a few seconds, then inspect the newest `apply-page-*.jpg` screenshot for a confirmation such as "Thank you for applying" or "application received".
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
