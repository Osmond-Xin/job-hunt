"""Workday login modal handling — extracted from cli.py.

The login modal is the very first Workday surface and the most fragile:
Workday A/B-tests it constantly and the "Sign In" vs "Create Account"
panel toggle is the failure mode that most often leaves the operator
staring at a blank form.

Design:

- Password lives in ``storage/private/workday-login-password.txt`` and is
  deleted after use unless ``storage/private/keep-workday-login`` exists.
  File-based instead of CLI flag so the password never appears in
  ``ps``-style process listings.
- Three end states are surfaced (per ADR-011 Phase 3.5 diagnostics):
  ``logged_in``, ``no_modal`` (assume signed-in via cookies), and
  ``unknown_state`` (modal in unexpected configuration; dumps screenshot
  + HTML for diagnosis).
- Fallback typing (``_fill_by_label_or_placeholder``) is injected from
  cli.py rather than re-implemented here, so the generic honeypot-aware
  form helper stays a single source of truth.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Awaitable, Callable

from job_hunt.services.web import apply_run_log


_PASSWORD_PATH = Path("storage/private/workday-login-password.txt")
_KEEP_LOGIN_SENTINEL = Path("storage/private/keep-workday-login")

FillFallback = Callable[[object, str, str], Awaitable[bool]]
Warner = Callable[[str], None]


_JS_SWITCH_TO_SIGN_IN = """() => {
    const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
    const norm = text => (text || '').replace(/\\s+/g, ' ').trim().toLowerCase();
    const links = Array.from(document.querySelectorAll('a, button, [role="button"], span'))
        .filter(visible)
        .filter(el => norm(el.innerText) === 'sign in')
        .filter(el => {
            const parentText = norm(el.parentElement?.innerText || '');
            return parentText.includes('already have an account');
        });
    const target = links[links.length - 1];
    if (!target) return false;
    target.scrollIntoView({block: 'center'});
    target.click();
    return true;
}"""


_JS_FILL_LOGIN = """({email, password}) => {
    const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
    const norm = text => (text || '').replace(/\\s+/g, ' ').trim().toLowerCase();
    const setValue = (input, value) => {
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
        if (setter) setter.call(input, value);
        else input.value = value;
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.dispatchEvent(new Event('change', {bubbles: true}));
        input.dispatchEvent(new Event('blur', {bubbles: true}));
    };
    const forms = Array.from(document.querySelectorAll('form, [role="dialog"], section, main, div'))
        .filter(visible)
        .map(el => ({el, text: norm(el.innerText)}))
        .filter(item => item.text.includes('sign in'))
        .filter(item => !item.text.includes('verify new password'));
    const candidates = forms.length ? forms : [{el: document.body, text: norm(document.body.innerText)}];
    for (const {el} of candidates) {
        const emailInput = Array.from(el.querySelectorAll('input[type="email"], input[type="text"], input:not([type])'))
            .filter(visible)
            .find(input => {
                const hint = norm(input.getAttribute('aria-label') || input.placeholder || input.name || input.closest('div')?.innerText || '');
                return hint.includes('email') || hint === '';
            });
        const passwordInput = Array.from(el.querySelectorAll('input[type="password"]')).filter(visible)[0];
        if (emailInput && passwordInput) {
            setValue(emailInput, email);
            setValue(passwordInput, password);
            return true;
        }
    }
    return false;
}"""


_JS_CLICK_SIGN_IN_SCOPED = """() => {
    const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
    const norm = text => (text || '').replace(/\\s+/g, ' ').trim().toLowerCase();
    const passwords = Array.from(document.querySelectorAll('input[type="password"]')).filter(visible);
    for (const pw of passwords) {
        let scope = pw.parentElement;
        for (let depth = 0; scope && depth < 8; depth++, scope = scope.parentElement) {
            const text = norm(scope.innerText);
            if (!text.includes('sign in') || text.includes('verify new password')) continue;
            const buttons = Array.from(scope.querySelectorAll('[role="button"], button, input[type="submit"]')).filter(visible);
            const button = buttons.find(btn => norm(btn.innerText || btn.value || btn.getAttribute('aria-label')) === 'sign in');
            if (button) {
                button.scrollIntoView({block: 'center'});
                button.click();
                return true;
            }
        }
    }
    return false;
}"""


def _read_and_consume_password() -> str:
    """Read the one-time password file then delete it (unless keep sentinel)."""
    if not _PASSWORD_PATH.exists():
        return ""
    try:
        password = _PASSWORD_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        password = ""
    finally:
        if not _KEEP_LOGIN_SENTINEL.exists():
            _PASSWORD_PATH.unlink(missing_ok=True)
    return password


async def maybe_login(
    page,
    *,
    email: str,
    artifact_dir: Path | None = None,
    fallback_fill: FillFallback | None = None,
    warn: Warner | None = None,
) -> None:
    """Log into Workday from the one-time local secret file, then delete it.

    No-op when:
    - We are not on a Workday URL.
    - The password file does not exist (assume cookie/session reuse).
    - The page body is unreadable or shows no Sign In / Create Account
      controls (assume signed-in; emit a diagnostic event).

    Otherwise: switch the modal to the Sign In panel, fill credentials,
    submit, and navigate back to the form URL if Workday redirected to
    Candidate Home. Dumps screenshot + HTML when the modal stays stuck.
    """
    if "myworkdayjobs.com" not in page.url:
        return
    password = _read_and_consume_password()
    if not password:
        return

    try:
        page_text = await page.locator("body").inner_text(timeout=5000)
    except Exception:
        page_text = ""

    if not page_text:
        await _dump_unknown_state(page, artifact_dir, reason="body_text_unreadable", warn=warn)
        return

    sign_in_visible = "Sign In" in page_text
    create_account_visible = "Create Account" in page_text
    if not sign_in_visible and not create_account_visible:
        if artifact_dir is not None:
            apply_run_log.emit(
                artifact_dir,
                "workday.login.skipped",
                reason="no_modal_detected_assume_signed_in",
            )
        return

    form_url = page.url

    # Workday opens with the Create Account panel active; flip to Sign In
    # before filling credentials.
    await page.goto(form_url, wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(3000)
    await _switch_to_sign_in_panel(page)

    # Fill email + password in the visible Sign In panel.
    filled = False
    try:
        filled = bool(
            await page.evaluate(_JS_FILL_LOGIN, {"email": email, "password": password})
        )
    except Exception:
        filled = False
    if not filled and fallback_fill is not None:
        try:
            await fallback_fill(page, "Email Address", email)
            await fallback_fill(page, "Password", password)
        except Exception:
            pass

    await page.wait_for_timeout(500)
    await _submit_sign_in(page)

    # Workday may redirect to Candidate Home after login; navigate back.
    if "/apply" not in page.url:
        try:
            await page.goto(form_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(3000)
        except Exception:
            pass

    # Verify the modal cleared. If we still see Sign In / Create Account,
    # dump artifacts so the next reader has something concrete to inspect.
    try:
        post = await page.locator("body").inner_text(timeout=3000)
    except Exception:
        post = ""
    if "Sign In" in post or "Create Account" in post:
        await _dump_unknown_state(
            page, artifact_dir, reason="login_submit_did_not_clear_modal", warn=warn
        )


async def _switch_to_sign_in_panel(page) -> None:
    """Click the 'Already have an account? Sign In' link to flip panels."""
    switched = False
    try:
        switched = bool(await page.evaluate(_JS_SWITCH_TO_SIGN_IN))
        if switched:
            await page.wait_for_timeout(2500)
    except Exception:
        switched = False
    if switched:
        return

    for sign_in_tab in [
        page.locator("a, span, button").filter(
            has_text=re.compile(r"already have an account", re.IGNORECASE)
        ),
        page.get_by_text("Already have an account?", exact=False),
    ]:
        try:
            if await sign_in_tab.count():
                await sign_in_tab.last.click(timeout=5000)
                await page.wait_for_timeout(2500)
                return
        except Exception:
            continue


async def _submit_sign_in(page) -> None:
    """Press Enter on the password field, then fall back to clicking Sign In.

    Workday's React stack often ignores button clicks before the controlled
    state has settled, so the Enter-first ordering matters.
    """
    try:
        password_fields = page.locator('input[type="password"]')
        for idx in range(await password_fields.count() - 1, -1, -1):
            field = password_fields.nth(idx)
            if await field.is_visible():
                await field.press("Enter", timeout=5000)
                await page.wait_for_timeout(4500)
                break
    except Exception:
        pass

    if not await _still_on_sign_in(page):
        return

    # JS-scoped click (handles password-scoped Sign In button only).
    try:
        if await page.evaluate(_JS_CLICK_SIGN_IN_SCOPED):
            await page.wait_for_timeout(6000)
    except Exception:
        pass

    if not await _still_on_sign_in(page):
        return

    # Locator-based fallback for sites where JS evaluation is ignored.
    try:
        buttons = page.locator(
            '[role="button"][aria-label="Sign In"], button'
        ).filter(has_text=re.compile(r"^\s*Sign In\s*$", re.IGNORECASE))
        for idx in range(await buttons.count() - 1, -1, -1):
            button = buttons.nth(idx)
            if await button.is_visible():
                await button.click(timeout=8000)
                await page.wait_for_timeout(6000)
                return
    except Exception:
        pass


async def _still_on_sign_in(page) -> bool:
    try:
        body = await page.locator("body").inner_text(timeout=3000)
    except Exception:
        return False
    return "Password" in body and "Sign In" in body


async def _dump_unknown_state(
    page,
    artifact_dir: Path | None,
    *,
    reason: str,
    warn: Warner | None = None,
) -> None:
    """Phase 3.5 diagnostic: write a screenshot + DOM HTML snapshot."""
    if artifact_dir is None:
        return
    try:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        screenshot = artifact_dir / "login-modal-unknown.png"
        try:
            await page.screenshot(path=str(screenshot), full_page=True)
        except Exception:
            screenshot = None
        try:
            html = await page.content()
            (artifact_dir / "login-modal-unknown.html").write_text(html, encoding="utf-8")
        except Exception:
            pass
        apply_run_log.emit(
            artifact_dir,
            "workday.login.unknown_state",
            reason=reason,
            url=page.url,
            screenshot=str(screenshot) if screenshot else None,
        )
        if warn is not None:
            warn(
                f"Workday login modal in unknown state ({reason}); dumped "
                f"login-modal-unknown.png/html in {artifact_dir.name}."
            )
    except Exception:
        pass
