"""Is this posting still worth a human's time?

Every rule here comes from a posting that wasted real effort on 2026-08-14, when
20 shortlisted candidates were validated by hand and **13 were unusable**. The
failure modes are not interchangeable and a single status-code check catches
only the first three:

``DEAD``
    Hard 404. Databricks' Solutions Architect and a Digital Nova Scotia
    aggregator page for a CGI role.

``EXPIRED``
    Job Bank answers a removed posting with **HTTP 410** and the words "Job
    posting expired" — the cleanest signal any source gives. Four Nova Scotia
    government rows died this way.

``EXPIRED`` (redirect form)
    HTTP 200, full page, but the site bounced the request to a search page.
    LinkedIn appends ``?trk=expired_jd_redirect``. Reading the status code
    alone scores this as healthy.

``INELIGIBLE``
    The posting is live and the candidate may not apply to it. New Brunswick's
    In-Service competitions are open to current government employees only, and
    Nova Scotia's NSGEU bargaining-unit postings consider external applicants
    only if no internal candidate qualifies. ⚠️ GNB's own API reported
    ``RequisitionType: "Open Competition"`` for a requisition whose apply form
    said In-Service, so this has to be read out of the page text, not a field.

``NOT_A_VACANCY``
    Talent pools and candidate inventories. GNB publishes these as
    ``Candidate Inventory``; Cliniconex's page said outright it was "not for an
    existing vacancy".

``BLOCKED``
    Bot protection answered instead of the site: Cloudflare 403 (CareerBeacon,
    Newfold), CloudFront 403 (Adzuna detail pages), or a Radware CAPTCHA page
    served with HTTP 200 (CGI's njoyn). **This is not evidence the posting is
    gone** and must never be treated as DEAD — a job seeker who drops these
    loses real openings.

``SKIPPED``
    LinkedIn. The operator's account was restricted on 2026-08-14 for
    high-volume access; this checker will not fetch from that host at all.

Results are cached by URL. A posting that is DEAD stays dead, so the cache is
permanent for terminal verdicts and short-lived for the inconclusive ones.
"""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

CACHE_PATH = Path("cache/link_check/verdicts.json")
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

LIVE = "LIVE"
DEAD = "DEAD"
EXPIRED = "EXPIRED"
INELIGIBLE = "INELIGIBLE"
NOT_A_VACANCY = "NOT_A_VACANCY"
BLOCKED = "BLOCKED"
SKIPPED = "SKIPPED"

#: Verdicts that mean "do not spend a human on this".
REJECTING = frozenset({DEAD, EXPIRED, INELIGIBLE, NOT_A_VACANCY})
#: Verdicts worth re-checking later; the others are terminal.
PROVISIONAL = frozenset({BLOCKED, SKIPPED, LIVE})

# How long a rejection is trusted before it is re-checked. Nothing is cached
# forever: employers re-post the same requisition number, and a competition that
# was internal-only this month can be opened up next month. The split is by how
# solid the evidence was — an HTTP 410 is a fact, a phrase match is a reading.
CACHE_DAYS_HARD = 90    # 404 / 410
CACHE_DAYS_TEXT = 21    # anything derived from page wording

# Below this much visible text a page is a client-side shell, not a posting.
# Measured: GNB's Oracle job page renders 561 characters of chrome and no job
# description at all.
_THIN_PAGE_CHARS = 800

# Hosts this checker refuses to touch. Not a technical limitation.
_NO_FETCH_HOSTS = ("linkedin.com",)

