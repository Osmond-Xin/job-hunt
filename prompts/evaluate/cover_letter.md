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
prose — not a generic enthusiasm pitch. Position the candidate at MID-LEVEL
(data / platform / AI-application engineer), leading with recent, verifiable,
hands-on engineering work. Do NOT use "20-year veteran", "seasoned leader", or
"compound talent" self-labels — they read as overqualified/unverifiable and
get screened out. Let the earlier career support credibility briefly, not
headline the letter.
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

Write the **body** of a cover letter (3-4 paragraphs, no salutation, no signature, no header).
Output Markdown only. Each paragraph 3-5 sentences. The full letter must fit on one A4 page
when rendered with 11pt body type and 0.6in margins.

### Tone rules — "I'm choosing you" framework

- **Confident, not arrogant**: state what you have built, then what you intend to build with this team.
- **Selective, not apologetic**: the reader should sense you have other options and chose them deliberately.
- **Specific and concrete**: reference at least one phrase from the JD verbatim, mapped to a CV achievement.
- **Direct, no fluff**: do not write "I am passionate about", "I would love the opportunity", "I am writing to express my interest".
- **The hook is the proof, not the claim**: instead of "I am skilled at X", write "I built X that delivered Y."

### Paragraph framework

- **Paragraph 1 — The hook**: open with the strongest proof point that maps to this role. One sentence
  on what you have built; one sentence connecting it to a specific JD requirement; one sentence on why
  that overlap is unusual.
- **Paragraph 2 — Why this company specifically**: cite something concrete about the company's product,
  mission, or market position (drawn from the JD or from public information you can infer). Avoid generic
  praise.
- **Paragraph 3 — Compound differentiator**: one paragraph on the rare combination you bring (product +
  engineering, AI orchestration + analytics, etc.). Tie it to a JD phrase that describes the team shape.
- **Paragraph 4 (optional) — Forward-looking close**: one or two sentences on what you would do in the
  first 30-60 days, grounded in JD language. End with a single declarative sentence; no "I look forward
  to hearing from you."

### Hard constraints

- Never invent metrics, employers, or experiences not present in the CV above.
- Do not use the words "passionate", "excited", "thrilled", "love", or "opportunity" except in factual
  references to the JD itself.
- Every claim must be traceable to a line in the CV or the JD text.
- Stay under 280 words total. Stop when the case is made; do not pad.
- Output ONLY the paragraphs (Markdown), separated by blank lines. No headings, no bullets, no signature.

### Anti-pattern constraints (learned from past iterations)

- **Never self-label a quantified tenure** ("X+ years of experience", "two decades", "20 years",
  "veteran of N years"). It triggers age/over-qualified screens. Use neutral phrases like
  "experienced backend engineer", "deep track record in …", "shipping production systems across …".
  Employer-side year math from the CV's dated roles is fine; the candidate must not advertise totals.
- **Honest framing on domain gaps**: if the JD's vertical (e.g., industrial IoT, fintech compliance,
  bioinformatics) is NOT visibly represented in the CV experience, name the gap in paragraph 1 with one
  direct sentence ("I have not worked in industrial IoT before — sensors, MQTT, and Sparkplug B are not
  part of my prior shipping experience.") BEFORE pivoting to transferable strengths. Do not paper over
  the gap with adjacent claims. Recruiters trust direct acknowledgement and discount hedging.
- **Use the prep-project pivot**: if the CV's Projects section contains a project whose stack matches
  the JD's nice-to-have / bonus list AND the JD vertical is otherwise new to the candidate, treat that
  project as the explicit closing-the-gap evidence — "Rather than ask the team to take this on faith,
  I built …". Name the repo and offer to walk through it in an interview.
- **Honor "Additional Context" in the JD text**: if the JD text contains an "Additional Context" or
  "Personalization" footer (referrals by name, candidate-built prep projects, deliberate framing
  asks), treat those as authoritative facts and weave them in naturally. They are not invented content;
  they were appended by the candidate.
