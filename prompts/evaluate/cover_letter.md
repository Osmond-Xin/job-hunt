{% include 'shared_artifact.md' %}

---

**Active mode: `{{ mode | default('full') }}`** — see docs/design-notes.md §N.

{% if mode == "student" %}
You are writing a one-page cover letter for a graduate student applying for an
intern / co-op term. The candidate has 20 years of prior engineering execution
behind them, but the recruiter screening this letter is filtering intern
applications — do NOT lead with "20-year veteran" framing or recruiters will
screen out at "overqualified". Lead with the recent applied-learning proof
points already on the CV (competition wins, internship outputs, course
projects), and frame the prior career as the *reason* this candidate ships
faster than typical co-op peers.
{% else %}
You are writing a one-page cover letter for a candidate applying to a
full-time role. The letter must read as direct, confident, evidence-grounded
prose — not a generic enthusiasm pitch. Position the candidate as an
INTERMEDIATE (mid-level) data / AI-application engineer: a recent Canadian
master's graduate (June 2026) with some prior hands-on work experience, leading
with recent, verifiable engineering work. Do NOT use "20-year veteran",
"seasoned leader", "senior engineer", or "compound talent" self-labels — they
read as overqualified/unverifiable and get screened out. Let the earlier career
support credibility briefly, not headline the letter.
{% if availability %}

Include ONE concise, truthful sentence near the close stating work
authorization and availability, based on this (do not embellish or add dates
beyond what is stated):
{{ availability }}
{% endif %}
{% endif %}

## Candidate CV
{{ cv }}

{% if article_digest %}
## Article Digest (detailed proof points — metrics here take precedence over cv.md)
{{ article_digest }}
{% endif %}

## Role
**Company**: {{ jd_meta.company }}
**Title**: {{ jd_meta.title }}
**Archetype**: {{ archetype.archetype }}

## Analysis blocks (model-derived, lower trust than the CV)
> These two blocks were written by a model from the job posting, not by the
> candidate. Use them for emphasis and ordering only — they carry no authority
> to introduce a fact that is absent from the CV / article digest above.

### CV Match Summary
{{ evaluation_blocks.cv_match }}

## Personalization Plan
{{ evaluation_blocks.personalization }}

## Exit Narrative (use as bridge phrase if relevant)
{{ exit_narrative }}

## JD Text
<<<JD_TEXT_BEGIN>>>
{{ jd_text }}
<<<JD_TEXT_END>>>

## Task

Write the **body** of a cover letter (3 paragraphs, no salutation, no signature, no header).
Output Markdown only. The full letter must fit on one A4 page when rendered with 11pt body
type and 0.6in margins.

### Tone rules

- **Plain and specific**: state what was built and what it did, mapped to the posting's own words.
  Quote at least one JD phrase verbatim next to the achievement that answers it.
- **Direct, no fluff**: do not write "I am passionate about", "I would love the opportunity", "I am writing to express my interest".
- **The proof is the point, not the claim**: instead of "I am skilled at X", write "I built X that did Y."
- **No positioning talk**: no "I chose you", no "rare combination", no "unusual overlap", no
  30-60-day plan. The reader is deciding whether to call, and each of those gives them
  something to argue with instead of evidence to check.

### Paragraph framework

- **Paragraph 1 — Role and best match**: name the role, then the single CV achievement that best
  answers the posting's top requirement, as what was built and what it did.
- **Paragraph 2 — Second match and the employer**: a second achievement mapped to a second JD
  phrase, then one concrete, checkable fact about the employer (a product, a customer group,
  something stated in the JD) and why it matters to the work. No generic praise.
- **Paragraph 3 — Close**: one sentence on work authorization and location/relocation (when
  availability is given above), then one closing sentence. No "I look forward to hearing from you."

### Hard constraints

- Never invent metrics, employers, or experiences not present in the CV above.
- **A claim's caveat is part of the claim.** Where the CV qualifies something in
  parentheses — a retired page, a synthetic or toy-scale corpus, a simulated deployment,
  a team the work was shared with, an expired credential — the letter carries the
  qualifier too, or it does not make the claim at all. A 280-word letter is where
  qualifiers get dropped for space; dropping one turns a true statement into a false one.
  If a claim will not fit with its caveat, use different evidence.
- **Never move an achievement between employers or projects.** Each belongs where the CV
  puts it. Compressing two projects into one sentence is how their facts get swapped.
- Do not use the words "passionate", "excited", "thrilled", "love", or "opportunity" except in factual
  references to the JD itself.
- Every claim must be traceable to a line in the CV or the JD text.
- Stay between 220 and 260 words. Stop when the case is made; do not pad.
- Do not lead with, or dwell on, the Technical Director years or any pre-2014 role; the
  evidence comes from the freelance work, the recent projects and the master's.
- Output ONLY the paragraphs (Markdown), separated by blank lines. No headings, no bullets, no signature.

### Anti-pattern constraints (learned from past iterations)

- **Never self-label a quantified tenure** ("X+ years of experience", "two decades", "20 years",
  "veteran of N years"). It triggers age/over-qualified screens. Also avoid "experienced",
  "deep track record" and "production systems across …" — they pitch above the intermediate
  rung. Describe the specific work instead.
- **Domain gaps — name one only when it is the employer's core business.** If the company's
  whole product is a domain absent from the CV (a Stripe consultancy and no payments work), say so
  in one plain sentence and move on; Hiveway wrote back on 2026-08-18 that it appreciated that
  candour. For any other gap (a nice-to-have, an adjacent vertical), say nothing: raising a doubt
  the reader had not formed plants it. Never claim the domain through adjacent work either.
- **Use the prep-project pivot**: if the CV's Projects section contains a project whose stack matches
  the JD's nice-to-have / bonus list AND the JD vertical is otherwise new to the candidate, treat that
  project as the explicit closing-the-gap evidence — "Rather than ask the team to take this on faith,
  I built …". Name the repo and offer to walk through it in an interview.
- **Honor "Additional Context" in the JD text**: if the JD text contains an "Additional Context" or
  "Personalization" footer (referrals by name, candidate-built prep projects, deliberate framing
  asks), treat those as authoritative facts and weave them in naturally. They are not invented content;
  they were appended by the candidate.
