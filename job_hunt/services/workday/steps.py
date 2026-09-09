"""Driving a Workday application from one step to the next.

The orchestration half of Workday support: which step the page is on, what to
put in it, and how to get to the next one. The per-step logic it calls has lived
in this package since ADR-010 (``my_information``, ``my_experience``,
``application_questions``, ``voluntary_disclosures``, ``review_gate``); this is
the part that stayed in ``cli/apply.py`` for another four months.

Moved verbatim in Phase 3a of docs/apply-seam-plan.md -- no behaviour change, so
that the diff reads as a move and can be checked as one. What makes any of it
testable without a browser is Phase 3b, and only if the spike there says the
step decisions separate cleanly from the DOM reads.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from job_hunt.services.text import _short

from job_hunt.services.web import apply_run_log
from job_hunt.services.web.form_fill import (
    _advance_application_start,
    _attach_resume,
    _enter_application_form,
    _field_context,
    _field_contains_text,
    _fill_by_label_or_placeholder,
    _fill_contenteditable,
    _finish_pending_upload_dialog,
    _force_fill_by_accessible_label,
    _required_empty_fields,
    _resolve_unique_target,
)
from job_hunt.services.apply.answers import _answer_for_application_question
from job_hunt.services.profile_loader import _apply_profile_values
from job_hunt.services.profile_loader import (
    workday_education_entries as _load_workday_education_entries,
    workday_experience_entries as _load_workday_experience_entries,
)
from job_hunt.services.workday.application_questions import (
    run_question_ops as _run_workday_question_ops_from_module,
)
from job_hunt.services.workday.employer_config import (
    select_employer_config as _select_workday_employer_config,
)
from job_hunt.services.workday.required_empty import (
    dedupe_preserve_order as _dedupe_preserve_order,
    filter_required_empty_fields as _filter_required_empty_fields,
)
from job_hunt.services.workday.detect import is_workday_page
from job_hunt.services.workday.step_decisions import (
    step_from_body_text,
    step_from_headings,
)
from job_hunt.services.workday.review_gate import (
    ReviewIssue,
    detect_review_issues,
    review_needs_repair as _workday_review_needs_repair_from_module,
)


# These two stay here until Phase 3a: both fall back to a Workday-specific
# helper, so they are not the ATS-agnostic primitives form_fill.py is for.
# Moving them now would have services/web/ importing services/workday/ --
# legal by the layer rule, and still the generic form filler knowing whose
# form it is.
async def _do_fill_by_label(page, label: str, value: str) -> bool:
    pattern = re.compile(re.escape(label), re.I)
    target = await _resolve_unique_target(
        (
            page.get_by_label(label, exact=True),
            page.get_by_label(pattern),
            page.get_by_placeholder(pattern),
        ),
        label,
    )
    if target is not None:
        try:
            await target.fill(value, timeout=3000)
            return True
        except Exception:
            pass
    # Workday-style: input inside the question container matching the label text.
    try:
        return await _fill_workday_input_in_question(page, label, value, force=True)
    except Exception:
        return False


async def _do_select_by_label(page, label: str, value: str) -> bool:
    pattern = re.compile(re.escape(label), re.I)
    target = await _resolve_unique_target(
        (page.get_by_label(label, exact=True), page.get_by_label(pattern)), label
    )
    if target is not None:
        try:
            await target.select_option(label=value, timeout=3000)
            return True
        except Exception:
            pass
    try:
        return await _select_workday_dropdown_by_label(page, label, [value], force=True)
    except Exception:
        return False


# Login + diagnostic dump live in `services.workday.login`. This wrapper keeps
# the long historical call-site signature stable while delegating the actual
# orchestration. The body below the early returns was moved verbatim into the
# module; see ADR-011 §3.5 for the design.
async def _maybe_workday_login(page, *, artifact_dir: Path | None = None, warn) -> None:
    from job_hunt.services.workday.login import maybe_login

    values = _apply_profile_values()
    return await maybe_login(
        page,
        email=values.get("email", ""),
        artifact_dir=artifact_dir,
        fallback_fill=_fill_by_label_or_placeholder,
        warn=warn,
    )


async def _recover_workday_error_page(page, original_url: str) -> None:
    if not is_workday_page(page):
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
    if not is_workday_page(page):
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
    if not is_workday_page(page):
        return False
    try:
        text = await page.locator("body").inner_text(timeout=3000)
    except Exception:
        return False
    return pdf.name in text


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
    if not is_workday_page(page):
        return [], [], []
    try:
        # Readability probe, not a value: a body that will not yield its text
        # inside 3s is a page still rendering, and stepping it would act on a
        # half-built form. The text itself is unused -- `_workday_current_step`
        # re-reads what it needs.
        await page.locator("body").inner_text(timeout=3000)
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
    """Read the page, and let step_decisions say what it means.

    The body read stays conditional on the heading pass finding nothing --
    see the note in step_decisions about why that ordering is load-bearing.
    """
    try:
        headings = await page.locator("h1, h2, h3").all_inner_texts()
    except Exception:
        headings = []
    step = step_from_headings(headings)
    if step:
        return step
    try:
        text = await page.locator("body").inner_text(timeout=2000)
    except Exception:
        return ""
    return step_from_body_text(text)


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
    if not is_workday_page(page):
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
