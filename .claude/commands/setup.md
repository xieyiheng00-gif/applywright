# /setup - Build the candidate's answer database

You are onboarding the candidate into jobfill. Goal: fill the database so plain Python can answer
most application questions, and collect the base material the AI needs for the rest.

Everything goes through the CLI (`python -m jobfill ...`). Never edit `data/jobfill.db` with raw SQL
and never write personal data into tracked files. Scratch files (patches, drafts) go in `data/`,
which is gitignored.

**Ask everything at once.** Do all the reading and checking yourself first, then send the candidate
ONE numbered questionnaire with every question you still need answered (Step 4). Never drip-feed
questions group by group across turns. Follow-ups (unanswered items, rejected values, drafts to
approve) are also sent as one batched message each.

If `$ARGUMENTS` contains `--section <name>`, skip to **Section mode** at the end.

---

## Step 0: Preflight (no questions yet)

1. `python -m jobfill init` creates `data/jobfill.db` and loads the question bank. Safe to re-run.
2. Privacy check: run `git check-ignore -q data/jobfill.db && git check-ignore -q documents/resume/x.pdf`.
   If either is NOT ignored, stop and fix `.gitignore` first.
3. `python -m jobfill status --json`. If `setup_complete` is already true, say so and offer
   **Section mode** or `/update` instead of starting over.

## Step 1: Pick the intake path

Scan `documents/` (Glob `documents/**/*`, ignore `README.md` and `.gitkeep`) and count files per
subfolder.

- If `$ARGUMENTS` contains `--portal`, or the candidate asks for the form / portal / website, use
  **Path D** (with Path A or B first when there are documents or a resume).
- Else, if `$ARGUMENTS` or the conversation already includes a resume, or `documents/` has files, do not
  ask: use **Path A** (documents folder) or **Path B** (the resume given in chat) and go to Step 2.
- Otherwise send one short message with the four options, then wait:
  - **A. Documents folder** - put resume, LinkedIn PDF export, transcripts, project write-ups in
    `documents/` (layout in `documents/README.md`) and say "go".
  - **B. Paste or attach one resume** here.
  - **C. Questionnaire only** - skip documents; Step 4 asks for everything in chat.
  - **D. Setup portal** - fill a local web form laid out like a job application (pages, required
    questions marked *), in the browser. Combines with A or B: the documents are read first and the
    form opens with what was found.

In the first message of setup, add one sentence that the profile stays on this machine in `data/`
and `documents/`, which git ignores.

## Step 2: Extract into a patch file

Read every source document. Build ONE patch file at `data/patches/<YYYY-MM-DD>-<source>.json`
(Path C: an empty patch, `{"source": "setup:questionnaire", "facts": {}, "records": {}}`):

```json
{
  "source": "setup:resume.pdf",
  "facts":   { "identity.first_name": "...", "contact.email": "...", "address.city": "..." },
  "records": { "education": [ {...} ], "experience": [ {...} ], "project": [ {...} ],
               "skill": [ {...} ], "language": [ {...} ], "certification": [ {...} ] }
}
```

- Valid fact keys, labels and enum choices: `python -m jobfill fact list`. Record fields:
  `python -m jobfill record kinds`. Copy dates as the document writes them ("Sep 2025", "9/2025",
  "2025/9", "Present"); jobfill stores them as `YYYY-MM` and writes each form's own format.
- Record only what the documents state. Metrics only when written down; never estimate.
- Projects: descriptive name, the candidate's real role and `ownership` (sole/lead/contributor),
  `is_coursework` for class projects, a factual one-line `summary`.
- Skills: one record per skill, name only unless the document states years or level (a name-only
  skill counts as "has it"; Step 4 asks for years and level).
- **Never infer sensitive facts** (gender, race, ethnicity, veteran, disability, orientation,
  criminal record, citizenship, visa status, sponsorship needs, date of birth, current salary) from
  documents, names, photos, schools or countries. Those are asked directly in Step 4.
- Note every **cross-document mismatch** (dates, titles, degree names, employer spelling between
  CV, LinkedIn and transcript) for the questionnaire. Leave the disputed value out of the patch until
  the candidate answers.

## Step 3: Dry-run the import (nothing is written)

1. `python -m jobfill import data/patches/<file>.json` returns `additive`, `conflicts`
   (only on re-runs, against values already stored), `unchanged` and `errors`.
