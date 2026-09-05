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
- Do not use "passionate", "love to", "excited to have the opportunity", or similar filler phrases.
- Every claim must be traceable to a line in the CV or the JD text.
