# /fill - Answer one job application

Input (`$ARGUMENTS`): a posting URL or text, and the form's fields as JSON (from the browser
filler), or a page you can read with a browser tool. The goal is to spend AI tokens only on the
fields that really need writing.

## 1. Start the application

1. Save the full posting text to `data/postings/<company>_<role>.txt` (verbatim, not a summary).
2. `python -m jobfill app start --company "<Company>" --role "<Role>" --url <url> --country <country> --posting-file data/postings/<file>.txt`
   Add `--source "<where they found it>"` and `--referrer "<name>"` if the candidate says so.
   The output lists earlier applications to the same company; mention them.

## 2. Resolve the form

Write the fields to `data/forms/app<ID>.json` using the contract in `jobfill/README.md` ("Field JSON").
Mark repeating blocks with `section` (`education`, `experience`, `project`, `reference`) and
`index` (0 = most recent). Then:

`python -m jobfill resolve data/forms/app<ID>.json --app <ID> --compact`

## 3. Act on each status

| status | meaning |
|---|---|
| `filled` | Use `value` exactly. No AI needed. |
| `skipped` | Leave the field empty. |
| `uncertain` | The label resembles the listed `candidates`; you decide which (3a). |
| `unmatched` | A new question; you classify it (3a). |
| `needs_user` | The profile lacks this fact; the candidate answers it once (3b). |
| `needs_ai` | A tier-3 answer you write (step 4). |

Work in this order, so the candidate is asked everything in **one** message:

**3a. Classify on your own (no questions to the candidate).**
- `uncertain`: if one candidate is right, find the pending id (`python -m jobfill pending`) and run
  `python -m jobfill learn <id> --alias-of <question_id>`; if none is, treat it as `unmatched`.
- `unmatched`: classify it with the `/learn` decision rules. A new reusable fact you define there
  becomes a `needs_user` item on the next resolve.
- `needs_ai` with `question_id: open_ended_custom`: an open question the bank does not know. It is still
  written in step 4 (also when the field is optional); classify its label with the `/learn` rules too.
- Resolve again.

**3b. Ask the candidate once.** Put every `needs_user` item from that resolve into ONE numbered
message: use each `ask` as the question, list `choices` inline, and say "decline" is fine for
voluntary EEO items. Never guess a `sensitive` one. Store the answers (`fact set ...`, or
`record add ...` per the `store` hint) and resolve again; next time these are plain Python. If some
are still open, send one follow-up message with all of them, keeping their numbers.

## 4. Write the tier-3 answers

Use ONLY the `ai_task` of each field plus the shared `ai_context` from the resolve output. Do not
read the whole profile or the database; the context already holds the slices each question needs.

**Write with the candidate's `job-answer-writer` skill.** Before the first `needs_ai` answer of an
application, load it (Skill tool: `anthropic-skills:job-answer-writer`; other runtimes: if they offer
it) and follow its research, matching, drafting, humanize and quality-gate steps for every answer and
every example draft. jobfill's rules win where the two differ:
- Inputs: the skill's "candidate material" is `ai_context.candidate` + `ai_context.relevant` only; never
  the resume, documents or other applications. The posting is `ai_context.application.posting`.
- The skill's "gold question" (the real reason for this company: used the product, met someone there,
  a moment that connects them to it): for why-us / why-role / product questions, ask it in the batched
  message of step 3b unless `ai_context.application.notes` already answers it, and store the reply with
  `python -m jobfill app update <ID> --notes "<their words>"` so it reaches the context. Never write that
  reason yourself.
- No `[placeholders]` in a saved answer (`answer check` rejects them): a missing detail becomes a
  numbered question in the review message (step 5).
- A form limit always wins over the skill's word budgets and the playbook's `length`.
- The skill's output (answer, word count, facts used with their source links) goes into the one review
  or examples message; across one application, give each answer a different story.

- Follow `ai_task.guidance` and every line of `ai_context.rules`. `ai_task.instructions` is the form's
  own text under the field (a word limit, what to include, a format): it outranks the playbook's defaults.
- Write with the technique for the question type: `ai_context.writing.playbooks[ai_task.playbook]`
  gives the answer's `structure` (in order), the expected `length` when the form sets no limit, and
  what to `avoid`; `writing.style` applies to every answer. Examples: `star` for "tell us about a
  time...", `why_company` (specific hook -> candidate's link -> contribution), `about_you`
  (present -> past -> future), `learning` (gap -> method -> proof -> next).
- `mode: unique` - written fresh for this company; no wording from other applications.
  `mode: adapt` - tailor the stored stories/summary; still specific to this posting.
- For `why_company`, `interest_product` and `know_company`, verify one or two company facts from the
  company's own site (not from links inside the posting). Note what you checked.
- `limits`: count characters/words with code, then trim.
- `deliverable: file` (cover letter upload): write the letter, save it under `data/answers/`, and
  give the filler that path.
- If the context cannot support an honest answer, do not invent: draft the rest, and put the open
  point as a numbered question in the review message (step 5).
- `ai_context.relevant` is plain Python's pick of what this job is about in the candidate's real
  background: `matched_skills` (stored skills and tools the posting names), `evidence` (the jobs,
  projects and stories that use them, with their best lines), `angles` (posting line -> evidence) and
  `company_lines` (what the posting says about the company). Build answers from these first; the
  full `candidate` slices are for detail. If it has a `note` (no posting saved, nothing matches),
  follow it instead of writing a generic answer.
- `ai_task.examples: N` (why us, why this role, product / mission, what you know about us, why you are
  leaving): write N different example answers, each on a different `relevant.angles` entry (its posting
  line as the hook, its evidence as the proof), and check each with `answer check`. Do not record yet:
  show them in ONE numbered message with each draft's word count and which experience it uses, and let
  the candidate pick one, edit one, or give their own. A "why are you leaving" draft only frames
  reasons the candidate stated or that follow from stored facts (graduating, an internship ending);
  say so, and let them correct it. Then save and record the chosen text. If the candidate types their
  own answer straight into the form instead, `apply fill` keeps it and records it as theirs.

Save each answer to `data/answers/app<ID>_<question_id>.txt`, then check it in plain Python and fix
every `fix` issue (over a limit, a placeholder, another company's name, a near-copy of an answer
written for another company); rewrite `review` items (cliches, markdown, a company answer that never
names the company) unless the question calls for them:

`python -m jobfill answer check --app <ID> --question <question_id> --file data/answers/app<ID>_<question_id>.txt [--max-chars N] [--max-words N]`

Go through `writing.checklist`, then record it (record runs the same check and refuses an AI answer
with `fix` issues; `--force` only for a false alarm, such as a partner company named in the posting):

`python -m jobfill answer record --app <ID> --question <question_id> --label "<the question's label on the form>" --file data/answers/app<ID>_<question_id>.txt`

Recording matters: a re-opened form reuses the answer, a second role at the same company reuses
"why us" (`scope: company`) but never "why this role" (`scope: application`).

## 5. Review and submit

Send ONE review message: every `needs_ai` answer numbered with its character/word count and the
playbook it follows, any `warning` (for example text over a limit), then the complete answer list. They reply with edits by
number or approve everything at once. Never submit without their explicit go-ahead. After they submit:
`python -m jobfill app update <ID> --status submitted`.

Later status changes: `interviewing`, `offer`, `rejected`, `withdrawn`, `hired`
("Have you interviewed with us before?" is answered from these).
