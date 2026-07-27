{% include 'shared.md' %}

---

# LinkedIn Outreach — Power Move

Generate a 300-character LinkedIn connection request for the candidate to send
to a contact at **{{ company }}** about the **{{ role }}** opening.

## Inputs

**Company**: {{ company }}
**Role**: {{ role }}
{% if jd_text %}
**JD excerpt**:
<<<JD_TEXT_BEGIN>>>
{{ jd_text[:1500] }}
<<<JD_TEXT_END>>>
{% endif %}
{% if cv_excerpt %}
**Candidate CV excerpt**:
{{ cv_excerpt[:1500] }}
{% endif %}
{% if research_context is defined and research_context %}

## Live web snippets (Brave WebSearch)

Use one of these recent items as the **Hook** sentence — preferring news,
product announcements, or eng blog posts the recipient is likely to recognise
as their own work or their team's.

{{ research_context }}
{% endif %}

## Task

### 1. Target list
Identify likely useful targets at {{ company }} (you do not need real names —
suggest the *role* of person to find via LinkedIn search). Up to 4:
- Hiring manager for the team that owns this role
- Recruiter likely assigned to the req
- 2 peers (engineers/PMs with the same archetype)

### 2. Primary target
Pick ONE target above whose day-to-day would most benefit from the candidate
joining. Justify in one sentence.

### 3. The message — 3-sentence framework

| Sentence | Purpose | Rules |
|----------|---------|-------|
| **Hook** | Specific to this company / their AI challenge — never generic | Reference one concrete signal from the JD or recent product / engineering blog post |
| **Proof** | Strongest quantified achievement from the candidate's CV that maps to the role | Format: "I built X that delivered Y" — never "I'm passionate about Z" |
| **Proposal** | Low-pressure ask: short chat about a specific topic | "Would love to chat about [topic] for 15 min" — never share phone number |

### 4. Output

Produce one ready-to-send EN message under **300 characters total** (LinkedIn
connection-request limit). If the company is in a Spanish-speaking market,
also produce an ES variant under 300 characters. Otherwise EN only.

### 5. Alternative targets

For each remaining target from step 1, give a 1-sentence rationale of why
they're a good second-choice contact (different angle of value).

### Hard constraints

- Never use "passionate", "excited", "love to connect", or other filler.
- The hook must reference one specific thing about {{ company }} that you
  can plausibly source from the JD or public knowledge — no generic praise.
- Every message must be ≤ 300 characters including spaces.
- Output Markdown only.