2. Fix `errors` in the patch yourself (usually a wrong key or enum value) and dry-run again.
3. Work out what is still missing after this patch:
   `python -m jobfill missing --tier 1` and `python -m jobfill missing --tier 2` (each item has the
   exact `prompt`, its `choices`, and whether it is `sensitive`), then drop every fact key the patch
   already supplies and every record kind the patch already contains.

## Step 4: Send ONE numbered questionnaire

Path D: skip Steps 4 and 5 and follow **Portal path** below.

Combine everything that needs the candidate into a single message. Number the questions
continuously (1, 2, 3, ... across all sections) so they can reply "1. yes, 2. decline, ...".
Use the section headings below, include only sections that have questions, one line per question.

**A. Check what I found** - one line summary of what the documents gave you (counts per section:
"2 degrees, 1 job, 4 projects, 12 skills, contact details"), then:
- each cross-document mismatch: "CV says X, LinkedIn says Y - which is right?"
- each conflict with a stored value: "stored X, document says Y - keep or replace?"
- one item: "Anything above wrong or to leave out? (reply 'all ok' if not)"

**B. Identity & contact** - missing name, preferred name, pronouns (optional; "decline" is fine), email,
phone, address, LinkedIn/GitHub. Then one line: "Job portals that need an account (Workday and some
company sites) will use <contact email> to sign up: OK, or use another address?" Store the answer in
`account.login_email` (the contact email itself if they keep it, so it is not asked again).
Where the documents suggest a value, show it in brackets so "ok" confirms it.

**C. Work authorization** - primary job-search country, countries they can work in, citizenship,
US status (if US), and the two sponsorship questions. Explain the difference once, inline: "needs
sponsorship NOW = can't start without the employer filing a visa; in the FUTURE = e.g. OPT ends and
you'll need an H-1B". These answers decide many questions; they must be exact.

**D. Voluntary EEO** - one sentence: optional, never used for anything else, "decline" is a complete
answer and "decline all" covers this whole section. List gender, race (several allowed), Hispanic/
Latino, veteran, disability with their choices inline.

**E. Legal & background** - 18+, criminal conviction (never guessed, never defaulted), background
check, non-compete, companies where relatives work.

**F. Standing consents** - may jobfill tick "I certify my answers are true" and "I have read the
privacy notice" boxes? (If no, those fields stay for them to tick by hand.)

**G. Compensation, availability, preferences, documents** - currency, minimum, target, the
sentence for free-text salary boxes; earliest start, notice period, internship terms (students);
relocation, preferred locations in order, onsite/hybrid/remote, employment types, travel, default
"how did you hear about us"; path of the default resume PDF (suggest `documents/resume/`).
Students with no full-time job: how to count internships/research as years of experience.

**H. Skills** - one compact list of the skills found, each as `Skill: years / level` to fill in
(levels: beginner, intermediate, advanced, expert). Ask them to add common skills from their target
postings that they do NOT have, marked "none", so forms get an honest "No". Spoken languages and
levels (an unlisted language is answered "No" on forms).

**I. Projects** - per project: a measured outcome (if any) and the hardest problem solved.

**J. Your own words** (raw material for tier-3 answers; drafts come back for approval later):
what motivates them / what they want next, career goals (3-5 years), top strengths with an example,
one honest growth area, preferred work style, interests outside work, tone rules for written answers,
and 3-5 short true stories (failure, conflict, leadership, teamwork, pressure, learning): a sentence
or two each is enough.

Close the message with: answer in any order by number, skip anything optional, and partial answers
are fine (you will follow up once on what is left).

## Step 5: Store the answers

1. Update the patch with section A answers (mismatches resolved, items to leave out removed), then
   `python -m jobfill import data/patches/<file>.json --apply --replace <ids they chose to replace>`
   (omit `--replace` to keep every stored value).
2. Facts: `python -m jobfill fact set <key> "<value>"` (lists as `"A, B"`; natural language such as
   `"Female"` or `"I am not a protected veteran"` is mapped to the canonical choice, and ambiguous
   values are rejected).
3. Records (skills with years/level, languages, stories, project outcomes): write a patch and
   import it, or `python -m jobfill record update <id> --file ...` / `record add <kind> --file ...`
   (prefer `--file` on Windows to avoid shell quoting). Check `record list <kind>` before adding.
