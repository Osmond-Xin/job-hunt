# job-hunt — mandatory gates

Everything here is outward-facing: these documents go to real employers under the
user's name. Two gates are not optional, and both apply to artifacts you write by
hand as well as to anything the pipeline generates.

## 1. Red team before delivery

**No artifact is delivered to the user until it has passed through the red team.**
That covers résumés, cover letters, outreach emails, and application-form answers.

The pipeline does this on its own — `redteam_review` runs on every `job-hunt evaluate`
that produced an artifact, writes `redteam.md` into the run directory, and puts the
verdict at the top of the report. For anything you wrote by hand:

```bash
uv run python scripts/redteam.py \
    --artifact <path to .pdf or .md> --jd jds/<archived-jd>.md \
    --company "..." --role "..." --out <run-dir>/redteam.md
```

Three passes, in order: **facts** against `profile/redteam-facts.md`, **targeting**
against the JD, then a cold **HR read**. Verdict is `BLOCK` / `REVISE` / `SEND`.

- `UNREVIEWED` (mmx unreachable, timeout, no verdict line) is **not** a pass. Say so.
- The reviewer reads text extracted from a PDF and does produce false positives. You
  adjudicate — it has no veto over a fact you can verify yourself. But every claimed
  factual conflict gets checked against the source, never argued away.
- Report the verdict and the findings you rejected, with your reason. Do not hand over
  a clean-looking artifact and bury the review.

`profile/redteam-facts.md` is the ground truth the factual pass runs against. When a
fact changes — a permit, a certification, an employer, a project's honest boundary —
update that file in the same turn.

## 2. Page budget

**Résumé: 2 pages. Cover letter: 1 page.** Enforced, not aspirational.

- Generated: `MAX_CV_PAGES` in `job_hunt/nodes/pdf.py`; `_render_within_budget` renders,
  measures, drops one block, and re-renders until it fits, recording what it dropped.
- Hand-written: `scripts/render_cv.py --max-pages` (default 2) and `render_letter.py`
  **refuse** to pass a document that overflows. They do not auto-trim, because a human
  chose those lines and should choose again.
- Trimming words almost never removes a rendered line. Remove a whole bullet or a whole
  entry. Verify with the page count, and check remaining slack with
  `pdftoppm -r 100` if you are close to the edge.

If a run's warnings show many dropped blocks, the length budget in
`prompts/evaluate/tailor_cv.md` stopped working — fix that rather than leaning on the
trimmer.

## 2b. Tailoring never reorders dated roles

**Experience is always reverse-chronological**, newest first, on every résumé.

Tailoring reorders bullets within a role, skills groups, and projects — that is the
whole point of a tailored résumé, and `prompts/evaluate/tailor_cv.md` says so four
times. It says "keep every dated role … **in the same order**" exactly once, and a
hand-written résumé never reads that file. On 2026-09-17/18 ten of seventeen
hand-written résumés led with whichever role best matched the JD, and the red team
reported one of them as "reverse-chronological" without checking the dates.

`scripts/render_cv.py` now **refuses to render** a CV whose dated roles are out of
order — before the PDF exists, the same way the page budget deletes one that
overflows. An LLM reviewer is not a date checker; do not treat a clean review as
evidence that the order is right.

## 3. Record what was sent

**An application that is not in `data/applications.md` did not happen** — not for
follow-up, not for interview prep, not for "did I already apply here?". Building the
materials and sending them are two steps; writing the row is a third, and nothing in
the pipeline forces it. On 2026-08-19/20 thirty-five applications were built, sent, and
never recorded; the rejections that followed were invisible too, and an interview
invitation from GNWT sat unnoticed for eleven days.

Recording goes through the CLI, never by editing the markdown:

```bash
.venv/bin/job-hunt apply '<url>' --company '...' --role '...' \
    --pdf '<pdf under output/>' --no-browser --confirmed
```

A URL is required to open a browser, not to record. If the application has no
URL — found while browsing LinkedIn, or a referral into a role with no posting
— omit it; `--no-browser` is what makes that legal:

```bash
.venv/bin/job-hunt apply --company '...' --role '...' \
    --pdf '<pdf under output/>' --no-browser --confirmed
```

Leaving off `--no-browser` with no URL fails immediately with a message
explaining why — there is nothing to open. It does not silently skip the
browser.

`--pdf` is not optional: it stamps the tracker row number into the `output/`
directory, which is the only exact link between materials and row. Then write the real
detail into the row's notes — the red-team verdict, the submission date, any stated
timeline, and what must not be contradicted at interview.

**Close every session that produced or sent anything with:**

```bash
.venv/bin/job-hunt checkup      # materials with no row, mail with no row, follow-ups due
.venv/bin/job-hunt tracker verify
```

This applies to any agent working in this repo, not only this one. The full mechanics,
including the two inbound mail tracks and why outbound mail is invisible, are in
`AGENTS.md`.

## 4. A title is a guess — read the posting, and re-check what was cut

**Never recommend a posting, and never build materials for one, from its listing alone.**
Fetch the body and read it. On 2026-09-19 five of twelve picked postings collapsed on
reading; on 2026-09-21 it was fourteen of about twenty. The listing's *company name* can
be wrong too ("Socket.dev" was West Fraser; "ProNavigator" was Guidewire).

Titles fail in both directions, so the filters that read only titles are checked twice:

- **What fitted him was not AI-titled.** Beacon's "Software Engineer" asked in its body for
  agentic-framework projects at 1–4 years; Ada's was "Customer Solutions Consultant";
  Chowbus's "POS Support Specialist" required fluent Chinese. The lane is *applied-AI
  delivery for business users at 1–4 years*, under many titles.
- **What collapsed mostly was AI-titled**: 5+ years, a co-op term, depth in a named stack
  he cannot claim (TypeScript, Go, Java/Spring, Kubernetes, Databricks, SQL Server, Azure),
  or a domain credential (SAP, SCADA/OT, mining, insurance).

The mechanism, all deterministic:

| Where a posting can be lost | What now catches it |
|---|---|
| `scan`'s positive title filter — a discard never reaches `data/pipeline.md` | near misses are written to `data/scan-near-misses.tsv` |
| `triage` — one-role-per-employer, "large employer", a generic title ranked below the cut | `job-hunt triage --second-look N` reads the body of up to N such rows |
| the second look itself | every posting read is logged with its verdict in `data/second-look-log.tsv`; unread pages are retried, never counted as clean |

`scripts/daily_scan.sh` runs `--second-look 40`; the result is the last section of
`data/daily/triage-<date>.txt`. It **admits nothing by itself** — a rescued row is a
posting to read, not a recommendation. The signal lists live in
`job_hunt/services/second_look.py`; when a reading session teaches a new reason a posting
fits or collapses, add it there with the posting that taught it.

One role per employer still stands, with the exception he made on 2026-09-21: when a new
role at an employer fits **better** than the one already applied to, say so and let him
decide. Do not silently hold it back, and do not offer a menu.

## Contact details

`jonzy.xin@outlook.com` is the only address for applications the user submits himself.
`profile/cn/` deliberately carries a different address for résumés a third party submits
on his behalf — **never** use a `profile/cn/` file as the starting point for anything
else without replacing the contact block first.
