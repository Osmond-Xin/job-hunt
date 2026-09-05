{% include 'shared.md' %}

---

# CV Match Analysis

You are a rigorous technical recruiter matching a candidate's CV against a job description.

## Candidate CV
{{ cv }}

{% if article_digest %}
## Article Digest (detailed proof points — metrics here take precedence over cv.md)
{{ article_digest }}
{% endif %}

## Job Description
**Company**: {{ jd_meta.company }}
**Title**: {{ jd_meta.title }}

<<<JD_TEXT_BEGIN>>>
{{ jd_text }}
<<<JD_TEXT_END>>>

## Archetype detected
{{ archetype.archetype }} (confidence {{ archetype.confidence }})

## Task
For each **must-have** requirement in the JD, find direct evidence in the CV.

Rules:
- Quote the exact CV phrase that supports each claim. Do not paraphrase.
- If no evidence exists, state "No evidence found" — do not invent or extrapolate.
- A partial match is better than a fabricated one; label it "Partial".
- Evidence from the CV's **Projects** section counts — label it "Full (project)" or
  "Partial (project)" so downstream prompts can use the project as closing-the-gap proof.

Output a Markdown table:

| Requirement | Match | CV Evidence (exact quote) | Gap / Note |
|---|---|---|---|
| ... | Full / Partial / None | "..." | ... |

Then repeat the same table for the JD's **nice-to-have / preferred** requirements
(heading: `### Nice-to-have match`). These matter downstream: a personal project that
covers a nice-to-have is the candidate's strongest pivot when the JD's vertical is new.

After the tables, add:
- **Strengths**: 3–5 bullets where the CV clearly exceeds requirements.
- **Gaps**: hard blockers or significant missing experience. State each gap plainly —
  downstream prompts must acknowledge gaps honestly, not paper over them.
- **Domain verticals**: one line — is the JD's industry vertical (e.g. industrial IoT,
  fintech, healthcare) visibly represented in the CV's *employment* history? Answer
  "yes (where)", "only via projects (which)", or "no".
