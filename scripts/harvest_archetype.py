"""Daily harvest of the Protocase archetype.

The archetype, named for the role the operator picked out on 2026-09-05
(Protocase Inc., AI Engineer (Agentic Workflows), Sydney NS): an applied-AI
engineer who builds agentic and RAG workflows ON TOP of existing foundation
models, at an ordinary company rather than an AI lab, mid-level, ideally
on-site outside Toronto. Protocase's JD even says the role "does not require
deep expertise in training or fine-tuning models from scratch" — that sentence
is the archetype in one line.

Why this exists next to `job-hunt triage`: triage ranks the pipeline inbox on
the job TITLE, because that is all a pipeline row carries. This screens on the
Adzuna DESCRIPTION, so it finds the work under titles the vocabulary would miss
and rejects AI-titled roles whose actual content is model training or research.
Adzuna's `what_phrase` is an exact-phrase match over title+description, which
is what makes that possible.

Usage:  .venv/bin/python scripts/harvest_archetype.py
Writes the full result to `harvest.json` beside the script and prints the
shortlist. Costs no search quota (Adzuna is free); ~2 minutes.

Rows already in `data/pipeline.md` are KEPT and flagged `pipe` — nobody has
read those 8k rows, so being in the inbox is not a reason to hide a match.
Only an employer already applied to is dropped.
"""
import os, re, json, time, sys
from pathlib import Path
import httpx

# Run from the repo root whichever directory it was invoked from: the .env
# read below, the `job_hunt` import and `data/pipeline.md` are all relative.
REPO_ROOT = Path(__file__).resolve().parent.parent
os.chdir(REPO_ROOT)
sys.path.insert(0, str(REPO_ROOT))
for line in Path(".env").read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
from job_hunt.services.triage import excluded, PipelineRow, submitted_employers, applied_employer

APP_ID = os.environ["ADZUNA_APP_ID"]; APP_KEY = os.environ["ADZUNA_APP_KEY"]
ENDPOINT = "https://api.adzuna.com/v1/api/jobs/ca/search/{page}"

# Content phrases that identify the archetype. `what_phrase` is an exact-phrase
# match over title+description, so these find the work regardless of job title.
PHRASES = [
    "agentic workflows", "agentic AI", "AI agents", "retrieval-augmented generation",
    "RAG pipelines", "LLM APIs", "large language models", "prompt engineering",
    "vector database", "LangChain", "LangGraph", "AI automation",
    "foundation models", "tool calling", "AI workflows",
]
# Plain title queries for the same lane.
TITLES = [
    "AI Engineer", "AI Developer", "Applied AI Engineer", "AI Solutions Engineer",
    "Forward Deployed Engineer", "AI Automation Engineer", "Machine Learning Engineer",
]

# The work itself must be building ON existing models.
SIGNAL = re.compile(
    r"\b(agentic|ai agent|llm|large language model|rag\b|retrieval[- ]augmented|"
    r"langchain|langgraph|vector (database|store|search)|prompt (engineering|design)|"
    r"foundation model|tool[- ]calling|openai|anthropic|claude|gpt-)", re.I)
# Research / training roles are the opposite of this archetype.
RESEARCH = re.compile(
    r"\b(phd|ph\.d|publications?\b|research scientist|from scratch|pre-?train|"
    r"pretraining|cuda|distributed training|novel architectures?|peft|lora\b)", re.I)
AGENCY_EXTRA = re.compile(
    r"\b(jobgether|mercor|turing|mindrift|upwork|lifted|cynet|actalent|vdart|"
    r"cyber ?coders|talentlab|career renew|inizio|upstaff|kelly services|avance|"
    r"excelgens|us tech|innova|pacer group|vtech|whopper|i8is|thri5|veracity|"
    r"hire ?digitalent|hirevouch|nearsource|acestack|keelen|equest|prolific|"
    r"vaco|jobgoal|scalian|quantum|altis|bilingual source|raise|swoon|motion recruit|"
    r"tundra|ian martin|calian|collabera|photon|mastech|zortech|net2source|"
    r"artech|ampstek|infojini|intelliswift|kforce|judge group|lorien|harvey nash)\b", re.I)
# Pure sales / marketing / recruiting, even when the product is AI.
SALES_RE = re.compile(
    r"\b(account executive|sales engineer|pre[- ]?sales|sales representative|"
    r"business development|account manager|marketing|demand gen|growth marketer|"
    r"recruiter|talent acquisition|customer success manager|partnerships?)\b", re.I)
FRENCH = re.compile(r"(bilingu|français|francais|maîtrise du français)", re.I)
# Stacks the operator does not have. A mention is not always a requirement, so
# this only FLAGS the row — but read the required-skills list before applying.
# Twice now a role was shortlisted off its responsibilities section and only
# later found to demand a language he has never used: MNP (C#/.NET/Angular,
# 2026-09-04) and Shift Technology ("2+ years of experience in C# and SQL" as
# the first must-have, 2026-09-05).
STACK_GAP = re.compile(
    r"\b(c#|\.net|asp\.net|angular|salesforce|\bgolang\b|\brust\b|kotlin|swift|"
    r"scala|ruby on rails|php|dynamics 365|sharepoint|powerapps|uipath|"
    r"pytorch|tensorflow|kubernetes|terraform)\b", re.I)
