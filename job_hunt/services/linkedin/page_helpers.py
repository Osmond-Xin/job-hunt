"""The page helpers the LinkedIn Easy Apply driver runs on.

``easy_apply.py`` has always taken these as injected callables -- that is the
seam ADR-010's dispatcher established and this one follows. They lived in
``cli/apply.py`` anyway, so the seam existed while the things on both sides of
it sat in the same file. Moved in Phase 4 of docs/apply-seam-plan.md.
"""

from __future__ import annotations

import re
from pathlib import Path

from job_hunt.services.apply.answers import _answer_for_application_question
from job_hunt.services.profile_loader import _apply_profile_values
from job_hunt.services.web import apply_run_log


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
