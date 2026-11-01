# jobfill

A local answer database for job application forms. Plain Python answers the questions it knows;
an AI agent (Claude Code, Codex, ...) is called only for the few answers that must be written for
each application, and it gets a small, targeted context instead of your whole profile.

```
form fields ──► matcher ──► question bank ──► answer strategy ──► value for the form
 (labels,        exact alias      ~185 types       fact / template /        filled  (no tokens)
  options,       regex pattern    ~1,500           derived rule / record
  section)       fuzzy score      phrasings        ── ai ──────────────────► needs_ai  (compact context)
                     │                            missing fact ───────────► needs_user (asked once, stored)
                     └── unknown ──► pending queue ──► /learn ──► new alias / question (python next time)
```

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

Rules derive answers instead of storing every variant: "Are you authorized to work in Canada?",
"Will you now or in the future require sponsorship?", "How many years of Python experience?" (range
dropdowns included), "Have you applied to {company} before?", "Do you have relatives at
{company}?", "Highest degree", "Are you comfortable working in office 3 days a week?", GPA as an exact
number, a range ("3.5 - 4.0", "3.75 and above") or a threshold ("Is your GPA 3.0 or higher?") without
ever passing a 10-point GPA off as a 4.0 one, "How did you hear about us?" in flat, combined
("Job Board: LinkedIn"), grouped or two-step form (pick "Job Board", then "Which job board?"), and so on.

## Quick start

Requires Python 3.10+, no third-party packages.

```bash
python -m jobfill init
```

Then, in Claude Code, run `/setup` (Codex and other agents: "set me up", see `AGENTS.md`). The agent
reads `documents/` (or a pasted resume), checks what it found against what is still missing, and
sends you one numbered questionnaire with every open question. Nothing is written until you confirm.

Try it with the fictional sample profile:

```bash
python -m jobfill --db data/demo.db import examples/sample_profile.json --apply
```

```bash
python -m jobfill --db data/demo.db app start --company "Acme Robotics" --role "ML Infrastructure Engineer"
```

```bash
python -m jobfill --db data/demo.db resolve examples/sample_form.json --app 1 --compact
```

On the 50-field sample form, plain Python fills about 40 fields; 4 go to the agent.

## Applying from a URL: `/apply <job URL>`

```bash
python -m pip install playwright
```

```bash
python -m playwright install chromium
```

1. **Detect** (`apply start <url>`, plain Python): URL rules, then the page HTML (embedded
   Greenhouse/Lever/iCIMS/Ashby forms, schema.org `JobPosting`). Not a job posting (search page, company
   board, removed posting, unrelated page) is an error. Greenhouse and Lever postings are also read
   through their public APIs, so the posting text costs no browsing.
2. **Fill**, by system:

| System | Who fills | Notes |
|---|---|---|
| Greenhouse, Lever, iCIMS, Ashby | jobfill's Playwright filler (`apply fill`) | visible Chromium window that stays open; React dropdowns, autocompletes, radio/checkbox groups, yes/no toggle buttons, resume upload; re-reads the form for follow-up questions; iCIMS email step and multi-page `apply next` |
| Workday | `jobfill login` (you run it), then the Playwright filler | Apply → Apply Manually → sign in / create account; multi-page `apply next`; search-and-pick menus, including two-level ones (Job Boards → LinkedIn), read by their pills; "Select One" dropdowns |
| Company portals (Amazon, TikTok, Google, ...) and other ATSs (SmartRecruiters, Workable, ...) | the agent in the built-in browser; portals that need an account use the jobfill window (`--ats generic`) so the login carries over | answers still come from `jobfill resolve` |
| LinkedIn / Indeed listings | the agent follows the employer's apply link first | |

The filler never solves CAPTCHAs, never accepts terms without a standing consent, and never submits:
`apply submit --confirm` runs only after the candidate says so (or they click Submit themselves).
`apply fill --dry-run` previews what would be filled without typing.

**You can work in the window too.** Before every fill, set, next or submit, jobfill reads which page
of the application is showing (by the progress bar's current step, or by the page's questions):

- Answers you typed or changed are kept: jobfill only ever replaces values it typed itself, records
  yours as answered by you, and does not ask or write them again.
- You clicked Next yourself: jobfill continues on that page and does not click Next again.
- You clicked Back to check an earlier page: jobfill pauses (no typing, clicking or submitting) until
  you return to the furthest page or move on. `apply watch` tells the agent when that happens; it
  fills an earlier page only if you ask (`--here`).

### Accounts and login (Workday, company portals)

Sites that need an account are handled by `python -m jobfill login --app <ID>`, which **you run in your
own terminal** (it refuses to run otherwise, so the agent can never type a password):

- **Email:** your contact email by default; the first login asks whether to use another address and
  remembers it (`account.login_email`, change it with `jobfill account email --set ...`).
- **Password:** `HMAC-SHA256(secret, company name)`, written as 16 characters with upper, lower, digit
  and symbol so it passes typical rules. The secret is generated once in `data/secret.env` (gitignored;
  back it up). Nothing else is stored: the `accounts` table records only site, email, status and a
  version number. `jobfill account password --host <site> --show` prints a password in your terminal
  if you ever need to log in by hand; after a reset on the site, `jobfill account rotate --host <site>`
  moves to the next derived password.