4. Students: set `profile.years_experience_override` from their section G answer.
5. If any required item is unanswered or a value was rejected, send ONE follow-up message listing
   all of them (keep the original numbers), then store those answers.

## Portal path (Path D): the candidate answers in a local web form

Instead of Steps 4 and 5, the candidate fills the setup portal: a page on 127.0.0.1 laid out like a
job application (My Information, Work Authorization, Experience, Education, Skills & Documents,
Application Questions, About You, Voluntary Disclosures, Review). It shows every stored value, marks
the required questions with * and checks them before Submit, and stores answers through the same
validation as `fact set` and `import`. You never fill it in yourself.

1. With documents (A or B first): after Step 3, apply only the additive part of the patch with
   `python -m jobfill import data/patches/<file>.json --apply` (no `--replace`: conflicts keep the
   stored value). Disputed values stay out of the patch; list each mismatch in your message so the
   candidate fixes it in the form.
2. Run `python -m jobfill portal` in the background. It opens the browser; its first output line
   holds the link with its key and the database it saves to (`--no-browser` only prints them). Send the candidate the link
   and one sentence: the form saves only to this computer, Submit stores the profile and sends
   nothing anywhere, Save for Later keeps a draft.
3. Wait for the command to end. It prints one JSON line per save and exits after Submit with
   `"submitted": true`, `setup_complete` and `missing_recommended`. If the candidate says they are
   done without submitting, ask them to click Submit (or stop the command and read `status`).
4. If they attached a resume in the form (`"attached"` lines) and no documents were read, offer once
   to read it (Steps 2-3) and reopen the portal with what it found.
5. Anything still missing (`python -m jobfill status`, `missing --tier 1|2`): ONE batched message, as
   in Step 5.5.
6. Step 6: the About You answers are already stored as `profile.*` facts and `story` records, in the
   candidate's words. Keep them; draft only what is still empty (and stories, if fewer than four)
   and send those drafts for approval as usual. Then Step 7.

The candidate can run `python -m jobfill portal` again at any time to review or change their profile.

## Step 6: Tier-3 base material (one approval message)

Tier-3 questions ("Why this company?", "Tell us about yourself") are written fresh for every
application; nothing static is pasted. Store their raw material once. From the section J answers
and the documents, draft all of these, and show every draft in ONE numbered message for approval:

`profile.headline`, `profile.summary` (80-150 factual words), `profile.motivations`,
`profile.career_goals`, `profile.strengths`, `profile.weaknesses`, `profile.work_style`,
`profile.interests`, `profile.writing_style`, and each STAR story as a `story` record
(situation / task / action / result, themes). Aim for four to six stories that together cover many
themes (failure, conflict, leadership, teamwork, pressure, learning, initiative, data, customer,
influence, adaptability, mentoring, creativity...): behavioral questions pick stories by theme.

Store only what they approve (`fact set`, story records via patch import); apply their edits.

## Step 7: Verify

1. `python -m jobfill status` - `setup_complete` must be yes.
2. `python -m jobfill export --format md --out data/profile_review.md` for the candidate to skim.
3. Dry run on the bundled form: `python -m jobfill resolve examples/sample_form.json --no-log --compact`.
   Report how many fields plain Python filled. Collect every remaining gap (`status` missing items,
   `needs_user` results) into one batched message, store the answers. Remove pending entries the
   test created that are noise (`python -m jobfill pending`, `python -m jobfill learn <id> --ignore`).
4. Close with what happens next: `/fill` for an application, `/update` when something changes.

---

## Section mode (`/setup --section <name>`)

Run only the matching part: `identity`, `contact`, `work_auth`, `eeo`, `legal`, `consent`,
`compensation`, `availability`, `preferences`, `documents`, `skills`, `projects`, `stories`,
`experience`, `education`, `narrative`. Show the current values
(`python -m jobfill fact list --category <cat>` or `record list <kind>`) and ask every question
for that section in one numbered message; store the answers, then run `python -m jobfill status`.

## Rules that always apply

- One batched, numbered message per round of questions; never one topic per turn.
- Ask, don't guess, for anything sensitive or anything a form would treat as a legal statement.
- Keep the questionnaire scannable: headings, one line per question, choices inline, suggested
  values in brackets.
- Re-running setup must not duplicate data: always import through a patch dry run, or check
  `record list <kind>` before `record add`.
