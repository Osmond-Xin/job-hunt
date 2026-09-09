"""Driving a form without knowing whose form it is.

The ATS-agnostic half of the browser work: find a control by its label, fill it,
read what is still empty, attach a file, get past a page that fronts the real
form. Split out of ``cli/apply.py`` (docs/apply-seam-plan.md, Phase 2).

Four of these -- ``_enter_application_form``, ``_advance_application_start``,
``_attach_resume`` and ``_finish_pending_upload_dialog`` -- read like session
orchestration and an earlier draft of the plan moved them with the session. They
are here instead because the Workday driver calls them: leaving them a phase
behind would have meant Phase 3a importing them back out of ``cli/``, which is
the layer inversion this whole refactor exists to remove.
"""

from __future__ import annotations

import re
from pathlib import Path

from job_hunt.services.workday.detect import is_workday_page


class ApplyDoRefused(Exception):
    """A single apply-do operation could not be carried out as asked."""


def _looks_like_submit_label(label: str) -> bool:
    return bool(_SUBMIT_LABEL_RE.search(label))


# Deny-list for apply-do clicks. Final-submission buttons across ATSes say
# more than just "Submit" (Greenhouse/Lever/Ashby use Apply/Finish/Done/…),
# so anything that plausibly finalizes an application is refused; the human
# (or the multi-gated --auto-submit) performs that click. Step-advance labels
# like "Save and Continue" / "Next" stay allowed.
_SUBMIT_LABEL_RE = re.compile(
    r"\bsubmit\b|\bsend\b|\bapply\b|\bfinish\b|\bcomplete\b|\bdone\b"
    r"|\bconfirm\b|\bfinali[sz]e\b",
    re.I,
)


async def _resolve_unique_target(candidates, label: str):
    """First locator with exactly one match wins; >1 matches is a hard refusal.

    Candidates are ordered exact-match-first so a precise label never loses to
    a broader substring locator, and ``.first`` never silently picks among
    multiple hits (red-team fix: partial first-match mutating the wrong field).
    """
    for locator in candidates:
        try:
            n = await locator.count()
        except Exception:
            continue
        if n == 0:
            continue
        if n > 1:
            raise ApplyDoRefused(f"ambiguous: {n} elements match '{label}'")
        return locator.first
    return None


async def _element_looks_like_submit(target) -> bool:
    """Read back the resolved element's own text/labels before clicking.

    The CLI-side guard only sees the requested label; without this, clicking
    'Save' could land on a 'Save & Submit' button.
    """
    try:
        text = await target.evaluate(
            "el => [el.innerText, el.value, el.getAttribute('aria-label')]"
            ".filter(Boolean).join(' ')"
        )
    except Exception:
        return False
    return _looks_like_submit_label(str(text or ""))


async def _do_click_by_label(page, label: str) -> bool:
    pattern = re.compile(re.escape(label), re.I)
    target = await _resolve_unique_target(
        (
            page.get_by_role("button", name=label, exact=True),
            page.get_by_role("link", name=label, exact=True),
            page.get_by_role("button", name=pattern),
            page.get_by_role("link", name=pattern),
            page.locator("button, [role='button'], a, input[type='submit']").filter(
                has_text=pattern
            ),
        ),
        label,
    )
    if target is None:
        return False
    if await _element_looks_like_submit(target):
        raise ApplyDoRefused("resolved element looks like a final submit control")
    try:
        await target.click(timeout=3000)
        return True
    except Exception:
        return False


async def _do_check_by_label(page, label: str) -> bool:
    pattern = re.compile(re.escape(label), re.I)
    target = await _resolve_unique_target(
        (
            page.get_by_role("checkbox", name=label, exact=True),
            page.get_by_role("radio", name=label, exact=True),
            page.get_by_role("checkbox", name=pattern),
            page.get_by_role("radio", name=pattern),
            page.get_by_label(pattern),
        ),
        label,
    )
    if target is None:
        return False
    try:
        await target.check(timeout=3000)
        return True
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
        if pdf.name in text or (
            is_workday_page(page)
            and "Successfully Uploaded" in text
            and "Resume/CV" in text
        ):
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


_COVER_LETTER_LABEL_RE = re.compile(r"cover\s*letter", re.IGNORECASE)
