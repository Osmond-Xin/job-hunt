"""Rank the pipeline inbox down to a day's worth of candidates.

Discovery got good enough to be useless: 1,579 pending rows is not a list a
person reads, so the real filter became "whatever happened to be near the
bottom of the file". This ranks the inbox against the operator's standing
priorities instead, deterministically and with no model call — at this row
count an LLM pass is neither affordable nor necessary, because everything
that decides the ordering is already in the row.

Priorities, in the order they were established:

1. **Role shape.** Applied-AI work, and roles whose real content is one
   person owning everything, over generic engineering.
2. **Freshness.** A month-old posting on a board that reposts weekly is
   usually already filled.
3. **Direct employer over aggregator redirect.**

Geography scored and ordered rows here until 2026-09-03: a nomination-
qualifying province or territory outranked a major metro, which outranked
nothing in particular. That tier list was built for an immigration-path
ranking the operator's own later research reversed — his 2026-07-07 notes
rank the paths ① OINP (Ontario, including Toronto) ② RCIP ③ AIP, with AIP
tightening to ~4,000 national places — and it duplicated, badly, the region
list actually maintained at `profile/profile.yml::immigration_priority` and
read by `services/immigration.py`. The operator's own ruling settled it: he
looks for a matching job first and immigration second — "两个能一起解决最好，
但是不一起肯定先找工作" — so location has been removed from this file
entirely, score and tie-break both. Immigration still reaches him downstream,
in `services/immigration.py`, against the list that is actually kept current.

Exclusions are the categories that were measured to convert at zero: staffing
agencies, roles above the reachable level, clearance-gated defence work, and
anything already applied to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

_ROW_RE = re.compile(r"^- \[ \] (?P<rest>.+)$")
# Any row the file has already settled: `[x]` handled, `[!]` dead or closed.
_RESOLVED_ROW_RE = re.compile(r"^- \[[x!]\] (?P<rest>.+)$", re.I)
_POSTED_RE = re.compile(r"posted (\d{4}-\d{2}-\d{2})")

# Conversion measured at zero in the 2026-07 application review.
AGENCY_RE = re.compile(
    r"\b(accruetalent|tech talent|emergitel|carbon60|3pillar|robert half|randstad|teema|"
    r"insight global|aston carter|maarut|s\.?\s?i\.?\s?systems|procom|lorven|diverse lynx|"
    r"softpath|talentburst|apex systems|compunnel|nlb services|dexian|akkodis|experis|hays|"
    r"adecco|lancesoft|astra north|staffing|recruiting|recruitment|consulting services)\b",
    re.I,
)
# Above the level the operator can currently reach.
TOO_SENIOR_RE = re.compile(
    r"\b(director|vice president|\bvp\b|head of|principal|staff engineer|chief|"
    r"distinguished|fellow|partner)\b",
    re.I,
)
# Canadian security clearance generally requires citizenship or PR.
CLEARANCE_RE = re.compile(
    r"\b(anvil|thales|general dynamics|lockheed|raytheon|calian|babcock|"
    r"secret clearance|security clearance|controlled goods)\b",
    re.I,
)
# Explicitly excluded job function.
EXCLUDED_FUNCTION_RE = re.compile(r"\b(procurement|purchasing|buyer)\b", re.I)
# The company field holds a search-result title, not an employer. A websearch
# hit on an aggregator is titled "<role> - <city, province> - <site>", so the
# half after the first separator is a location and the employer is nowhere in
# the row: "Greater Sudbury, ON P3A 5N8 - Indeed.com", "Halifax, NS - Job
# posting - Job Bank". Two of these ranked in the top ten on 2026-09-04, and
# neither could be evaluated — Indeed answers 401 and nobody knows who the
# employer is, so there is nothing to tailor a résumé to.
UNIDENTIFIED_EMPLOYER_RE = re.compile(
    r"(indeed\.com|glassdoor|ziprecruiter|jooble|talent\.com|simplyhired|neuvoo|"
    r"jobillico|job posting - job bank)",
    re.I,
)
LOCATION_AS_COMPANY_RE = re.compile(
    r"^[^,|]+,\s*(AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT)\b", re.I
)
# Hosts that are a hop to the posting rather than the posting: the employer
# name, the location and sometimes the role come from the aggregator's own page
# furniture, and two of them (LinkedIn, Indeed) cannot be fetched at all.
AGGREGATOR_HOST_RE = re.compile(
    r"^https?://(?:[^/]*\.)?(?:linkedin\.com|indeed\.[a-z.]+|adzuna\.[a-z.]+|jobbank\.gc\.ca|"
    r"glassdoor\.[a-z.]+|wellfound\.com|angel\.co|startup\.jobs|welcometothejungle\.com|"
    r"ziprecruiter\.[a-z.]+|jooble\.org|talent\.com|simplyhired\.[a-z.]+|neuvoo\.[a-z.]+|"
    r"jobillico\.com|monster\.[a-z.]+|careerbeacon\.com|jobbank\.gc\.ca)/",
    re.I,
)
# The row's "role" is a search-listing title, not one posting: a wellfound
# category page ("Machine Learning Engineer Jobs in Canada"). There is no
# single job behind these to evaluate. LinkedIn's "<Company> hiring <Role> in
# <City>" is NOT one of these — it names exactly one posting, and dropping it
# hid Symend's Senior Machine Learning Engineer, a Calgary target the operator
# had been looking for. That title is unpacked below instead.
LISTING_TITLE_RE = re.compile(r"\bjobs in\b|\bjob posting\b", re.I)
# LinkedIn titles its posting pages "<Company> hiring <Role> in <City, Region,
# Country>", and a websearch row copies the whole line into the role field. The
# employer and the role are both in there; this takes them back out.
# The " in <place>" tail is required, and "hiring" is matched in lower case:
# both are part of the convention, and without them the pattern also eats a
# real title like "Engineering Manager, Hiring Platform".
LINKEDIN_TITLE_RE = re.compile(r"^(?P<company>.{2,60}?) hiring (?P<role>.+?) in .+$")
# French is a hard requirement in these, and the operator has none. Only a role
# that says so in its own title is dropped — "bilingualism an asset" in a JD
# body is a different thing and is not read here.
FRENCH_REQUIRED_RE = re.compile(r"\bbilingual\b|french language|\bfrançais\b", re.I)
# Operator's decision, 2026-08-13: "大厂不缺我这样的人" — a bank or a global
# consultancy has a full funnel of candidates who look like him on paper and
# screens on credentials he does not have. A twelve-person company in Halifax
# is choosing between three applicants. Governments are exempt: a public
# competition is scored against stated qualifications, not against a queue.
BIG_EMPLOYER_RE = re.compile(
    r"\b(rbc|royal bank|td bank|toronto-dominion|scotiabank|bmo|bank of montreal|cibc|"
    r"national bank|desjardins|manulife|sun life|canada life|intact|aviva|"
    r"deloitte|kpmg|pwc|pricewaterhouse|ernst|accenture|cgi group|cgi information|"
    r"capgemini|infosys|wipro|tcs|cognizant|synechron|softchoice|"
    r"ibm|microsoft|amazon|aws|google|meta|apple|oracle|sap|salesforce|nvidia|intel|"
    r"telus|bell canada|rogers|shaw|shopify|loblaw|sobeys|walmart|costco|"
    r"air canada|cn rail|canadian tire|magna|bombardier|resmed|instacart|affirm|"
    r"lockheed|boeing|siemens|ge |general electric)\b",
    re.I,
)

GOVERNMENT_RE = re.compile(
    r"\b(government|gouvernement|province of|city of|municipality|public service|"
    r"health authority|school district|crown corporation)\b",
    re.I,
)
AI_ROLE_RE = re.compile(
    r"\b(ai|a\.i\.|artificial intelligence|machine learning|\bml\b|llm|genai|generative|"
    r"rag|nlp|data scien|forward deployed|applied scien)\b",
    re.I,
)
SOLO_ROLE_RE = re.compile(
    r"\b(first technical hire|sole developer|only developer|founding engineer|generalist|"
    r"digital transformation|automation specialist|systems analyst|solutions? (engineer|"
    r"architect|specialist)|technical lead|full[- ]stack)\b",
    re.I,
)
ADJACENT_ROLE_RE = re.compile(
    r"\b(data engineer|data analyst|software (engineer|developer)|backend|integration|"
    r"business analyst|product manager|analyst|developer|engineer)\b",
    re.I,
)


@dataclass(frozen=True)
class PipelineRow:
    url: str
    company: str
    role: str
    location: str
    posted: str
    source: str

    @property
    def haystack(self) -> str:
        return f"{self.company} {self.role} {self.location}"


@dataclass(frozen=True)
class Ranked:
    row: PipelineRow
    score: float
    reasons: tuple[str, ...]


def parse_pipeline(text: str) -> list[PipelineRow]:
    """Unchecked ``- [ ]`` rows, newest first as the file stores them.

    A URL the file has already settled stays settled. Discovery appends by URL
    but writes whatever company name its source used, so the same posting comes
    back under a new name and no dedupe key matches: two Nova Scotia geomatics
    postings verified closed in April were re-added twenty times as "NS Public
    Service" instead of "NS Geomatics Centre" and climbed back into the
    shortlist months after they closed.
    """
    resolved = {
        match.group("rest").split("|")[0].strip()
        for line in text.splitlines()
        if (match := _RESOLVED_ROW_RE.match(line.strip()))
    }
    rows: list[PipelineRow] = []
    for line in text.splitlines():
        match = _ROW_RE.match(line.strip())
        if not match:
            continue
        if match.group("rest").split("|")[0].strip() in resolved:
            continue
        parts = [part.strip() for part in match.group("rest").split("|")]
        if not parts or not parts[0].startswith("http"):
            continue
        posted = ""
        source = ""
        for part in parts[3:]:
            posted_match = _POSTED_RE.search(part)
            if posted_match:
                posted = posted_match.group(1)
            if part.startswith("source:"):
                source = part.removeprefix("source:").strip()
        company, role = _unpack_board_title(
            parts[1] if len(parts) > 1 else "",
            parts[2] if len(parts) > 2 else "",
        )
        rows.append(
            PipelineRow(
                url=parts[0],
                company=company,
                role=role,
                location=parts[3] if len(parts) > 3 else "",
                posted=posted,
                source=source,
            )
        )
    return rows


def _unpack_board_title(company: str, role: str) -> tuple[str, str]:
    """Recover (company, role) when the role field is a board's page title.

    A websearch row copies the search result's whole title line, so LinkedIn's
    "Symend hiring Senior Machine Learning Engineer in Calgary, Alberta,
    Canada" arrives as the role. Both halves are in there. The company found
    this way only fills an empty or parenthesised company field — the row's own
    company is better evidence when it has one.
    """
    match = LINKEDIN_TITLE_RE.match(role)
    if not match:
        return company, role
    found = match.group("company").strip()
    if not company or _norm(found).startswith(_norm(company).split(" ")[0]):
        company = found
    return company, match.group("role").strip()


def excluded(row: PipelineRow) -> str:
    """Reason this row should never reach the operator, or ``""``."""
    if AGENCY_RE.search(row.company):
        return "staffing agency"
    if TOO_SENIOR_RE.search(row.role):
        return "above reachable level"
    if CLEARANCE_RE.search(row.haystack):
        return "clearance-gated"
    if EXCLUDED_FUNCTION_RE.search(row.role):
        return "excluded function"
    if UNIDENTIFIED_EMPLOYER_RE.search(row.company) or LOCATION_AS_COMPANY_RE.match(row.company):
        return "employer not identified"
    if LISTING_TITLE_RE.search(row.role):
        return "a listing page, not one posting"
    if FRENCH_REQUIRED_RE.search(row.role):
        return "French required"
    if BIG_EMPLOYER_RE.search(row.company) and not GOVERNMENT_RE.search(row.company):
        return "large employer"
    return ""


def score(row: PipelineRow, *, today: date | None = None) -> tuple[float, list[str]]:
    """Priority score and the reasons behind it. Higher is better."""
    points = 0.0
    reasons: list[str] = []

    # 1. Geography scored here until 2026-09-03, on a scale that inverted the
    #    priority it was meant to serve: a nomination-qualifying region was
    #    worth +1.5, a major metro only +0.5 — less than an unrecognised
    #    location's flat +1.0 — so a Brandon equipment operator (7.5)
    #    outranked Mistral's Applied AI forward-deployed role in Montréal
    #    (4.0), and 130 of the top 300 rows were government postings. That
    #    was already the second version of the rule, tightened once from an
    #    even worse +4 spread; the tightening didn't fix the premise. The
    #    operator's ruling, 2026-09-03: "两个能一起解决最好，但是不一起肯定
    #    先找工作" — a job match first, immigration second, always — and the
    #    tiers here were built for an immigration-path ranking his own later
    #    research reversed (see the module docstring). Geography no longer
    #    scores or orders anything in this file; immigration still reaches
    #    him, downstream of triage, through `services/immigration.py`
    #    against the region list that is actually kept current.
    # 1b. Public sector, but only for a role that is already on target. The
    #     point is a preference between comparable jobs, not a rescue: paid
    #     flat, it let a service-desk analyst and a bilingual French language
    #     services officer tie with every applied-AI role in the inbox on
    #     2026-09-04, which is the same plateau the tier fix below removes.
    on_target = bool(AI_ROLE_RE.search(row.role) or SOLO_ROLE_RE.search(row.role))
    if on_target and GOVERNMENT_RE.search(row.company):
        points += 1
        reasons.append("public sector")

    # 2. Role shape, the dominant term now that geography scores nothing.
    #
    #    The three vocabularies are TIERS — applied AI beats one-person scope
    #    beats merely adjacent — so exactly one of them scores. They used to
    #    accumulate, and that inverted the tier list they were meant to
    #    express: "Full Stack Engineer" collected one-person scope (+2) *and*
    #    adjacent (+1) for 3.0, while "Forward Deployed Engineer" collected
    #    applied AI (+3) alone for 3.0 — and lost the direct-source half point
    #    on top, because the FDE roles come through aggregators. Measured
    #    2026-09-04: every one of the twelve rows triage surfaced was a generic
    #    full-stack post, while 631 pending applied-AI and forward-deployed
    #    rows — the operator's actual track — sat below the cut.
    #
    #    A row matching none of the three is not a near miss: it is an
    #    equipment operator, a collections officer, a language coordinator.
    if AI_ROLE_RE.search(row.role):
        points += 3
        reasons.append("applied AI")
    elif SOLO_ROLE_RE.search(row.role):
        points += 2
        reasons.append("one-person scope")
    elif ADJACENT_ROLE_RE.search(row.role):
        points += 1
    else:
        points -= 2
        reasons.append("off-target role")

    # 3. Freshness. A month-old posting on a board that reposts weekly is
    #    usually already filled.
    if row.posted:
        try:
            age = ((today or date.today()) - datetime.strptime(row.posted, "%Y-%m-%d").date()).days
        except ValueError:
            age = None
        if age is not None:
            if age <= 7:
                points += 1
                reasons.append("posted this week")
            elif age <= 14:
                points += 0.5
            elif age > 30:
                points -= 1
                reasons.append("stale")

    # 4. A direct employer beats an aggregator redirect: the aggregator link
    #    is a hop, and its employer field has been wrong before.
    #
    #    Read off the URL, not the `source:` label. The label says where the
    #    row was found, and "websearch" — which is most of them — is not a
    #    source at all: on 2026-09-04 it collected this half point for rows
    #    whose URL was linkedin.com, a host this system will not fetch under
    #    any circumstances, and for wellfound pages that carry the operator's
    #    own city as the job's location. Those outranked postings on the
    #    employer's own ATS, which are the ones that can actually be read.
    if not AGGREGATOR_HOST_RE.search(row.url):
        points += 0.5

    return points, reasons


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", value.lower()).strip()


def tracker_seen(tracker_text: str) -> tuple[set[str], set[tuple[str, str]]]:
    """URLs, and (company, role) pairs, already in the tracker.

    The tracker's URL column is usually empty — the apply link lives in the
    free-text notes — so every http token in the file counts as seen.

    Matching on the company alone would be wrong: one GNB competition is not a
    reason to hide the rest of the New Brunswick civil service. The pair is the
    unit, compared loosely because the two sides never agree on wording —
    "Government of New Brunswick — Finance and Treasury Board, OCIO" against
    "Government of New Brunswick", and "AI Integration and Automation
    Specialist (Competition 16958)" against the bare title.
    """
    urls = set(re.findall(r"https?://[^\s|)\]]+", tracker_text))
    pairs: set[tuple[str, str]] = set()
    for line in tracker_text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) <= 4 or cells[3] == "Company":
            continue
        company, role = _norm(cells[3]), _norm(cells[4])
        # An empty cell would normalise to "" and, through startswith(""),
        # match every row in the pipeline — which silently emptied the list.
        if len(company) >= 3 and len(role) >= 3:
            pairs.add((company, role))
    return urls, pairs


#: Tracker statuses that mean an application was actually sent. `Evaluated` and
#: `SKIP` are materials built and a decision not to send. `Discarded` is
#: ambiguous in the file's own history — it covers both "withdrew after
#: applying" and "decided against before applying" — so it does not hide an
#: employer; the pair rule still catches the exact posting either way.
SUBMITTED_STATUSES = frozenset({"applied", "interview", "responded", "rejected", "offer"})


def submitted_employers(tracker_text: str) -> dict[str, str]:
    """Normalised employer -> the tracker row already sent to that employer.

    The standing rule is one role per employer: chase the closest-fit posting,
    and if that one does not land, its siblings at the same company are not
    worth the postage. The (company, role) pair alone cannot enforce it — a
    different title at the same employer matches nothing, which is how
    Procurify, Irving Oil and Signal 1 all came back on 2026-09-04 after being
    applied to, and how the same Connor, Clark & Lunn posting came back a
    sixth day later under the affiliate name its aggregator used.

    Governments are exempt, deliberately: a public competition is scored
    against stated qualifications, so one GNB competition is not a reason to
    hide the rest of the New Brunswick civil service. Those stay on the pair.
    """
    employers: dict[str, str] = {}
    for line in tracker_text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) <= 6 or cells[3] == "Company":
            continue
        company, status = cells[3], cells[6].lower()
        if status not in SUBMITTED_STATUSES or GOVERNMENT_RE.search(company):
            continue
        key = _norm(company)
        if len(key) >= 4:
            employers.setdefault(key, cells[1] or "?")
    return employers


def applied_employer(row: PipelineRow, employers: dict[str, str]) -> str:
    """The tracker row already sent to this row's employer, or ``""``.

    The two sides never agree on wording — "PointClickCare" against
    "PointClickCare Technologies", "J.D. Irving" against "J.D. Irving,
    Limited" — so one name may extend the other, but only at a word boundary.
    A character prefix would be enough to hide "Citizens Bank" because "Citi"
    is in the tracker, and this rule deletes rows the operator never sees.

    It cannot catch an employer that appears under an unrelated trading name —
    an aggregator called Connor, Clark & Lunn "FortWood Capital LP" — and
    nothing string-shaped can.
    """
    company = _norm(row.company)
    if len(company) < 4:
        return ""
    for tracked, entry in employers.items():
        if company == tracked or company.startswith(tracked + " ") or tracked.startswith(company + " "):
            return entry
    return ""


def already_applied(row: PipelineRow, pairs: set[tuple[str, str]]) -> bool:
    """True when this row is a posting the tracker already covers."""
    company, role = _norm(row.company), _norm(row.role)
    if len(company) < 3 or len(role) < 3:
        return False
    for tracked_company, tracked_role in pairs:
        company_key = tracked_company[:18]
        role_key = tracked_role[:20]
        if not (company.startswith(company_key) or tracked_company.startswith(company[:18])):
            continue
        if role.startswith(role_key) or tracked_role.startswith(role[:20]):
            return True
    return False


def rank(
    rows: list[PipelineRow],
    *,
    limit: int = 10,
    seen_urls: set[str] | None = None,
    seen_pairs: set[tuple[str, str]] | None = None,
    seen_employers: dict[str, str] | None = None,
    today: date | None = None,
) -> list[Ranked]:
    """Best `limit` rows, dropping anything already applied to or duplicated."""
    seen_urls = seen_urls or set()
    seen_pairs = seen_pairs or set()
    seen_employers = seen_employers or {}
    ranked: list[Ranked] = []
    pairs: set[tuple[str, str]] = set()
    for row in rows:
        if row.url in seen_urls or excluded(row):
            continue
        if already_applied(row, seen_pairs) or applied_employer(row, seen_employers):
            continue
        pair = (row.company.lower(), row.role.lower())
        # Boards repost the same job under several locations; one is enough.
        if pair in pairs:
            continue
        pairs.add(pair)
        points, reasons = score(row, today=today)
        ranked.append(Ranked(row=row, score=points, reasons=tuple(reasons)))
    # Geography broke ties here until 2026-09-03 (see score() above and the
    # module docstring for why that stopped). Ties now fall to freshness,
    # then company name — both already inherent to the row, neither of them
    # a location, and both stable regardless of the order rows arrived in.
    #
    # Freshness sorted on the raw string until 2026-09-04, ascending: the
    # oldest posting won the tie, and a row with no date at all — "" sorts
    # before any date — won it outright. Most rows carry no date, so the tie
    # was decided by company name alphabetically, which is how a pool of 200
    # returned nothing from Calgary while 290 Calgary rows waited in the inbox.
    ranked.sort(
        key=lambda item: (
            -item.score,
            _freshness_key(item.row.posted),
            item.row.company,
        )
    )
    return ranked[:limit]


def _freshness_key(posted: str) -> tuple[int, int]:
    """Newest first; a row with no usable date sorts last, never first."""
    try:
        return 0, -datetime.strptime(posted, "%Y-%m-%d").date().toordinal()
    except ValueError:
        return 1, 0
