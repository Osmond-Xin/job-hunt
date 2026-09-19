# Handoff — 17 résumés ready to submit (2026-09-17 / 09-18 batch)

Written 2026-09-18 for the session that will run the submissions. Everything here
was built and reviewed in the session that produced it; nothing has been sent, and
**no tracker row exists for any of it yet**.

## Status in one line

17 tailored résumés, all hand-written by Claude, all **exactly 2 pages**, all through
the mmx red team: **1 SEND (West Fraser), 16 REVISE, 0 BLOCK**. Every REVISE was read
and adjudicated — the remaining findings are targeting gaps (a JD asks for a stack or a
year count he does not have), not false claims. Per-artifact reasoning is in each run
directory's `redteam*.md`, highest `-rN` = latest round.

## The two rules that matter for this batch

1. **Sending and recording are two separate steps, and nothing enforces the second.**
   On 2026-08-19/20 thirty-five applications were sent and never recorded; the rejections
   and one interview invitation were invisible for eleven days. Record each one the same
   day it goes out:

   ```bash
   .venv/bin/job-hunt apply '<posting url>' --company '...' --role '...' \
       --pdf 'output/<run dir>/<the pdf>' --no-browser --confirmed
   ```

   `--pdf` is not optional: it stamps the tracker row number into the output directory,
   which is the only exact link between a row and the materials that were sent. Drop the
   URL (keep `--no-browser`) for anything with no posting link. Then write the real detail
   into the row's notes: submission date, the red-team verdict, any stated timeline, and
   **the gap named in the "do not contradict" column below**.

2. **Close the session with:**

   ```bash
   .venv/bin/job-hunt checkup
   .venv/bin/job-hunt tracker verify
   ```

## A — applied-AI track

| # | Employer / role | Location | Comp | Apply | Résumé | Verdict |
|---|---|---|---|---|---|---|
| 1 | **West Fraser** — AI Agent Developer | Vancouver, on-site | — | https://www.adzuna.ca/details/5885851558 | `output/2026-09-17-west-fraser-ai-agent-developer/Yi_Xin_AI_Agent_Developer_West_Fraser.pdf` | **SEND** |
| 2 | **Xanadu** — AI Developer, Agentic System | Toronto, hybrid | — | https://xanadu.careers.hibob.com/jobs/621aa1a7-1167-469e-8f28-8f1effc30bab | `output/2026-09-18-xanadu-ai-developer-agentic-system/Yi_Xin_AI_Developer_Agentic_System_Xanadu.pdf` | REVISE |
| 3 | **thinkRF** — AI/ML Engineer | Kanata, Ottawa | — | ⚠️ **email**, see below | `output/2026-09-18-thinkrf-ai-ml-engineer/Yi_Xin_AI_ML_Engineer_thinkRF.pdf` | REVISE |
| 4 | **ServiceNow** — Forward Deployed Solution Engineer, Applied AI | Montréal (JD says remote) | — | https://www.adzuna.ca/details/5884501919 | `output/2026-09-17-servicenow-forward-deployed-solution-engineer/Yi_Xin_Forward_Deployed_Solution_Engineer_ServiceNow.pdf` | REVISE |
| 5 | **Escalent** — Developer, AI Transformation Team | Remote, Canada | CAD 135–155k | https://www.adzuna.ca/details/5886075601 | `output/2026-09-17-escalent-developer-ai-transformation-team/Yi_Xin_AI_Developer_Escalent.pdf` | REVISE |
| 6 | **Quadbridge** — AI Business Analyst | Kitchener–Waterloo | CAD 85–95k | https://www.adzuna.ca/details/5887360574 | `output/2026-09-17-quadbridge-ai-business-analyst/Yi_Xin_AI_Business_Analyst_Quadbridge.pdf` | REVISE |
| 7 | **YuJa** — AI Engineer | Toronto, on-site | CAD 90–100k | https://www.adzuna.ca/details/5886072229 | `output/2026-09-17-yuja-ai-engineer/Yi_Xin_AI_Engineer_YuJa.pdf` | REVISE |

## B — early-stage / product engineering