TORONTO = re.compile(
    r"\b(toronto|mississauga|markham|brampton|scarborough|etobicoke|north york|"
    r"vaughan|richmond hill|regent park|east york|don mills|downsview)\b", re.I)
REMOTE = re.compile(r"\b(remote|telework|télétravail|work from home)\b", re.I)


def fetch(params, page=1):
    q = {"app_id": APP_ID, "app_key": APP_KEY, "results_per_page": 50,
         "max_days_old": 30, "sort_by": "date", **params}
    try:
        r = httpx.get(ENDPOINT.format(page=page), params=q, timeout=25)
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}


def known_urls():
    urls = set()
    for p in ("data/pipeline.md", "data/applications.md", "data/scan-history.tsv"):
        f = Path(p)
        if f.exists():
            urls.update(re.findall(r"adzuna\.ca/(?:details|land/ad)/(\d+)", f.read_text(encoding="utf-8")))
    return urls


def known_companies():
    return submitted_employers(Path("data/applications.md").read_text(encoding="utf-8"))


def main():
    seen_ids, rows = set(), []
    seen = known_urls(); employers = known_companies()
    queries = [("what_phrase", p) for p in PHRASES] + [("what", t) for t in TITLES]
    for i, (key, val) in enumerate(queries):
        for page in (1, 2):
            payload = fetch({key: val}, page)
            results = payload.get("results") or []
            if not results:
                break
            for r in results:
                jid = re.search(r"/(\d+)\?", r.get("redirect_url", ""))
                jid = jid.group(1) if jid else r.get("id")
                if not jid or jid in seen_ids:
                    continue
                seen_ids.add(jid)
                rows.append({
                    "id": jid,
                    "title": r.get("title", "").strip(),
                    "company": (r.get("company") or {}).get("display_name", "").strip(),
                    "location": (r.get("location") or {}).get("display_name", "").strip(),
                    "created": (r.get("created") or "")[:10],
                    "salary_min": r.get("salary_min"), "salary_max": r.get("salary_max"),
                    "desc": r.get("description", ""),
                    "url": r.get("redirect_url", ""),
                    "query": f"{key}={val}",
                })
            time.sleep(0.35)
        print(f"  [{i+1}/{len(queries)}] {key}={val}: cumulative {len(rows)}", file=sys.stderr)

    kept = []
    for r in rows:
        blob = f"{r['title']} {r['desc']}"
        row = PipelineRow(url=r["url"], company=r["company"], role=r["title"],
                          location=r["location"], posted=r["created"], source="adzuna")
        why = excluded(row)
        if why:
            r["drop"] = why
        elif AGENCY_EXTRA.search(r["company"]):
            r["drop"] = "agency/marketplace"
        elif FRENCH.search(blob):
            r["drop"] = "French required"
        elif not SIGNAL.search(blob):
            r["drop"] = "no LLM/agent signal"
        elif RESEARCH.search(blob):
            r["drop"] = "research/training role"
        elif SALES_RE.search(r["title"]):
            r["drop"] = "sales/marketing function"
        elif applied_employer(row, employers):
            r["drop"] = "employer already applied to"
        else:
            # Being in the pipeline is not a reason to hide it — nobody has
            # read those 8k rows. Flag it and keep it in the shortlist.
            r["drop"] = ""
            r["in_pipeline"] = r["id"] in seen
            r["stack_gap"] = sorted({m.group(0).lower() for m in STACK_GAP.finditer(blob)})
            kept.append(r)

    def rank(r):
        s = 0.0
        if not REMOTE.search(r["location"] + " " + r["title"]):
            s += 1.0
        if not TORONTO.search(r["location"]):
            s += 1.0
        if re.search(r"\b(agentic|ai agent|rag\b|retrieval[- ]augmented)\b", r["desc"], re.I):
            s += 1.5
        if re.search(r"\b(build|design|develop|ship|implement|integrat)", r["desc"], re.I):
            s += 0.5
        if not r.get("in_pipeline"):
            s += 0.25
        if re.search(r"not require.{0,40}(fine[- ]tun|training)|no.{0,20}fine[- ]tuning", r["desc"], re.I):
            s += 1.0
        if r["salary_min"]:
            s += 0.5
        return -s, r["created"]

    kept.sort(key=rank)
    out = Path(__file__).parent / "harvest.json"
    out.write_text(json.dumps({"kept": kept, "all": rows}, indent=1))
    from collections import Counter
    print(f"\nfetched {len(rows)} unique · kept {len(kept)}", file=sys.stderr)
    print(Counter(r["drop"] for r in rows if r["drop"]).most_common(), file=sys.stderr)
    for r in kept[:80]:
        sal = f"{int(r['salary_min']//1000)}-{int(r['salary_max']//1000)}k" if r["salary_min"] else "-"
        flag = "pipe" if r.get("in_pipeline") else "NEW "
        gap = (" ⚠ " + ",".join(r["stack_gap"][:4])) if r.get("stack_gap") else ""
        print(f"{flag} {r['created']} | {r['company'][:28]:28s} | {r['title'][:50]:50s} | {r['location'][:26]:26s} | {sal:9s} | {r['id']}{gap}")


main()
