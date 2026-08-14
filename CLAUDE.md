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

## Contact details

`jonzy.xin@outlook.com` is the only address for applications the user submits himself.
`profile/cn/` deliberately carries a different address for résumés a third party submits
on his behalf — **never** use a `profile/cn/` file as the starting point for anything
else without replacing the contact block first.