# Deliberately narrow. "no longer available" was removed after review: it
# matches "Similar jobs: the previous posting is no longer available" in a
# sidebar and would delete the live posting the page is actually about.
_EXPIRED_TEXT = re.compile(
    r"job posting (?:has )?expired|this (?:job|posting|competition) has (?:expired|closed)|"
    r"no longer accepting applications|this posting has closed|"
    r"this posting is no longer active|position has been filled|"
    r"cette offre d'emploi a expir",
    re.I,
)
# Landing on a search page after asking for one posting means the posting is
# gone. Path boundaries are mandatory: without them "/jobs/search" matched the
# real slug "/jobs/search-engineer-123", and the bare "/jobs/" and "/search/"
# heuristics were dropped entirely — they were never strong enough to justify a
# deletion, and `expired_jd_redirect` is the only unambiguous signal here.
_REDIRECT_TO_SEARCH = re.compile(
    r"expired_jd_redirect|/jobs/search(?:[/?#]|$)|/jobsearch(?:[/?#]|$)", re.I
)
# INELIGIBLE means "he cannot apply at all". It is NOT for competitions that
# merely rank internal candidates first — Nova Scotia's NSGEU clause says
# external applicants are considered when no internal candidate qualifies, so
# he CAN apply and the operator has decided such postings are worth a lottery
# ticket. Flagging those as ineligible deleted applications he wanted to make.
_INELIGIBLE_TEXT = re.compile(
    r"in-service \(closed\)|in-service job opportunity|"
    r"open only to (?:current )?employees|"
    r"restricted to (?:current )?(?:civil service |government )?employees|"
    r"only open to (?:current )?employees",
    re.I,
)
# Any of these means the posting does accept outside applicants after all, and
# overrides an ineligibility match. Checked because real pages carry both.
_EXTERNAL_WELCOME = re.compile(
    r"external applicants (?:are|will be) (?:welcome|considered)|"
    r"open to (?:current )?employees and (?:the public|external)|"
    r"and members of the public|open to the public|"
    r"external (?:applicants|candidates)[^.]{0,60}will only be considered|"
    r"not only open to",
    re.I,
)
# The bare words "talent pool" / "talent community" were removed after review:
# they appear in the footer of live postings ("when you apply you will be added
# to our talent pool"), and matching them deleted real vacancies. What is left
# only fires when the page says of ITSELF that it is not a specific opening.
_NOT_A_VACANCY_TEXT = re.compile(
    r"candidate inventory|"
    r"(?:this (?:posting|position|job)[^.]{0,60})?not for an existing vacancy|"
    r"accepting applications for anticipated future opportunities|"
    r"expression of interest only|"
    r"this is not a (?:posting|competition) for a specific",
    re.I,
)
# Bot walls that answer with HTTP 200 instead of a status code.
_BOT_WALL_TEXT = re.compile(
    r"radware captcha|just a moment\.\.\.|enable javascript and cookies|"
    r"verifying you are human|attention required!|cf-ray|"
    r"checking your browser before accessing",
    re.I,
)


@dataclass(frozen=True)
class Verdict:
    url: str
    status: str
    detail: str = ""
    http_status: int = 0

    @property
    def rejects(self) -> bool:
        return self.status in REJECTING


def _load_cache(path: Path) -> dict[str, dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(path: Path, cache: dict[str, dict[str, Any]]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, indent=1, sort_keys=True), encoding="utf-8")
    except OSError:
        pass


def _visible_text(body: str) -> str:
    """Strip markup so the model reads the page, not the framework."""
    stripped = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", body or "")
    stripped = re.sub(r"(?s)<[^>]+>", " ", stripped)
    return re.sub(r"\s+", " ", html.unescape(stripped)).strip()


def _still_trusted(entry: dict[str, Any], today_date: date) -> bool:
    """Is a cached rejection still recent enough to act on?"""
    try:
        checked = date.fromisoformat(str(entry.get("checked", "")))
    except ValueError:
        return False
    budget = CACHE_DAYS_HARD if int(entry.get("http_status", 0)) in (404, 410) else CACHE_DAYS_TEXT
    return (today_date - checked).days < budget