- **Flow:** signs in if jobfill created the account before, else creates it (filling password + confirm,
  accepting the terms only if you allowed that once), switches to sign-in when the site says the email
  already exists, never touches hidden honeypot fields, and stops for CAPTCHAs. A portal that opens on
  "Create Account" for a company jobfill already has an account with goes straight to "Sign In"; a
  Workday company is one account whichever of its addresses (myworkdayjobs / myworkdaysite) you use.
- **Email verification (optional):** fill the `JOBFILL_IMAP_*` lines in `data/secret.env` (for Gmail, an
  app password; check with `jobfill mail test`). `login` then reads the portal's verification email
  read-only, enters the code, or opens the link only if it points at the portal's own domain. Without
  it, you enter the code or click the link yourself and run `login` again.

### The result record of each application

Every question seen on a form is saved with the application: the question, the answer, who answered
it (your profile via plain Python, the AI, or you), and whether jobfill typed it in. The record is
written to `data/applications/app<ID>_<company>_<role>.md` (readable) and `.json`, and is refreshed
after every fill, recorded answer, and status change:

```
## Result
| Answered by                        | Questions |
| Your profile (plain Python, no AI) | 11 |
| AI                                 | 1  |
| You                                | 0  |
| Left blank                         | 2  |
| Still open                         | 0  |
Plain Python answered 92% of the answered questions without any AI tokens.

## Questions
| # | Question  | Answer        | Answered by                        | Into the form    |
| 2 | Full name | Jordan Rivera | your profile (plain Python, no AI) | typed by jobfill |
...
```

`python -m jobfill app report <ID>` regenerates it; `python -m jobfill app stats` totals how much
plain Python, the AI and you answered across all applications.

## Field JSON (what a form filler sends to `resolve`)

```json
{"fields": [
  {"id": "q7", "label": "Will you require sponsorship?", "type": "select", "options": ["Yes", "No"], "required": true},
  {"id": "e0", "label": "Company", "type": "text", "section": "experience", "index": 0},
  {"id": "w",  "label": "Why do you want to work at Acme?", "type": "textarea", "max_chars": 1000},
  {"id": "d",  "label": "Start date", "type": "date", "format": "MM/DD/YYYY"}
]}
```

- `type`: `text`, `textarea`, `email`, `tel`, `url`, `number`, `date`, `select`, `radio`,
  `checkbox`, `multiselect`, `file`.
- `options`: strings or `{"label", "value"}` objects. jobfill returns the option to pick.
- `section` + `index`: repeating blocks (`education`, `experience`, `project`, `reference`), 0 = most recent.
- `format` / `placeholder`: dates. An explicit `format` (`YYYY`, `MM`, `DD`, `MMM`, `MMMM`, e.g. `MM/YYYY`)
  wins; otherwise jobfill reads it from the label or placeholder ("MM/YYYY", "e.g. Sep 2025"). Split fields
  ("Start month" + "Start year", or "Month"/"Year" under a "Graduation date" heading) get just their part,
  and month dropdowns match "September", "Sep" or "09". Defaults: `MM/YYYY` for date fields, "June 1, 2027"
  for questions like "When can you start?". `max_chars` / `max_words`: limits for written answers.
- `required`: optional fields with nothing stored are skipped instead of asked.

Each result has a `status`: `filled` (use `value`), `needs_ai` (agent writes it from `ai_task` and
the shared `ai_context`), `needs_user` (ask once, store with the given command), `uncertain`
(agent picks one of `candidates`), `unmatched` (new question, queued for `/learn`), `skipped`.

### Written answers (tier 3)

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

## CLI

```
init | status | missing [--tier] [--level]
fact get|set|unset|confirm|list|define|history
record list|add|update|remove|kinds
import <patch.json> [--apply] [--replace ids] [--replace-all]
export [--format json|md] [--tier] [--out]
match "<label>" [--section] [--type] [--company]        (debug the matcher)
resolve <fields.json> [--app ID] [--no-log] [--compact]
context --question <id> [--app ID] [--max-chars N]      (AI context for one tier-3 question)
answer check|record|list                                 (check a written answer, then log it)
apply detect|start <url> | apply fill|set|next|submit|watch|status --app ID | apply close
login --app ID  (you run it) | account email|list|password|rotate|forget | mail test
app start|list|show|update
pending | learn <pending_id> --alias-of|--new-question|--ignore
question list|show|alias | catalog export-learned|import-learned
```

`--db <path>` or `JOBFILL_DB` selects the database (default `data/jobfill.db`).

## Layout

```
jobfill/
  catalog/      built-in schema: facts.py (tier 1-2 facts), records.py (repeating data),
                choices.py (canonical values + option synonyms), questions.py (question bank)
  db.py         SQLite schema and seeding      store.py   profile, bank, answer log
  matcher.py    label -> question              derive.py  plain-Python answer rules
  resolver.py   fields -> answers / AI tasks   cli.py     commands
  apply/        /apply: detect.py (which system), extract.js + forms.py (read/fill any form),
                adapters.py (Greenhouse/Lever/iCIMS/Ashby quirks), runner.py, browser.py (shared window)
  report.py     per-application result record (data/applications/)
.claude/commands/  agent workflows: setup, apply, fill, learn, update (AGENTS.md points other agents here)
documents/      your source documents (gitignored)     data/  the database and drafts (gitignored)
examples/       fictional profile + sample form        tests/ unittest suite
```

## Privacy

All personal data stays in `data/` and `documents/`, both gitignored. EEO, criminal-record,
visa/sponsorship, date-of-birth and salary answers are never inferred from documents; the agent asks.
The AI context for written answers carries only the profile slices a question needs: never EEO
answers, contact details or address.
