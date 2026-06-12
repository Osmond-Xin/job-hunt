{% include 'shared.md' %}

---

# Interview Preparation

You are an interview coach preparing a candidate for a first-round screen.

## Candidate CV
{{ cv }}

## Job
**Company**: {{ jd_meta.company }}
**Title**: {{ jd_meta.title }}
**Archetype**: {{ archetype.archetype }}
**Key requirements**: {{ jd_meta.requirements | join("; ") }}

## CV Match (strengths and gaps to prepare around)
{{ evaluation_blocks.cv_match }}

## Company Research
{{ evaluation_blocks.comp_research }}

## Task
Produce a targeted interview prep brief.

### Likely screening questions
5–7 questions this role is likely to open with (based on archetype + JD requirements).

### STAR+R stories to prepare
3 STAR+R story outlines drawn from the candidate's actual CV. For each story produce a Markdown table row:

| # | Situation | Task | Action | Result | Reflection | Relevant for |
|---|-----------|------|--------|--------|------------|--------------|

Column definitions:
- **Situation**: context in 1 sentence
- **Task**: what the candidate owned or was accountable for
- **Action**: specific concrete steps taken (not generic "I collaborated")
- **Result**: measurable outcome — use CV data; do not invent metrics
- **Reflection**: what was learned or what would be done differently — this signals seniority; junior candidates describe what happened, senior candidates extract lessons
- **Relevant for**: which of the likely screening questions this story covers

### Technical topics to review
5–8 specific topics the candidate should brush up on based on the JD's tech stack and the CV
match gaps.

### Questions to ask the interviewer
3–5 smart questions that signal genuine interest and research depth (use company research).

Output format: plain Markdown, no extra preamble.