| # | Employer / role | Location | Comp | Apply | Résumé | Verdict |
|---|---|---|---|---|---|---|
| 8 | **AppDirect (Firstbase)** — Intermediate Software Developer | Montréal, hybrid | CAD 95–120k | https://boards.greenhouse.io/appdirect/jobs/8783745002 | `output/2026-09-18-appdirect-intermediate-software-developer/Yi_Xin_Intermediate_Software_Developer_AppDirect.pdf` | REVISE |
| 9 | **Stay22** — Software Developer, Integrations | Montréal | — | https://boards.greenhouse.io/stay22/jobs/4405481009 | `output/2026-09-18-stay22-software-developer-integrations/Yi_Xin_Software_Developer_Integrations_Stay22.pdf` | REVISE |
| 10 | **Chexy** — Full Stack Engineer | Toronto | — | https://chexy.applytojobs.ca/engineering/50836 | `output/2026-09-18-chexy-full-stack-engineer/Yi_Xin_Full_Stack_Engineer_Chexy.pdf` | REVISE |

## C — public sector

| # | Employer / role | Location | Comp | Apply | Résumé | Verdict |
|---|---|---|---|---|---|---|
| 11 | **City of Brampton** — Analyst, WFM Business Systems | Brampton | CAD 102,784–115,632 | https://www.jobbank.gc.ca/jobsearch/jobposting/50307090 (Job Opening 107117) | `output/2026-09-17-city-of-brampton-wfm-business-systems-analyst/Yi_Xin_WFM_Business_Systems_Analyst_Brampton.pdf` | REVISE |
| 12 | **Government of Manitoba** — Business Analyst and IT Support | Winnipeg | — | Manitoba job board, **Advertisement # 45668** | `output/2026-09-17-gov-manitoba-business-analyst-and-it-support/Yi_Xin_Business_Analyst_IT_Support_Manitoba.pdf` | REVISE |
| 13 | **University of Waterloo** — Business Systems Analyst | Waterloo | — | https://www.adzuna.ca/details/5858051306 ⚠️ see below | `output/2026-09-17-university-of-waterloo-business-systems-analyst/Yi_Xin_Business_Systems_Analyst_Waterloo.pdf` | REVISE |
| 14 | **University of Alberta** — Functional Analyst | Edmonton, hybrid | — | https://www.adzuna.ca/details/5878186843 | `output/2026-09-17-university-of-alberta-functional-analyst/Yi_Xin_Functional_Analyst_University_of_Alberta.pdf` | REVISE |
| 15 | **City of Vancouver** — Systems Analyst II | Vancouver | — | https://www.jobbank.gc.ca/jobsearch/jobposting/50223007 (Req 46366) | `output/2026-09-17-city-of-vancouver-systems-analyst-ii/Yi_Xin_Systems_Analyst_II_City_of_Vancouver.pdf` | REVISE |

## D — northern lottery

Applied under the operator's 2026-09-17 ruling: a full match in the North almost never
exists, so these go out on technical reach — "they need my technical skills and will
accept that I learn the domain".

| # | Employer / role | Location | Comp | Apply | Résumé | Verdict |
|---|---|---|---|---|---|---|
| 16 | **Yukon Hospitals** — Technical Analyst, Information Systems | Whitehorse | CAD 38.37–48.55/hr (PSAC) | https://www.adzuna.ca/details/5870258542 | `output/2026-09-17-yukon-hospitals-technical-analyst-information-systems/Yi_Xin_Technical_Analyst_Yukon_Hospitals.pdf` | REVISE |
| 17 | **Air North** — IT System Analyst | Whitehorse | — | https://www.adzuna.ca/details/5860902568 | `output/2026-09-17-air-north-it-system-analyst/Yi_Xin_IT_System_Analyst_Air_North.pdf` | REVISE |

## Per-posting notes — read before sending

**#3 thinkRF is not a form.** The posting says: email `hr@thinkrf.com` with the role title
in the subject, a note of **ten sentences maximum**, and "receipts" — a repo link or a
two-page write-up of a production ML system he personally built and operated. No
cover-letter essays. The two open-source repos are exactly what it asks for. The note
still has to go through the red team before it is sent; it is an outward-facing artifact.

**#11 City of Brampton closes 2026-10-11.** The only hard deadline in this batch.
Internal applicants apply with a City email; external applications are open.

