{% include 'shared.md' %}

---

# Personalization Plan

You are a career coach designing a tailored application strategy.

## Candidate CV
{{ cv }}

{% if article_digest %}
## Article Digest (detailed proof points — metrics here take precedence over cv.md)
{{ article_digest }}
{% endif %}

## CV Match Summary
{{ evaluation_blocks.cv_match }}

## Company Research
{{ evaluation_blocks.comp_research }}

## Job
**Company**: {{ jd_meta.company }}
**Title**: {{ jd_meta.title }}
**Archetype**: {{ archetype.archetype }}

## JD Text
{{ jd_text }}

## Task
Design a concrete personalization plan for this application.

### Resume summary angle
One paragraph (3–5 sentences) that frames the candidate's narrative for this specific role.
Must be grounded in CV evidence. Do not invent experience. Never state a quantified tenure
total ("X+ years of experience", "two decades") — it triggers age/over-qualified screens;
downstream prompts inherit this angle verbatim.

### Projects to keep / drop
The CV's Projects section must be pruned per JD. List which projects to KEEP (ordered by
relevance to this JD, with one line on why) and which to DROP. A project that covers a JD
must-have or nice-to-have gap is the strongest keep.

### Top 3 proof points
The 3 strongest CV achievements to lead with for this role. For each:
- The achievement (quoted or paraphrased from CV)
- Why it is relevant to this JD
- How to frame it (metric, scope, impact)

### Keywords to include
10–15 keywords from the JD that appear or should appear in the resume/cover letter.
Distinguish: Already in CV / Missing but claimable / Should not claim.

### Cover letter hook
One opening sentence that is specific to this company (use research, not generic praise).

### What NOT to emphasise
Experience or skills in the candidate's CV that could create a negative signal for this role
(e.g. overqualification, irrelevant pivots, conflicting domain).

Output format: plain Markdown, no extra preamble.