def classify(
    url: str,
    *,
    http_status: int,
    final_url: str,
    body: str,
) -> Verdict:
    """Turn one fetched response into a verdict. Pure; the tests drive this."""
    lowered = body or ""

    if http_status == 410:
        return Verdict(url, EXPIRED, "HTTP 410", http_status)
    if http_status == 404:
        return Verdict(url, DEAD, "HTTP 404", http_status)
    # Order matters: a bot wall can arrive as 403 *or* as a 200 page, and either
    # way it tells us nothing about the posting.
    if http_status in (401, 403, 429) or _BOT_WALL_TEXT.search(lowered):
        return Verdict(url, BLOCKED, f"bot wall or auth ({http_status})", http_status)
    if http_status >= 500 or http_status == 0:
        return Verdict(url, BLOCKED, f"upstream error ({http_status})", http_status)

    # A redirect away from the posting to a listing page is an expiry in
    # disguise. Compare paths, not whole URLs: query-string churn is normal.
    if final_url and final_url != url and _REDIRECT_TO_SEARCH.search(final_url):
        return Verdict(url, EXPIRED, f"redirected to {final_url}", http_status)

    if _EXPIRED_TEXT.search(lowered):
        return Verdict(url, EXPIRED, "page says the posting is closed", http_status)
    if _NOT_A_VACANCY_TEXT.search(lowered):
        return Verdict(url, NOT_A_VACANCY, "talent pool, not a real opening", http_status)
    if _INELIGIBLE_TEXT.search(lowered) and not _EXTERNAL_WELCOME.search(lowered):
        return Verdict(url, INELIGIBLE, "internal / restricted competition", http_status)

    # Nothing matched. Before calling it healthy, check the page actually had
    # something to read: a client-side shell renders chrome and no posting, and
    # reporting that as LIVE tells the operator a page is fine when nobody has
    # seen it. GNB's Oracle portal renders 561 characters of header and no job
    # description at all.
    if len(_visible_text(body)) < _THIN_PAGE_CHARS:
        return Verdict(
            url, BLOCKED, "page renders client-side; a human has to open it", http_status
        )
    return Verdict(url, LIVE, "", http_status)


# Statuses that may be a bot wall rather than the site's real answer.
_TLS_BLOCK_CODES = frozenset({403, 429})
_CURL_TRAILER = "\n__curl_status__ "


