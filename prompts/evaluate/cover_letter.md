{% include 'shared.md' %}

---

You are writing a one-page cover letter for a candidate who has options and is deliberately
choosing this company. The letter must read as direct, confident, evidence-grounded prose —
not a generic enthusiasm pitch.

## Candidate CV
{{ cv }}

## Role
**Company**: {{ jd_meta.company }}
**Title**: {{ jd_meta.title }}
**Archetype**: {{ archetype.archetype }}

## CV Match Summary
{{ evaluation_blocks.cv_match }}

## Personalization Plan
{{ evaluation_blocks.personalization }}

## Exit Narrative (use as bridge phrase if relevant)
{{ exit_narrative }}

## JD Text
{{ jd_text }}

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
- Stay under 350 words total. Stop when the case is made; do not pad.
- Output ONLY the paragraphs (Markdown), separated by blank lines. No headings, no bullets, no signature.
