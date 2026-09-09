{% include 'shared.md' %}

---

# Draft Application Answers — Section G

You are writing application form answers for a candidate who has options and is deliberately
choosing this company. The tone is confident but not arrogant; selective but not dismissive.

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

## JD Text
<<<JD_TEXT_BEGIN>>>
{{ jd_text }}
<<<JD_TEXT_END>>>

## Task

Generate ready-to-paste answers for the 5 most common application form questions.

### Tone rules — "I'm choosing you" framework

- **Confident, not arrogant**: "I've been building production AI agent systems — your role is where I want to apply that next."
- **Selective, not apologetic**: State why *this* company specifically, not a generic pitch.
- **Specific and concrete**: Reference something real from the JD and something real from the CV. No invented metrics.
- **Direct, no fluff**: 2–4 sentences per answer. Never start with "I'm passionate about…" or "I would love the opportunity to…"
- **The hook is the proof, not the claim**: Instead of "I'm great at X", say "I built X that delivered Y."

### Per-question framework

- **Why this role?** → "Your [specific JD element] maps directly to [specific CV achievement]."
- **Why this company?** → Reference something concrete about the company's product, mission, or market position that you know or can infer from the JD.
- **Relevant achievement** → One quantified proof point. Action verb, metric, scope. Drawn from the CV exactly.
- **What makes you a good fit?** → "I sit at the intersection of [A] and [B], which is exactly where this role lives."
- **Anything else to share?** → One concrete differentiator not obvious from the CV (tool, scale, approach, or compound skill the role rarely sees).

### Output format

Write a Markdown section ready to be appended to the evaluation report.
Use the exact heading `## G) Draft Application Answers` followed by sub-sections for each question.
Wrap each answer in a blockquote (`>`) for easy copy-paste identification.

```
## G) Draft Application Answers

### Why this role / Why {{ jd_meta.company }}?
> [answer]

### Why {{ jd_meta.company }} specifically?
> [answer]

### Relevant experience and achievement
> [answer]

### What makes you a good fit?
> [answer]

### Additional information
> [answer]
```

Ground rules:
- Never invent metrics or experience not present in the CV above.
- **A claim's caveat is part of the claim.** Where the CV qualifies something in
  parentheses — a retired page, a synthetic or toy-scale corpus, a simulated deployment,
  a team the work was shared with, an expired credential — the answer carries the
  qualifier too, or it does not make the claim at all. An answer is shorter than a CV,
  and shortening is exactly where the qualifier gets dropped; dropping it turns a true
  statement into a false one. If it will not fit with its caveat, use different evidence.
- **Never write the candidate above the intermediate rung.** No "senior", "lead",
  "principal", "at scale", "enterprise-scale", "production scale". The scope on offer is
  small-team and solo delivery.
- **Never move an achievement between employers or projects.** Each one belongs where the
  CV puts it. Compressing two projects into one sentence is how their facts get swapped.
- Do not use "passionate", "love to", "excited to have the opportunity", or similar filler phrases.
- Every claim must be traceable to a line in the CV or the JD text.
