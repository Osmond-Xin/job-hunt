"""Daily harvest of the forward-deployed lane.

Why this exists next to `harvest_archetype.py`: that one screens for applied-AI
work built on top of foundation models. This one screens for a *shape of job* —
an engineer who sits with the customer, turns a vague requirement into something
running, and owns it end to end. The two overlap but neither contains the other:
a forward-deployed role at a data-infrastructure company mentions no LLM at all,
and plenty of agentic-AI roles never leave the office.

The lane earns its own script because it is the one that converts. Of every
real conversation the tracker has produced, the forward-deployed shape accounts
for nearly all of them — Vendasta FDE (behavioural round), Hiveway FDSE (first
round), TribalScale FDE (screening), Gallea (forward-deployed proposal),
PerfectServe FDE. That is a small sample and it is still the clearest signal in
394 rows.

The hard part is that the title is not stable. "Forward Deployed Engineer" is
the Palantir coinage and only some companies use it; the same job ships as
Implementation Engineer, Deployment Engineer, Solutions Engineer, Integration
Engineer, Customer Engineer, Professional Services Engineer, Delivery Engineer.
So this searches titles *and* the phrases that describe the work, then throws
out the ones that are actually sales.

That last filter is the whole game. "Solutions Engineer" is a pre-sales quota
job at least as often as it is an engineering job, and the difference does not
show up in the title — it shows up in whether the description talks about
quota, pipeline and demos, or about shipping code in a customer's environment.

Usage:  .venv/bin/python scripts/harvest_fde.py
Costs no search quota (Adzuna is free); ~1 minute.
"""
import os, re, json, time, sys
from pathlib import Path
import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
os.chdir(REPO_ROOT)
sys.path.insert(0, str(REPO_ROOT))
for line in Path(".env").read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
from job_hunt.services.triage import excluded, PipelineRow, submitted_employers  # noqa: E402

APP_ID = os.environ["ADZUNA_APP_ID"]; APP_KEY = os.environ["ADZUNA_APP_KEY"]
ENDPOINT = "https://api.adzuna.com/v1/api/jobs/ca/search/{page}"

# Phrases describing the work, matched as exact phrases over title+description.
# Deliberately narrow. "technical implementation" and "solution delivery" were
# here for one run and pulled in civil, structural and process engineering by
# the hundred — in construction consulting those words mean something else
# entirely, and Adzuna cannot tell the industries apart.
PHRASES = [
    "forward deployed", "forward-deployed", "deployed engineer",
    "customer-facing engineer", "embed with customers", "embedded with customers",
    "onsite with customers", "customer implementations",
    "post-sales engineering", "professional services engineer",
]
# Titles the same job ships under.
TITLES = [
    "Forward Deployed Engineer", "Forward Deployed Software Engineer",
    "Forward Deployed Solutions Engineer", "Deployment Engineer",
    "Implementation Engineer", "Implementation Consultant", "Integration Engineer",
    "Solutions Engineer", "Customer Engineer", "Delivery Engineer",
    "Technical Consultant", "Solutions Developer",
]

# The role must be SOFTWARE. "engineer" was in this list for one run and matched
# civil, structural, mechanical, process and geological engineers — in Canada
# most postings containing the word "engineer" are not software jobs at all.
SOFTWARE = re.compile(
    r"\b(python|javascript|typescript|node\.?js|react|sql\b|rest api|apis?\b|sdk|"
    r"codebase|software|saas|backend|back-end|front-end|full[- ]stack|"
    r"data pipeline|micro-?services|docker|aws|azure|gcp|postgres|api integration|"
    r"llm|machine learning|web application|platform)\b", re.I)
# Disciplines that share the vocabulary and are not this job. Checked against
# the TITLE, because a software posting may legitimately mention a client in
# manufacturing while a mechanical posting says "platform" about a product.
NOT_SOFTWARE_TITLE = re.compile(
    r"\b(civil|structural|mechanical|electrical|geotechnical|geological|"
    r"geoscientist|process engineer|manufacturing|mechatronics|hvac|"
    r"instrumentation|pavement|surfacing|land development|water resources|"
    r"design release|field engineer|technician|drafter|designer|"
    r"project coordinator|document controller|scheduling)\b", re.I)
# The lane's defining trait: the engineer is in front of the customer. Required
# unless the title already says forward-deployed, because that title carries it.
CUSTOMER_FACING = re.compile(
    r"\b(customer|client|end[- ]user|stakeholder|onboarding|implementation|"
    r"deployment|professional services|post-sales|onsite|on-site|field)\b", re.I)
FDE_TITLE = re.compile(
    r"\b(forward[- ]deploy\w*|solutions? engineer|implementation (engineer|consultant|"
    r"specialist)|deployment engineer|integration engineer|customer engineer|"
    r"delivery engineer|solutions? developer|technical consultant|"
    r"professional services engineer|solutions? architect)\b", re.I)

# Pre-sales is the dominant false positive in this lane. A description that
# talks about quota, pipeline or discovery calls is a sales job wearing an
# engineering title, whatever the title says.
PRESALES = re.compile(
    r"\b(quota|pipeline generation|net new logos|sales cycle|discovery calls?|"
    r"account executive|sales quota|revenue target|upsell|cross-sell|"
    r"close deals|book of business|territory)\b", re.I)
# Pure sales / marketing / recruiting titles, even at an engineering company.
SALES_TITLE = re.compile(
    r"\b(account executive|sales representative|business development|"
    r"account manager|marketing|recruiter|talent acquisition|"
    r"customer success manager|partnerships?|sales director)\b", re.I)
