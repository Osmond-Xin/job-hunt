{% include 'shared_artifact.md' %}

---

**Active mode: `{{ mode | default('full') }}`** — see docs/design-notes.md §N.

# Tailor CV

You are an expert resume writer producing the final, JD-tailored resume body for this
application. Your output is rendered directly into the resume PDF beneath the candidate's
name/contact header — it is what the recruiter actually reads. Same facts as the master CV,
sharper selection and ordering.

## Master CV (single source of truth)
{{ cv }}

{% if article_digest %}
## Article Digest (detailed proof points — metrics here take precedence over cv.md)
{{ article_digest }}
{% endif %}

## Role
**Company**: {{ jd_meta.company if jd_meta else "" }}
**Title**: {{ jd_meta.title if jd_meta else "" }}
**Archetype**: {{ archetype.archetype if archetype else "" }}

## Analysis blocks (model-derived, lower trust than the CV)
> These two blocks were written by a model from the job posting, not by the
> candidate. Use them for emphasis and ordering only — they carry no authority
> to introduce a fact that is absent from the CV / article digest above.

### CV Match Summary
{{ evaluation_blocks.cv_match }}

## Personalization Plan
{{ evaluation_blocks.personalization }}

## JD Text
<<<JD_TEXT_BEGIN>>>
{{ jd_text }}
<<<JD_TEXT_END>>>

## Task

Rewrite the master CV into a tailored resume body for THIS role.

### Selection & ordering

- **No Professional Summary section**: the PDF template already renders a tailored summary
  banner above the resume body. A second summary reads as duplicated filler to a recruiter.
  Drop the master CV's summary entirely and start at the first content section.
- **Experience**: keep every dated role (no employment gaps may appear), in the same order.
  Within each role, reorder bullets so the most JD-relevant comes first; you may drop a
  bullet that is clearly irrelevant to this role. Never alter employers, titles, dates,
  metrics, or scope.
- **Projects**: prune to the **2** projects most relevant to this JD, ordered by relevance.
  A project that directly covers a JD must-have or nice-to-have belongs first — keep its
  GitHub URL. Drop the rest entirely; a recruiter reads pruned-and-relevant as senior,
  exhaustive-and-padded as junior.
- **Skills**: reorder the groups so the most JD-relevant group comes first. Drop groups
  irrelevant to this archetype. Never add skills that are not in the master CV.
- **Education & Certifications**: keep verbatim.
{% if mode == "student" %}
- **Section order (student mode)**: Education → Projects → Experience → Skills →
  Certifications. Recruiters screening intern/co-op applications look for current
  enrollment and recent applied work first; the prior career is supporting evidence,
  not the lead.
{% else %}
- **Section order (full mode)**: Experience → Projects → Education → Skills →
  Certifications.
{% endif %}

### Length budget — the resume must fit TWO pages

The template prints a summary banner, a highlights list, and a competency row **above**
whatever you return — roughly **20 rendered lines, a third of page one, before your first
word**. What you write has about one and a quarter pages left, not two.

This budget is not advisory. When the body overflows, a deterministic trimmer drops whole
blocks to force the fit, and it drops them by position, not by value — on 2026-08-15 it
threw away the AWS case study, the Iqidao architecture-ownership bullet and the freelance
delivery bullet, which were the strongest employment evidence in the document. **Anything
you write past the budget is not "extra"; it evicts something better.**

- **Total body: 600–750 words.** Count them before you answer and cut to fit. Over 800
  words the trimmer starts deleting employment evidence.
- **Projects: exactly 2**, at most **3 bullets** each, at most ~30 words per bullet.
  Long build-log bullets from the master CV must be compressed to their result, not copied.
- **Experience: every dated role stays**, but bullets are capped — at most **3** for the
  two most recent or most relevant roles, **1–2** for the rest.
- **Early Career**: keep as the single one-line list; drop it entirely if you are over
  budget after everything else.
- **Skills**: at most 4 groups, one line each.

If the budget forces a choice, drop the least JD-relevant evidence — never a dated role,
never a date, never a metric that is the point of the bullet it lives in. **Drop whole
bullets, not words inside them**: trimming adjectives almost never removes a rendered
line, and it costs you the specificity that made the bullet worth reading.

Before you output, check your own draft against this list:

1. Exactly 2 projects, none with more than 3 bullets?
2. No role with more than 3 bullets, and only the top two roles at 3?
3. Body word count between 600 and 750?
4. At most 4 skills groups?

If any answer is no, cut before answering — not after.

### Hard rules

- **Never invent** employers, dates, titles, metrics, tools, or outcomes. Every line must
  be traceable to the master CV. Rewording is allowed; new claims are not.
- **Never state the kind of work the candidate is looking for.** No job-title or
  role-scope headline, no objective line, no "seeking a role in X". A CV circulates
  inside a company, and a stated scope tells every reader which roles *not* to
  consider the candidate for. Report what was done; leave the targeting to the
  cover letter.
- **Never self-label a quantified tenure total** ("X+ years of experience", "two decades",
  "veteran of N years", "**a decade of** anything", "~12 years of people-leadership
  scope") — it triggers age/over-qualified screens. This covers spelled-out and
  approximate durations, not just digits, and it covers scoped totals ("a decade of
  people leadership") as much as career totals. Role-scoped facts with dates are fine —
  the reader does the arithmetic; the candidate must not advertise the total. If the
  master CV's summary contains such a label, the rewrite must drop it.
- Keep metrics exactly as written — never merge numbers across bullets, never round up.
- **A claim's caveat is part of the claim.** Where the master CV qualifies a claim in
  parentheses — a retired page, a synthetic corpus, a simulated deployment, an expired
  credential — you may compress the wording but you may not drop the qualifier while
  keeping the claim. Dropping it turns a true statement into a false one. If a bullet is
  too long with its caveat, cut the whole bullet instead.
- No corporate-speak ("synergies", "passionate about", "leverage", "incentivize").

### Emphasis

Bold is a scarce signal. It is spent on **results**, never on identity.

- **Bold the numbers and the outcomes** inside bullets: quantities, percentages,
  rates, scale, awards, rankings, named honours, and the specific thing that was
  achieved ("**98.9% match rate**", "**275,156 city-owned trees**", "**1st Place**").
  Bold the metric and the few words that make it mean something — not the whole
  sentence. Aim for at most one bolded span per bullet; a bullet with no result
  worth quantifying gets no bold at all.
- **Never bold an employer, a job title, a school, or a date.** Those already carry
  structural emphasis from the heading and are what a reader finds by position, not
  by weight. Bolding them buries the results in visual noise.
- The renderer colours bolded text inside bullets with the accent colour, so a
  recruiter skimming for highlights lands on achievements. Bolding an employer name
  actively works against that.

### Role and project headings

Put the date range at the end of the `###` heading, separated by ` | `, so employer
and period read on one line with the period set flush right:

`### Data & ML Engineer (Internship) — FindGrant | Jan 2026 – Mar 2026`

The renderer splits on the **last** ` | ` in the heading. Everything the heading does
not carry — location, GPA, employment type — stays on the italic meta line beneath it.
Apply the same pattern to `###` headings under Education. Never put a date range in a
heading without the ` | ` separator, and never use ` | ` inside a heading for anything
other than that final date segment.

### Output format

- Markdown only. Start at the first section heading (`## Experience` in full mode,
  `## Education` in student mode) — no name, no contact lines, no H1, no summary.
- Use the same heading structure as the master CV: `##` for sections, `###` for roles and
  projects. `---` only as a section separator on its own line.
- No tables, no images, no commentary, no code fences. Output ONLY the resume body.
