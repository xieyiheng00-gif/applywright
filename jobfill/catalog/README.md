# The question bank

How jobfill decides what kind of question it is looking at, where the answer comes from, and how it
picks up questions it has not seen before. The built-in bank lives in this folder: `facts.py`,
`records.py`, `choices.py`, `questions.py` and `playbooks.py`.
Back to the [main README](../../README.md).

## The three tiers

| Tier | What | Examples | How it's answered | Changes |
|---|---|---|---|---|
| **1 static** | Facts that essentially never change | name, contact, work authorization, sponsorship, EEO, veteran/disability, 18+, background check, education | stored fact or plain-Python rule | asked once at setup |
| **2 evolving** | Facts and records that change over months | skills + years, projects, experience, salary, start date, relocation, preferred locations, resume file, STAR stories | stored fact, record, or rule over records | each fact has a review interval; `status` flags stale ones |
| **3 generated** | Answers that must be unique to the application | why this company, why this role, tell us about yourself, behavioral, cover letter | written by the agent from `ai_task` + `ai_context`; logged per application | never stored as a reusable answer |

Tier-3 details:

- `mode: unique` answers are written fresh from facts + the posting. Answers given to other
  companies are never shown to the model, so they cannot be recycled.
- `mode: adapt` answers tailor stored base material (STAR stories, summary, strengths).
- `scope` controls reuse: `company` (e.g. "why us" when the same company asks again for another
  role) or `application` (e.g. "why this role": never reused).

## Rules instead of stored variants

Rules derive answers instead of storing every variant: "Are you authorized to work in Canada?",
"Will you now or in the future require sponsorship?", "How many years of Python experience?" (range
dropdowns included), "Have you applied to {company} before?", "Do you have relatives at
{company}?", "Highest degree", "Are you comfortable working in office 3 days a week?", GPA as an exact
number, a range ("3.5 - 4.0", "3.75 and above") or a threshold ("Is your GPA 3.0 or higher?") without
ever passing a 10-point GPA off as a 4.0 one, "How did you hear about us?" in flat, combined
("Job Board: LinkedIn"), grouped or two-step form (pick "Job Board", then "Which job board?"), and so on.

## Written answers (tier 3)

Open questions ("Why us?", "Tell us about yourself", "What did you learn recently?", "Tell us about
a time...", "How do you use AI in your work?", first 90 days, values, gaps) are recognized like any
other question, then written per application, also when the form marks them optional (a blank essay
reads as no interest). Only "anything else?", "questions for us?", cover letters and employment gaps
stay blank unless the field is required. An open question the bank does not know is still written
(`open_ended_custom`) and queued for `/learn`. Each `needs_ai` task names a **playbook**, the
technique recruiters expect for that question type (STAR for behavioral, hook -> link -> contribution
for "why us", present -> past -> future for "about you", gap -> method -> proof for learning...).
`ai_context.writing` carries the shared style rules, only the playbooks used on that form, and a
checklist. Before recording, `answer check` tests the draft in plain Python: limits, placeholders,
another company's name, a near-copy of an answer written for another company, cliches, markdown.

## How the database keeps up with new questions

1. An unknown label is saved in `pending_questions` with its options and the company.
2. The agent classifies it (`/learn`): a new phrasing of a known question (alias or regex), a new
   reusable fact (tier 1/2), or a new tier-3 question.
3. Next time any company asks it, plain Python answers. Learned questions can be exported and
   shared without personal data (`catalog export-learned`).

Missing facts work the same way: the first form that needs one asks you, the answer is stored, and
every later form uses it.