def _curl_probe(url: str, *, timeout: int = 25) -> tuple[int, str, str]:
    """(status, final_url, body) via the system curl; (0, "", "") if unavailable."""
    curl = shutil.which("curl")
    if not curl:
        return 0, "", ""
    try:
        done = subprocess.run(
            [
                curl, "-sSL", "--compressed", "--max-time", str(timeout),
                "-w", f"{_CURL_TRAILER}%{{http_code}} %{{url_effective}}", url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
    except (subprocess.SubprocessError, OSError):
        return 0, "", ""
    if done.returncode != 0:
        return 0, "", ""
    body, _, trailer = done.stdout.rpartition(_CURL_TRAILER)
    parts = trailer.split()
    if not parts or not parts[0].isdigit():
        return 0, "", ""
    return int(parts[0]), (parts[1] if len(parts) > 1 else ""), body


def check_url(url: str, client: httpx.Client) -> tuple[Verdict, str]:
    """Fetch one posting and classify it. Returns (verdict, page body).

    The body comes back so a second opinion can read the same page that
    produced the verdict, without fetching the site twice.
    """
    host = (urlparse(url).hostname or "").lower()
    if any(host == blocked or host.endswith("." + blocked) for blocked in _NO_FETCH_HOSTS):
        # Deliberately still not fetched, not even a HEAD: the operator's
        # account was restricted once for high-volume access. The cost of that
        # is that these rows are unverified, so the caller must say so rather
        # than fold them into "nothing dead" — see the triage summary line.
        return Verdict(url, SKIPPED, "host is not fetched by policy"), ""
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        return Verdict(url, BLOCKED, f"request failed: {type(exc).__name__}", 0), ""

    status, final_url, body = response.status_code, str(response.url), response.text
    if status in _TLS_BLOCK_CODES:
        # A 403 here is usually not the site saying no — it is an edge
        # fingerprinting httpx's TLS handshake, which no header set can
        # disguise. Measured 2026-08-16 on digitalnovascotia.com: httpx gets
        # 403 for every posting while curl gets the real status. That made the
        # whole host unverifiable, and BLOCKED counts as "not proven dead", so
        # a genuinely 404 posting rode to the top of triage and was evaluated
        # at full price. Ask curl before believing the wall.
        curl_status, curl_final, curl_body = _curl_probe(url)
        if curl_status:
            status, final_url, body = curl_status, curl_final or url, curl_body

    return classify(
        url,
        http_status=status,
        final_url=final_url,
        body=body,
    ), body


def check_urls(
    urls: list[str],
    *,
    client: httpx.Client | None = None,
    cache_path: Path = CACHE_PATH,
    delay_s: float = 1.0,
    sleep=time.sleep,
    today: date | None = None,
    use_cache: bool = True,
    confirm: bool = True,
    confirm_runner=None,
) -> dict[str, Verdict]:
    """Check many postings, politely, with caching and a second opinion.

    ``confirm`` is the safety gate. A rejection derived from **page text** is
    only acted on once an independent reader agrees twice; a rejection derived
    from an unambiguous HTTP status (404, 410) is taken as read. Any dissent,
    unparsable answer or transport failure downgrades the verdict to LIVE with
    the disagreement recorded, because keeping a dead posting costs two minutes
    and deleting a live one costs an opportunity.

    Terminal verdicts are cached; ``LIVE``, ``BLOCKED`` and ``SKIPPED`` are
    re-checked on every run because a posting that was open yesterday can close
    today.
    """
    stamp = (today or date.today()).isoformat()
    cache = _load_cache(cache_path) if use_cache else {}
    out: dict[str, Verdict] = {}
    pending: list[str] = []

    today_date = today or date.today()
    for url in urls:
        entry = cache.get(url) if use_cache else None
        if entry and entry.get("status") in REJECTING and _still_trusted(entry, today_date):
            out[url] = Verdict(
                url, entry["status"], entry.get("detail", ""), int(entry.get("http_status", 0))
            )
        else:
            pending.append(url)

    if not pending:
        return out

    owns_client = client is None
    if client is None:
        client = httpx.Client(
            headers={
                "User-Agent": _USER_AGENT,
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=25.0,
            follow_redirects=True,
        )
    try:
        for index, url in enumerate(pending):
            if index and delay_s > 0:
                sleep(delay_s)
            verdict, body = check_url(url, client)

            # Only text-derived rejections need a second opinion; 404 and 410
            # are not a matter of interpretation.
            if confirm and verdict.rejects and verdict.http_status not in (404, 410):
                confirmed, notes = confirm_rejection(verdict, body, runner=confirm_runner)
                if not confirmed:
                    verdict = Verdict(
                        url,
                        LIVE,
                        "kept: second opinion did not confirm — " + "; ".join(notes[-2:]),
                        verdict.http_status,
                    )

            out[url] = verdict
            cache[url] = {
                "status": verdict.status,
                "detail": verdict.detail,
                "http_status": verdict.http_status,
                "checked": stamp,
            }
    finally:
        if owns_client:
            client.close()

    if use_cache:
        _save_cache(cache_path, cache)
    return out


def _row_url(line: str) -> str:
    """The URL field of a pipeline row, or "" when the line is not a row."""
    body = line.partition("]")[2].strip()
    return body.split("|", 1)[0].strip()


def annotate_pipeline(path: Path, verdicts: dict[str, Verdict]) -> int:
    """Mark rejected postings in the pipeline file so they never rank again.

    Uses the file's existing ``- [!]`` convention and appends the reason and the
    date, matching how expired rows were marked by hand. Returns the number of
    rows changed. Rows already marked are left alone.

    ⚠️ The URL is matched **exactly against the row's own URL field**, never as
    a substring. Substring matching marked ``/job/10`` as dead because
    ``/job/1`` was — a live posting silently deleted, which is the one failure
    this whole module exists to prevent.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0

    rejected = {url: v for url, v in verdicts.items() if v.rejects}
    if not rejected:
        return 0

    stamp = date.today().isoformat()
    out: list[str] = []
    changed = 0
    for line in text.split("\n"):
        if line.startswith("- [ ]"):
            verdict = rejected.get(_row_url(line))
            if verdict is not None:
                note = f"{verdict.status.lower()} — verified {stamp}"
                if verdict.detail:
                    note += f" ({verdict.detail})"
                line = "- [!]" + line[len("- [ ]"):].rstrip() + f" | {note}"
                changed += 1
        out.append(line)

    if changed:
        # Atomic: the pipeline file is the operator's inbox. A half-written file
        # is worse than any verdict this module could get wrong.
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_text("\n".join(out), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            tmp.unlink(missing_ok=True)
            return 0
    return changed


# ---------------------------------------------------------------------------
# Second opinion before anything is deleted.
#
# The regex pass above is a deletion mechanism, and a deletion that is wrong is
# invisible: the operator never learns the posting existed. So no rejection is
# acted on until an independent reader confirms it — TWICE, on two separate
# calls, both of which must agree. Any disagreement, any unparseable answer and
# any transport failure keeps the posting.
#
# This is deliberately lopsided. Confirming costs two cheap-tier calls on a
# posting the regexes already believe is dead; the error it prevents costs a
# job opportunity.
# ---------------------------------------------------------------------------

CONFIRM_SYSTEM = """You verify one narrow factual question about a job posting. You are a
second opinion before an automated filter deletes this posting from a job seeker's list.

Deleting a posting he could have applied to is the expensive mistake. Keeping a dead one
costs him two minutes. When the page is ambiguous, truncated, or you are not sure, answer
KEEP. Only answer CONFIRM when the page plainly supports the claim."""

CONFIRM_PROMPT = """A filter wants to DELETE this job posting from the candidate's list.

Filter's reason: {reason}
Claim to verify: {claim}

Page text (may be truncated):
<<<PAGE_BEGIN>>>
{page}
<<<PAGE_END>>>

Does the page plainly support the claim?

Answer with exactly one line:
CONFIRM: <short quote from the page that proves it>
or
KEEP: <why the claim is not supported>"""

_CLAIMS = {
    DEAD: "this posting no longer exists (the page is a 404 or an error page)",
    EXPIRED: "this posting is closed, expired, or no longer accepting applications",
    INELIGIBLE: (
        "this competition is restricted so that an external applicant who is NOT already "
        "an employee of this organisation cannot apply, or will only be considered after "
        "internal candidates"
    ),
    NOT_A_VACANCY: (
        "this is a talent pool / candidate inventory / expression of interest, NOT a "
        "posting for a specific open position being filled now"
    ),
}

CONFIRMATIONS_REQUIRED = 2
# The operator has ample MiniMax quota, so this is set to show the model the
# whole page rather than a truncation. A confirmation made on a cut-off page
# is the one most likely to be wrong in the expensive direction.
_PAGE_BUDGET = 20000


def confirm_rejection(
    verdict: Verdict,
    body: str,
    *,
    runner=None,
    model: str = "MiniMax-M3",
    # M3 is a reasoning model: hidden reasoning is spent before the first visible
# token, so a small budget yields a truncated answer that parses as a refusal.
    max_tokens: int = 8000,
    timeout: int = 420,
    rounds: int = CONFIRMATIONS_REQUIRED,
) -> tuple[bool, list[str]]:
    """Ask an independent reader, ``rounds`` times, whether the deletion is right.

    Returns ``(confirmed, notes)``. ``confirmed`` is True only when every round
    came back CONFIRM. Anything else — one dissent, an unparsable reply, a
    transport error, no runner available — returns False and the posting stays.
    """
    claim = _CLAIMS.get(verdict.status)
    if claim is None:
        return False, ["no claim defined for this verdict"]

    page = _visible_text(body)[:_PAGE_BUDGET]
    if not page:
        return False, ["page had no readable text to confirm against"]

    prompt = CONFIRM_PROMPT.format(
        reason=verdict.detail or verdict.status, claim=claim, page=page
    )
    runner = runner or _run_confirm
    notes: list[str] = []
    for round_index in range(rounds):
        try:
            text, error = runner(prompt, model, max_tokens, timeout)
        except Exception as exc:  # noqa: BLE001 - never let this be fatal
            return False, notes + [f"round {round_index + 1}: {exc}"]
        if error:
            return False, notes + [f"round {round_index + 1}: {error}"]
        answer = (text or "").strip()
        match = re.search(r"\b(CONFIRM|KEEP)\b\s*:?\s*(.*)", answer, re.I | re.S)
        if not match:
            return False, notes + [f"round {round_index + 1}: unparsable reply"]
        decision = match.group(1).upper()
        note = re.sub(r"\s+", " ", match.group(2))[:160]
        notes.append(f"round {round_index + 1}: {decision} — {note}")
        if decision != "CONFIRM":
            return False, notes
    return True, notes


def _run_confirm(prompt: str, model: str, max_tokens: int, timeout: int) -> tuple[str, str]:
    if not shutil.which("mmx"):
        return "", "mmx not on PATH"
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump([{"role": "user", "content": prompt}], fh, ensure_ascii=False)
        path = fh.name
    try:
        proc = subprocess.run(
            ["mmx", "text", "chat", "--messages-file", path, "--model", model,
             "--system", CONFIRM_SYSTEM, "--max-tokens", str(max_tokens),
             "--temperature", "0.0", "--quiet"],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return "", f"confirmation timed out after {timeout}s"
    finally:
        Path(path).unlink(missing_ok=True)
    if proc.returncode != 0:
        return "", (proc.stderr or proc.stdout or "").strip()[:200]
    return proc.stdout, ""
