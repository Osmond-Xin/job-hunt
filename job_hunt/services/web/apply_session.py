"""One apply session, from opening the browser to closing it.

The shape it settled into: launch, pick the driver that owns the page, let it
fill, put the result through the one gate, maybe submit, report. Everything
specific to an ATS is behind ``AtsDriver``; everything specific to a terminal is
behind ``Reporter``. What is left here is the sequence, which is the same
whichever ATS answered.

Moved out of ``cli/apply.py`` in Phase 5 of docs/apply-seam-plan.md. The
forty-nine ``console.print`` calls that came with it are ``reporter`` calls now
-- the session says what happened and ``cli/`` decides what that looks like.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import uuid
from pathlib import Path

from job_hunt.services.apply.answers import (
    _answer_for_application_question,
    _radio_choice_for_question,
)
from job_hunt.services.apply.linking import (
    _append_apply_review_event,
    _write_apply_review_summary,
)
from job_hunt.services.apply.reporting import _report_fit_warnings
from job_hunt.services.profile_loader import _apply_profile_values
from job_hunt.services.text import _short
from job_hunt.services.web import apply_ipc, apply_ops, apply_run_log, page_summary, submit_gate
from job_hunt.services.web.ats_contract import ApplyContext, Blocker
from job_hunt.services.web import ats_registry
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
from job_hunt.services.web.reporter import NullReporter, Reporter
from job_hunt.services.linkedin.page_helpers import _maybe_linkedin_easy_apply
from job_hunt.services.workday.detect import is_workday_page
from job_hunt.services.workday.required_empty import (
    filter_non_blocking_workday_skips as _filter_non_blocking_workday_skips,
    filter_required_empty_fields as _filter_required_empty_fields,
)
from job_hunt.services.workday.steps import (
    _collect_workday_review_issues,
    _fill_workday_current_step,
    _workday_current_step,
    _do_fill_by_label,
    _do_select_by_label,
    _maybe_workday_login,
    _recover_workday_error_page,
    _workday_advance_all_steps,
    _workday_resume_was_uploaded,
)

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


async def _page_identity_warnings(
    page, *, company: str | None, role: str | None,
    reporter: Reporter = NullReporter(),
) -> list[str]:
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


async def _auto_fill_application(
    page,
    *,
    company: str | None,
    role: str | None,
    report_context: dict | None = None,
    reporter: Reporter = NullReporter(),
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


async def _collect_status_payload(
    page, *, include_controls: bool, filled_hint: list[str],
    reporter: Reporter = NullReporter(),
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


async def _handle_do_command(
    page, payload: dict, *, filled_hint: list[str],
    reporter: Reporter = NullReporter(),
) -> dict:
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


async def _handle_refill_current_page(
    page,
    art_dir: Path,
    url: str,
    company: str | None,
    role: str | None,
    report_context: dict | None,
    pdf: "Path | None",
    role_warnings: list[str],
    reporter: Reporter = NullReporter(),
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
    reporter.good(f"Current page refilled: {last_screenshot}")
    if attached:
        reporter.good(f"Attached PDF: {pdf}")
    if filled:
        reporter.info("Auto-filled fields:")
        for item in filled:
            reporter.info(f"- {item}")
    if required_empty:
        reporter.warn("Required fields still empty / need review:")
        for item in required_empty[:20]:
            reporter.info(f"- {item}")
    return last_screenshot


async def _open_apply_page(
    url: str,
    *,
    reporter: Reporter,
    confirm_submitted=lambda prompt: False,
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
            warn=lambda msg: reporter.warn(f"{msg}"),
        )
        await _advance_application_start(page)
        await _recover_workday_error_page(page, url)
        await _wait_for_application_ready(page)
        title = await page.title()
        role_warnings = await _page_identity_warnings(
            page, company=company, role=role, reporter=reporter
        )
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
                reporter.info(
                    "[red]LinkedIn session is not signed in.[/red] "
                    "Open the persistent browser profile, sign in, then re-run."
                )
            elif auto_submit_clicked:
                apply_run_log.emit(
                    art_dir, "auto_submit.fired",
                    url=page.url, platform="linkedin",
                )
                reporter.info(
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
                    reporter.info(
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
                    reporter=reporter,
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
            # One gate, in services/web/submit_gate.py. This used to be an
            # if-chain here for Workday and a second one inside the LinkedIn
            # flow; the two could disagree, and only one of them was tested.
            # `authorised` is already true to be in this branch at all -- the
            # three keys were weighed in apply_assist -- so what is left is
            # whether the form itself is ready.
            driver = await ats_registry.driver_for(page)
            decision = submit_gate.may_submit(
                authorisation=submit_gate.GateDecision(allowed=True),
                driver_name=driver.name if driver else None,
                required_empty=list(required_empty),
                blockers=[
                    Blocker(code=i.code, message=i.message, details=dict(i.details))
                    for i in validation_issues
                ],
                unresolved_attempt=apply_run_log.unresolved_submit_attempt(art_dir),
            )
            if not decision.allowed:
                apply_run_log.emit(
                    art_dir, decision.event, reason=decision.reason,
                    **(decision.detail or {}),
                )
                reporter.info(
                    f"[yellow]Auto-submit skipped:[/yellow] "
                    f"{_gate_reason_text(decision, required_empty, validation_issues)}"
                )
            else:
                # Recorded before the click, not after: a click that lands and
                # then loses its confirmation must leave evidence that it
                # happened, or the next run has no way to know not to repeat it.
                apply_run_log.emit(art_dir, "submit.attempted", url=page.url,
                                   driver=driver.name)
                outcome = await driver.submit(page, _apply_ctx(
                    company=company, role=role, pdf=pdf,
                    cover_letter_pdf=cover_letter_pdf, artifact_dir=art_dir,
                    report_context=report_context,
                ))
                apply_run_log.emit(
                    art_dir, "submit.resolved", state=outcome.state,
                    evidence=_short(outcome.evidence, 200), driver=driver.name,
                )
                if outcome.state == "confirmed":
                    auto_submit_clicked = True
                    apply_run_log.emit(art_dir, "auto_submit.fired", url=page.url)
                    apply_run_log.emit(art_dir, "auto_submit.confirmed", url=page.url)
                    reporter.good("Auto-submit confirmed.")
                elif outcome.state == "unknown":
                    # Neither sent nor not-sent. Do not record it as applied and
                    # do not offer to retry: a duplicate application to a real
                    # employer is worse than a missing tracker row, and only a
                    # person can tell which happened.
                    reporter.info(
                        "[red]Submit clicked but not confirmed.[/red] "
                        f"{outcome.evidence}\n"
                        f"Check the page yourself before re-running — this run is "
                        f"recorded as unresolved in {art_dir}, and the next "
                        f"auto-submit for it will refuse until you clear it."
                    )
                else:
                    apply_run_log.emit(
                        art_dir, "auto_submit.gated", reason="submit_rejected",
                        detail=_short(outcome.evidence, 200),
                    )
                    reporter.info(
                        f"[yellow]Auto-submit skipped:[/yellow] {outcome.evidence}"
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

        reporter.info(f"Opened: {title or url}")
        reporter.info(f"Final URL: {page.url}")
        reporter.info(f"Artifact dir: {art_dir}")
        if report_context and report_context.get("path"):
            reporter.info(f"Matched report: {report_context['path']}")
        if role_warnings:
            reporter.warn("Identity warnings:")
            for warning in role_warnings:
                reporter.info(f"- {warning}")
        reporter.info(f"File inputs: {file_count}")
        if attached:
            reporter.good(f"Attached PDF: {pdf}")
        elif pdf:
            reporter.warn("PDF was not attached; no usable file input was found.")
        if filled:
            reporter.info("Auto-filled fields:")
            for item in filled:
                reporter.info(f"- {item}")
        if skipped:
            reporter.info("Needs review / not auto-filled:")
            for item in skipped[:20]:
                reporter.info(f"- {item}")
        if required_empty:
            reporter.warn("Required fields still empty / need review:")
            for item in required_empty[:20]:
                reporter.info(f"- {item}")
        if actions:
            reporter.info("Visible action labels:")
            for label in actions[:12]:
                reporter.info(f"- {label}")
        reporter.info(f"Review screenshot: {screenshot}")
        reporter.info(f"Review summary: {summary_path}")

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
            reporter.warn("\nBrowser open — review and submit manually.")
            reporter.info(f"Sentinel dir: {art_dir}")
            reporter.info("Commands: apply-replace-pdf <pdf>  |  Tell Claude 'submitted' when done.")

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
                    reporter.info(
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
                        reporter.info(
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
                            reporter.error("apply-replace-pdf: empty payload")
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
                            reporter.good(f"PDF replaced: {new_pdf}")
                            reporter.info(f"New screenshot: {last_screenshot}")
                        else:
                            apply_run_log.emit(
                                art_dir, "command.replace_pdf.failed",
                                pdf=new_pdf_str, reason="not_found",
                            )
                            reporter.error(f"PDF not found: {new_pdf_str}")
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
                        reporter.good(f"Page captured: {last_screenshot}")
                    elif cmd.kind == apply_ipc.COMMAND_TYPE_REFILL_CURRENT_PAGE:
                        last_screenshot = await _handle_refill_current_page(
                            page, art_dir, url, company, role, report_context,
                            pdf, role_warnings, reporter,
                        )
                    elif cmd.kind == apply_ipc.COMMAND_TYPE_STATUS:
                        # Fail closed: a status/do handler crash must never
                        # kill the fill-only session.
                        try:
                            status_payload = await _collect_status_payload(
                                page,
                                include_controls=bool(cmd.payload.get("controls")),
                                filled_hint=filled,
                                reporter=reporter,
                            )
                            apply_ipc.write_response(art_dir, cmd.id, status_payload)
                            apply_run_log.emit(
                                art_dir, "command.status",
                                url=page.url,
                                required_empty_count=len(status_payload.get("required_empty") or []),
                                error_count=len(status_payload.get("errors") or []),
                            )
                            reporter.good("Status request answered.")
                        except Exception as exc:
                            apply_run_log.emit(
                                art_dir, "command.status.failed",
                                error=_short(str(exc), 160),
                            )
                            reporter.error(f"Status request failed: {exc}")
                    elif cmd.kind == apply_ipc.COMMAND_TYPE_DO:
                        try:
                            do_result = await _handle_do_command(
                                page, cmd.payload, filled_hint=filled,
                                reporter=reporter,
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
                            reporter.info(
                                f"[green]apply-do handled:[/green] {do_result.get('op')} '{do_result.get('label')}'"
                                if do_result.get("ok")
                                else f"[red]apply-do failed:[/red] {do_result.get('op')} '{do_result.get('label')}'"
                            )
                        except Exception as exc:
                            apply_run_log.emit(
                                art_dir, "command.do.failed",
                                error=_short(str(exc), 160),
                            )
                            reporter.error(f"apply-do crashed: {exc}")
                    elif cmd.kind == apply_ipc.COMMAND_TYPE_CLOSE_SESSION:
                        _append_apply_review_event(
                            artifact_dir=art_dir,
                            event="Graceful browser close requested; persistent profile should save login state.",
                            screenshot=last_screenshot,
                        )
                        apply_run_log.emit(art_dir, "command.close_session")
                        reporter.good("Graceful close requested; saving browser profile.")
                        close_requested = True

                if close_requested:
                    break

            cdp_sentinel.unlink(missing_ok=True)
            apply_ipc.clear_heartbeat(art_dir)
            apply_run_log.emit(art_dir, "session.ended", final_url=page.url)
            await context.close()
            return {"submitted": False, "deferred": True, "screenshot": str(last_screenshot), "artifact_dir": str(art_dir)}

        reporter.info(
            "\nReview the form in the browser and submit it manually. "
            "This command will only update local state after you confirm."
        )
        # Asking a person a question is the caller's job, the same as printing
        # is: the session states the situation and waits for an answer it has no
        # way to obtain itself.
        submitted = await asyncio.to_thread(
            confirm_submitted, "Have you manually submitted this application?"
        )
        await context.close()
    return {"submitted": submitted, "screenshot": str(screenshot), "artifact_dir": str(art_dir)}


def _apply_ctx(*, company, role, pdf, cover_letter_pdf, artifact_dir, report_context):
    """Bundle what a driver needs about this application."""
    return ApplyContext(
        company=company, role=role, pdf=pdf, cover_letter_pdf=cover_letter_pdf,
        artifact_dir=artifact_dir, report_context=report_context or {},
        profile_values=_apply_profile_values(),
    )


def _gate_reason_text(decision, required_empty, validation_issues) -> str:
    """Say why in the operator's terms, not the gate's constant names."""
    return {
        submit_gate.REASON_NO_DRIVER:
            "no driver recognises this form, so there is no Submit it knows how to click.",
        submit_gate.REASON_REVIEW_ISSUES:
            f"{len(validation_issues)} Review-gate issue(s).",
        submit_gate.REASON_REQUIRED_EMPTY:
            f"{len(required_empty)} required field(s) still empty.",
        submit_gate.REASON_UNRESOLVED_ATTEMPT:
            "a previous submit was clicked and never confirmed. Check whether it "
            "went through before trying again.",
    }.get(decision.reason, decision.reason)