**#13 University of Waterloo — confirm the requisition first.** The JD used for this
résumé is the Registrar's Office Business Systems Analyst reached through Adzuna. The
pipeline also holds `uwaterloo.wd3.myworkdayjobs.com/en-US/uw_careers/job/Information-Systems-Specialist--Business-Systems-Analyst-_2026-01781-2`,
which may be a *different* requisition. Open both, apply to the one whose duties match
the archived JD (`jds/university-of-waterloo-business-systems-analyst.md`), and record
the URL actually used.

**#14 University of Alberta**: internal candidates and former employees get priority
consideration, and the term runs **Oct 2026 – Jun 2027** only. Multiple Functional Analyst
postings exist — each needs its own application.

**#4 ServiceNow**: the JD header says Montréal but the work persona is remote; the résumé
says "remote, or relocating to Montréal". Nothing in it claims French.

**Adzuna links are hops.** Where the link above is `adzuna.ca/details/...`, open it and
follow through to the employer's own ATS, then **record the employer URL**, not the
Adzuna one — the aggregator link dies when the ad rotates.

## Do not contradict at interview

Each résumé was written to be defensible; these are the gaps it does **not** paper over.

| Posting | The gap the JD asks for and he does not have |
|---|---|
| West Fraser, Xanadu | Microsoft Copilot/Foundry stack (West Fraser); Rust and Terraform, and "4+ years building AI/ML tools in a research environment" (Xanadu) |
| ServiceNow | JD wants 3+ years production ML/AI; his is ~2 (freelance 2022–2024 plus the internship) |
| Escalent | JD wants 5–8 years Python; Python has been his primary language since 2022 |
| YuJa | PyTorch, model training and deployment — his applied AI is retrieval/agents plus evaluation, not training |
| Quadbridge | Microsoft Foundry / Copilot Studio, Azure DevOps / Jira |
| AppDirect | JD's stack is Java + Spring + GraphQL + Kafka/SQS/SNS; his last Java role was 2012, no Spring or messaging |
| Stay22, Chexy | **TypeScript.** Ground truth bans claiming it: "Node.js and JavaScript are what I've shipped; TypeScript I'd be picking up." Chexy's 2+ years production TS/React bar is not met |
| thinkRF | No shipped production ML system at a customer; no RF/SIGINT domain; Reliability Status likely not available on a PGWP |
| Brampton | No workforce-management, time-and-attendance or scheduling experience — the résumé says so outright |
| Waterloo, U Alberta | No student information system (PeopleSoft Campus Solutions) experience |
| Vancouver | **The weakest of the 17.** "Systems Analyst II" is a DBA posting: SQL Server, Azure SQL, DB2, .NET. His hands-on database work is PostgreSQL and MySQL; the résumé says so |
| Yukon Hospitals, Air North | Both read above the posting's level (Technical Director, 10+ team). Healthcare and airline-PSS domains are new |

## Deliberately not applying

| Posting | Why |
|---|---|
| TC Energy — AI Applications Developer | JD body: "a senior technical enablement and solution-guidance role". Operator declined 2026-09-17. `pipeline.md` row marked DISCARD; tracker **#949 VOID** |
| Enbridge — Specialist I TIS Data Scientist / ML Engineer | "Specialist I" but 6+ years DS/ML delivery plus Databricks/Spark/MLflow. Declined 2026-09-17; both duplicate pipeline rows marked DISCARD |
| Ontario Northland — IT analyst | Job Bank answered 410; the posting is closed. Row marked `[!]` |
| Vector Institute — Machine Learning Associate | ADP metadata: **short-term contract, hourly, max CAD 32.00/hr**. No JD body published |
| Ecopia.AI — Frontend Engineer | Deep TypeScript plus WebGL/Unity3D; nothing honest to claim |

## For the agent running the submissions

- **Do not regenerate these artifacts.** They are hand-written and reviewed. If a JD has
  changed since 2026-09-17/18, re-review rather than rewrite — `scripts/redteam.py
  --artifact <pdf> --jd jds/<slug>.md --company '...' --role '...' --origin hand-written
  --out <run dir>/redteam-rN.md`.
- **Artifacts are written by Claude in-session**, not by the local CLIs; agy and opencode
  are reviewers only (operator's instruction, 2026-09-17). codex quota returns 2026-09-19.
- If a form needs free-text answers, write them, red-team them, and only then hand them
  over. Answers are outward-facing artifacts like any other.
- The browser is not used for submission. The operator submits; the agent records.
- Archived JDs for all 17 are in `jds/` under the slug matching each run directory.