AGENCY_EXTRA = re.compile(
    r"\b(jobgether|mercor|turing|mindrift|upwork|lifted|cynet|actalent|vdart|"
    r"cyber ?coders|talentlab|career renew|inizio|upstaff|kelly services|avance|"
    r"excelgens|us tech|innova|pacer group|vtech|whopper|i8is|thri5|veracity|"
    r"hire ?digitalent|hirevouch|nearsource|acestack|keelen|equest|prolific|"
    r"vaco|jobgoal|scalian|quantum|altis|bilingual source|raise|swoon|motion recruit|"
    r"tundra|ian martin|calian|collabera|photon|mastech|zortech|net2source|"
    r"artech|ampstek|infojini|intelliswift|kforce|judge group|lorien|harvey nash)\b", re.I)
FRENCH = re.compile(r"(bilingu|français|francais|maîtrise du français)", re.I)
# Flags only — a mention is not always a requirement, but read the must-haves.
STACK_GAP = re.compile(
    r"\b(c#|\.net|asp\.net|angular|salesforce|\bgolang\b|\brust\b|kotlin|swift|"
    r"scala|ruby on rails|php|dynamics 365|sharepoint|powerapps|uipath|"
    r"pytorch|tensorflow|kubernetes|terraform)\b", re.I)
REMOTE = re.compile(r"\b(remote|telework|télétravail|work from home)\b", re.I)
# Worth calling out: this lane often wants travel, and he has no US visa.
US_TRAVEL = re.compile(r"\b(travel to (the )?(us|u\.s\.|united states)|us travel|"
                       r"travel.{0,20}(client|customer) sites?|\d{1,2}% travel)\b", re.I)


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


def main():
    queries = [("what_phrase", p) for p in PHRASES] + [("what", t) for t in TITLES]
    rows, seen = [], set()
    for i, (key, val) in enumerate(queries):
        for page in (1, 2):
            data = fetch({key: val}, page)
            for r in data.get("results", []):
                jid = str(r.get("id", ""))
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                rows.append({
                    "id": jid,
                    "title": r.get("title", ""),
                    "company": (r.get("company") or {}).get("display_name", ""),
                    "location": (r.get("location") or {}).get("display_name", ""),
                    "created": (r.get("created") or "")[:10],
                    "salary_min": r.get("salary_min"), "salary_max": r.get("salary_max"),
                    "desc": r.get("description", ""),
                    "url": r.get("redirect_url", ""),
                    "query": f"{key}={val}",
                })
            if len(data.get("results", [])) < 50:
                break
            time.sleep(0.35)
        print(f"  [{i+1}/{len(queries)}] {key}={val}: cumulative {len(rows)}", file=sys.stderr)

    applied = submitted_employers(Path("data/applications.md").read_text(encoding="utf-8"))
    pipeline_ids = known_urls()

    kept, drops = [], {}
    for r in rows:
        blob = f"{r['title']} {r['desc']}"
        row = PipelineRow(url=r["url"], company=r["company"], role=r["title"],
                          location=r["location"], posted=r["created"], source="adzuna")
        why = excluded(row)
        if why:
            r["drop"] = why
        elif AGENCY_EXTRA.search(r["company"]):
            r["drop"] = "agency/marketplace"
        elif SALES_TITLE.search(r["title"]):
            r["drop"] = "sales/marketing function"
        elif PRESALES.search(blob):
            r["drop"] = "pre-sales, not engineering"
        elif NOT_SOFTWARE_TITLE.search(r["title"]):
            r["drop"] = "not a software discipline"
        elif not SOFTWARE.search(blob):
            r["drop"] = "no software signal"
        elif not (FDE_TITLE.search(r["title"]) or CUSTOMER_FACING.search(blob)):
            r["drop"] = "not customer-facing"
        elif FRENCH.search(blob):
            r["drop"] = "French required"
        elif r["company"].strip().lower() in applied:
            r["drop"] = "employer already applied to"
        else:
            r["drop"] = ""
            r["in_pipeline"] = r["id"] in pipeline_ids
            r["stack_gap"] = sorted(set(m.lower() for m in STACK_GAP.findall(blob)))
            r["remote"] = bool(REMOTE.search(blob))
            r["us_travel"] = bool(US_TRAVEL.search(blob))
            kept.append(r)
        if r["drop"]:
            drops[r["drop"]] = drops.get(r["drop"], 0) + 1

    Path("scripts/harvest-fde.json").write_text(
        json.dumps({"kept": kept, "all": rows}, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\nfetched {len(rows)} unique · kept {len(kept)}")
    print(sorted(drops.items(), key=lambda kv: -kv[1]))
    for r in sorted(kept, key=lambda r: r["created"], reverse=True):
        pay = ""
        if r["salary_min"]:
            pay = f"{int(r['salary_min'])//1000}-{int(r['salary_max'] or 0)//1000}k"
        flags = []
        if r["remote"]:
            flags.append("remote")
        if r["us_travel"]:
            flags.append("⚠US-travel")
        if r["stack_gap"]:
            flags.append("gap:" + ",".join(r["stack_gap"][:3]))
        tag = "pipe" if r.get("in_pipeline") else "NEW "
        print(f"{tag} {r['created']} | {r['company'][:28]:<28} | {r['title'][:50]:<50} | "
              f"{r['location'][:26]:<26} | {pay:<9} | {r['id']} {' '.join(flags)}")


if __name__ == "__main__":
    main()
