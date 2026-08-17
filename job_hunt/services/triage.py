"""Rank the pipeline inbox down to a day's worth of candidates.

Discovery got good enough to be useless: 1,579 pending rows is not a list a
person reads, so the real filter became "whatever happened to be near the
bottom of the file". This ranks the inbox against the operator's standing
priorities instead, deterministically and with no model call — at this row
count an LLM pass is neither affordable nor necessary, because everything
that decides the ordering is already in the row.

Priorities, in the order they were established:

1. **Immigration value first.** A permanent role in an AIP province, a
   territory, or an SINP/MPNP province outranks a better-matched job in
   Toronto, because the offer is what converts into status.
2. **Non-major-metro next.** Toronto, Vancouver and Montréal are ranked
   last, not excluded — the operator cleared Toronto explicitly once the
   remote pool thinned.
3. **Role shape.** Applied-AI work, and roles whose real content is one
   person owning everything, over generic engineering.

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

# AIP provinces, the three territories, and the two Prairie PNP provinces.
TIER_A_RE = re.compile(
    r"\b(nova scotia|new brunswick|prince edward|newfoundland|labrador|halifax|dartmouth|"
    r"sydney|moncton|fredericton|saint john|charlottetown|st\.? john's|"
    r"northwest territories|yukon|nunavut|yellowknife|whitehorse|iqaluit|inuvik|"
    r"saskatchewan|manitoba|saskatoon|regina|winnipeg|brandon|nb|ns|pe|nl|nt|yt|nu|sk|mb)\b",
    re.I,
)
TIER_C_RE = re.compile(
    r"\b(toronto|vancouver|montr[ée]al|greater toronto|gta|mississauga|brampton|markham|"
    r"north york|scarborough|etobicoke|vaughan|richmond hill|burnaby|surrey)\b",
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
        rows.append(
            PipelineRow(
                url=parts[0],
                company=parts[1] if len(parts) > 1 else "",
                role=parts[2] if len(parts) > 2 else "",
                location=parts[3] if len(parts) > 3 else "",
                posted=posted,
                source=source,
            )
        )
    return rows


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
    if BIG_EMPLOYER_RE.search(row.company) and not GOVERNMENT_RE.search(row.company):
        return "large employer"
    return ""


def score(row: PipelineRow, *, today: date | None = None) -> tuple[float, list[str]]:
    """Priority score and the reasons behind it. Higher is better."""
    points = 0.0
    reasons: list[str] = []

    # 1. Immigration value dominates: a nomination-qualifying region is worth
    #    more than a better-matched posting in a city with no path.
    if TIER_A_RE.search(row.location):
        points += 4
        reasons.append("PNP/AIP region")
    elif TIER_C_RE.search(row.location):
        reasons.append("major metro")
    else:
        points += 1.5
    if GOVERNMENT_RE.search(row.company):
        points += 2
        reasons.append("public sector")

    # 2. Role shape.
    if AI_ROLE_RE.search(row.role):
        points += 3
        reasons.append("applied AI")
    if SOLO_ROLE_RE.search(row.role):
        points += 2
        reasons.append("one-person scope")
    if not AI_ROLE_RE.search(row.role) and ADJACENT_ROLE_RE.search(row.role):
        points += 1

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
    if row.source and row.source not in {"adzuna", "jobbank", "linkedin", "indeed"}:
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
    today: date | None = None,
) -> list[Ranked]:
    """Best `limit` rows, dropping anything already applied to or duplicated."""
    seen_urls = seen_urls or set()
    seen_pairs = seen_pairs or set()
    ranked: list[Ranked] = []
    pairs: set[tuple[str, str]] = set()
    for row in rows:
        if row.url in seen_urls or excluded(row):
            continue
        if already_applied(row, seen_pairs):
            continue
        pair = (row.company.lower(), row.role.lower())
        # Boards repost the same job under several locations; one is enough.
        if pair in pairs:
            continue
        pairs.add(pair)
        points, reasons = score(row, today=today)
        ranked.append(Ranked(row=row, score=points, reasons=tuple(reasons)))
    ranked.sort(key=lambda item: (-item.score, item.row.posted or "", item.row.company))
    return ranked[:limit]
