# Résumé and cover-letter brief (hand-written artifacts)

Hand-written résumés never read `prompts/evaluate/tailor_cv.md`, so the rules they need live
here. Read this before writing one. Facts still come only from `profile/cv.md`,
`profile/cv-general.md` and `profile/redteam-facts.md`; the gates in `CLAUDE.md` still apply.

Written 2026-09-30 after reading the 66 résumés of 09-21..09-29 (about 300 applications since
August, four human interviews). With that few interviews, nothing below is proven. It targets
what the red team's HR reads kept reporting and what page one actually showed. Record
`layout: v2` in each run's `apply-notes.md` so the response rate can be compared later.

## What went wrong in the v1 layout

| Seen on the rendered page | Why it costs interviews | Rule now |
|---|---|---|
| "Early Career (2005–2014)": App Development Director, Development Manager, Senior Software Engineer…; bachelor's "2001 – 2005" | 19 of 67 HR reads: "overqualified", "wall of director titles"; the rejection happens before any evidence is read | Start at Iqidao (Dec 2014). No pre-2014 roles, no degree years. **Decided by him 2026-09-30.** |
| From 09-26 on, Projects sat above Experience, so page one had no paid work at all | Looks like a new grad with side projects, then page two shows a Technical Director. That is the worst of both. | Experience comes before Projects, always |
| 50–60 bold spans per résumé (it was ~25 on 09-21) | Bold is the accent colour, so when everything is highlighted nothing is | At most **1** bold span per line |
| A 6-sentence summary using the project's own words: "five-span audit trace", "degraded runs", "refusal gates", "a different model from the one that wrote it" | A screener who has never seen the project cannot tell what any of it proves | Summary is 60 words or fewer, in plain English, using the JD's nouns |
| A 4-sentence work-authorization paragraph | Takes three lines of the top third to say one fact | One line |

`scripts/render_cv.py` refuses to render the first four (`check_screen_signals`). `--allow-legacy-layout`
exists only for the full-history `profile/` variants.

## Layout (full mode)

1. **Header**: name, `Niagara Falls, ON — open to relocating to <city>`, contact line, links.
2. **Work authorization, one line**: `Work authorization: Post-Graduation Work Permit valid to Aug 2029 — any employer, no sponsorship needed.`
3. **Summary**: 2–3 sentences, 60 words or fewer, no "I". Sentence 1 says who he is at the level the
   posting hires at: a recent Canadian Master of Data Analytics graduate with hands-on
   software/data work. Sentence 2 names the one or two pieces of evidence that match the JD's
   top ask, in the JD's own words. Nothing about what he is looking for.
4. **Core Skills**: at most 4 lines, most JD-relevant first, only skills that are in the master CV.
   Use the JD's exact spelling of any skill he really has; that is the ATS match.
5. **Experience**: FindGrant → Freelance → Bailongma → Iqidao, reverse-chronological. The first
   bullet of each role answers one of the JD's top three asks. Two to three bullets for the
   roles that match the posting best, one for the rest.
6. **Selected Projects**: at most 2. Each bullet **leads with what it does or proves in plain words**
   and puts the technical term after it: "Answers only with a cited source or a refusal
   (five refusal gates)", not "Five refusal gates enforce fail-closed grounding".
7. **Education**: Master's with dates, GPA and the distinction list; Bachelor's **without years**.
   Certifications with date ranges and "expired", as before.

## Per-posting procedure

The v1 résumés passed the red team's targeting pass almost every time, because that pass only
checks that a JD keyword appears *somewhere*. That is not the test a screener applies. The screener
asks whether this person has done *our* work, and looks for the answer in the top third of page one.
So each posting goes through four steps, written into the run's `apply-notes.md` before the résumé exists.

1. **Knockout check.** List the hard requirements: years stated as a minimum, a stack the daily work
   runs on (not a nice-to-have), a licence, a language, on-site location, level. Anything he cannot
   show is a knockout, and wording will not rescue it. Quora ML Platform (GPU serving, Kubernetes,
   PyTorch), Citi (Kubernetes plus 4+ years of Python backend) and Fortinet (2–3 years of ML plus
   Kubernetes CI/CD) were all sent with knockouts named in their own red-team reports. Such a posting
   is either skipped or sent as a declared lottery (memory `lottery-applications`). It never gets a
   rewrite effort as if it were a fit.
2. **The three asks.** Take the three things the posting most wants, in the posting's own words.
   Rank them by where they appear (title, first paragraph, first duties) and how often they repeat.
   Skip the benefits boilerplate and the preferred-skills tail.
3. **Evidence map.** For each ask, pick the single strongest supported fact (cite the `profile/cv.md`
   line), then rewrite it **in the posting's vocabulary**. Leave a gap as a gap; never fill it.
   Worked example, Fortinet release-QA (JD: "spec-driven test plans", "verify before human review",
   "auditable AI insights"):
   | Ask | v1 wording (internal) | Mapped wording (same fact) |
   |---|---|---|
   | Specification-driven work | not used, though `cv.md` has it | LearnArken was built spec-first: written specs, then AI implementation, then an independent review pass |
   | Verify AI output before human review | "reviewed by a different model from the one that wrote it" | Every AI-generated document is checked against a ground-truth file by a second model before a person sees it |
   | Auditable, human oversight | "five-span audit trace", "degraded runs" | Each run records what was retrieved, what the model was asked and why it answered or refused; a person approves every step |
4. **Place the map.** Summary sentence 2 carries ask #1. Each role's first bullet carries whichever
   ask that role proves best. Skills line 1 uses the JD's exact spelling of skills he has. Projects are
   chosen by how many asks they cover, not by which is most impressive.

**Page-one test** before red team: read only the top third of page one (header, summary, the first
role). Do the posting's three asks appear there, in its words? If not, move them up. The same run
decides whether a cover letter is worth writing: when one ask is only provable in prose (customer
contact, a domain the company lives in), write one.

## Wording

- Bold marks a result: a number, an award, a named outcome. Never an employer, title, tool or date.
- Project-internal vocabulary goes into the repo, not onto page one: span, gate, degraded run, StateGraph,
  RRF, kappa. If a term has to stay, put the plain-language result first.
- Name a gap only when it is the company's core business and the reader will certainly see it
  (Stripe at a Stripe consultancy: Hiveway wrote back that the candour was welcome). Otherwise say
  nothing about it; see memory `no-defensive-preemption`.
- No seniority vocabulary (senior, seasoned, lead, at scale); no tenure totals; caveats stay attached to
  their claims. These rules are unchanged.

## Cover letter (when one is asked for)

220–260 words, three paragraphs, one page.

1. The role by name, and the single piece of evidence that best matches the posting's top ask, stated
   as what he built and what it did.
2. A second piece of evidence mapped to a second JD phrase, then one concrete, checkable fact about the
   employer (a product, a customer group, something from the JD itself) and why that matters to the work.
3. One sentence on authorization and location/relocation, then one closing sentence. No "I look forward".

Leave out: an "I'm choosing you" tone, a paragraph about his unusual combination of skills, the
Technical Director years as the lead, gap paragraphs outside the core-domain case above, and anything
answering a doubt the posting did not raise.
