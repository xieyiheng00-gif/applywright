# jobfill - agent instructions

jobfill fills job application forms from a local SQLite answer database. Plain Python answers
everything it recognizes; you (the agent) handle only what it hands back: questions the candidate
must answer once, new questions to classify, and tier-3 answers that must be written per application.

## Workflows (canonical, shared with every agent runtime)

| Command | File | When |
|---|---|---|
| `/setup` | `.claude/commands/setup.md` | First run: import documents, collect facts (chat questionnaire or the setup portal), write base material |
| `/apply` | `.claude/commands/apply.md` | `/apply <job URL>`: detect the system, auto-fill (Greenhouse/Lever/iCIMS/Ashby/Workday/Workable in Python, other portals in the built-in browser) |
| `/fill`  | `.claude/commands/fill.md`  | Answer a form given as field JSON: resolve, ask gaps, write tier-3 answers |
| `/learn` | `.claude/commands/learn.md` | Classify questions the bank did not recognize |
| `/update`| `.claude/commands/update.md`| New project/job/skill, changed visa, stale tier-2 facts |

## The three tiers

1. **Static** (identity, contact, work authorization, EEO, legal, consents, education): asked once.
2. **Evolving** (skills, projects, experience, compensation, availability, preferences): stored,
   with review intervals so stale values get re-confirmed.
3. **Generated** ("why this company", "tell us about yourself", behavioral): never stored as a
   reusable answer. Written per application from a compact context; logged per application.

## Rules

- Use the CLI (`python -m jobfill ...`) for every read and write. No raw SQL against `data/jobfill.db`.
- Personal data lives only in `data/` and `documents/` (both gitignored). Put patches, drafts and
  saved postings under `data/`. Never write candidate data into tracked files.
- Never infer sensitive facts (EEO, criminal record, citizenship, visa/sponsorship, date of birth,
  salary) from documents or names. Ask; "decline" is a valid EEO answer.
- Tier-3 answers: use only the `ai_task` + `ai_context` that `resolve`/`context` return. Do not load
  the whole profile; the point of jobfill is to keep those prompts small. Write with the playbook the
  task names (`ai_context.writing`) and the candidate's `job-answer-writer` skill (load
  `anthropic-skills:job-answer-writer` before the first answer; `/fill` step 4 says how it fits these
  rules), and run `answer check` before `answer record`.
- Ground every written claim in stored data. If the data cannot support an answer, ask.
- Never submit an application without the candidate's explicit confirmation. Never type passwords
  or verification codes, never solve CAPTCHAs. Account sign-up/sign-in is `python -m jobfill login`,
  which the candidate runs in their own terminal; never run it, `account password --show`, or read
  `data/secret.env` yourself.

## Development

- Python 3.10+. The core is standard library only; `/apply`'s filler needs Playwright
  (`pip install playwright` then `python -m playwright install chromium`).
  Tests: `python -m unittest discover -s tests -t .` (browser tests skip without Playwright).
- Filler code: `jobfill/apply/` (`detect.py` system detection, `extract.js` + `forms.py` generic field
  reading/filling, `adapters.py` per-ATS quirks, `runner.py` commands, `browser.py` shared window,
  `pages.py` which page the candidate is on). The candidate shares the window: jobfill never overwrites
  a value it did not type (state `typed`), follows them when they click Next, and pauses when they go
  Back. Multi-page behavior is tested on `tests/fixtures/wizard_like.html`.
- Workable: `detect.py` `_workable` (posting links, short links, company pages; the public API for the posting
  and company name, 404 = removed) and the `Workable` adapter (cookie banner declined, Education / Experience
  through `Adapter.entries` one editor at a time with `data-jobfill-index`, react-datepicker typed key by key,
  intl-tel-input phone by its country code, read-only selects, role=radio / role=checkbox boxes, "Address" as
  `location_full` via `Adapter.adjust`). Its optional EEO survey after Submit (US jobs) is `Adapter.survey`:
  `runner.submit_page` reports it, `apply fill` answers it (a radio still on the page's own preselection is
  `preset`, not an answer), and only another `apply submit --confirm` sends it. Tested on
  `tests/fixtures/workable_like.html` (`#eeo`, `#ofccp`) in `tests/test_workable.py`.
- Accounts: `jobfill/accounts.py` (secret.env, HMAC-SHA256 per-company passwords, login email),
  `jobfill/apply/login.py` (sign in / create / verify flow), `jobfill/mail.py` (optional IMAP reader).
  The `accounts` table holds no passwords. Login is tested only on `tests/fixtures/auth_like.html`.
- Open motivation questions (why us / why this role / product / why leaving): `jobfill/relevance.py`
  matches the stored skills, jobs, projects and stories to the saved posting (`ai_context.relevant`:
  matched skills, evidence, angles, company lines); tasks with `examples` get that many drafts for the
  candidate to pick from before anything is recorded.
- Lists never get a look-alike: a stored name (school, field of study) with no exact option is left
  empty and asked (`needs_user` with `set_with`). A search-as-you-type list that shows only its first
  entries is searched first (`forms.search_combobox`: earlier picks, the name, then its distinctive words
  from `text.search_queries`), and a result is picked only if `names_same` holds; tested on
  `tests/fixtures/school_search_like.html`. The candidate's pick, made in the window or through
  `apply set`, goes to `learned_choices` and is reused where a later form offers it (`choice list`).
- Resume per application: `jobfill/resumes.py` picks a tailored CV under `documents/resume/` by file name
  (company, then role words) at `apply start`; `applications.resume` holds it (`--resume` overrides) and
  the `resume_file` rule falls back to `documents.resume_path`. Tested in `tests/test_resumes.py`.
- Field instructions: `extract.js` reads the text under a field (`aria-describedby`, description/help
  elements in its block) into `description`; the resolver passes it on as `instructions` and fills a
  "None / N/A if it doesn't apply" answer when a rule has nothing (`text.not_applicable_answer`).
- Result record: every question of an application is a row in `answers` (status, source, fill);
  `jobfill/report.py` turns it into `data/applications/app<ID>_*.md|json` and `app stats`.
- Setup portal: `jobfill/portal/` (`python -m jobfill portal`), a local form laid out like an
  application portal. `layout.py` places every built-in fact on one page in portal wording (a new fact
  in `facts.py` must be placed there too; `tests/test_portal.py` checks); required marks come from the
  catalog's `setup="required"` and `store.RECORD_SETUP`. `form.py` reads and saves through `Store` and
  writes nothing when any field fails. `server.py` binds 127.0.0.1, checks the Host header and a key
  kept per database (so a tab left open saves again after a restart), and exits after Submit. Saves
  send only what changed on the page; records go only when the page deletes them. The candidate
  fills it; the agent only starts it.
- Built-in catalog: `jobfill/catalog/` (`facts.py`, `records.py`, `choices.py`, `questions.py`).
  `python -m jobfill init` re-seeds it into existing databases without touching learned rows.
- Adding a built-in question: add it to `questions.py` (tier 3: give it a `playbook` from
  `catalog/playbooks.py`), add a real-world label to `CASES` in `tests/test_matcher.py`, run the tests
  (they also reject alias collisions between questions). Draft checks live in `jobfill/writing.py`.
