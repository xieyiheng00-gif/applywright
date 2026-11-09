# Inside jobfill

How the answer database works, the format of a form, every command, where the code lives and how to
measure where the time goes. Back to the [main README](../README.md).

| Topic | Where |
|---|---|
| The three tiers, rules, written answers, learning new questions | [catalog/README.md](catalog/README.md) |
| Applying from a URL: supported sites, the shared window, accounts, the result record | [apply/README.md](apply/README.md) |
| The setup portal | [portal/README.md](portal/README.md) |
| A sample profile and form to try | [../examples/README.md](../examples/README.md) |

## How a form is answered

jobfill is a local answer database for job application forms. Plain Python answers the questions it knows;
an AI agent (Claude Code, Codex, ...) is called only for the few answers that must be written for
each application, and it gets a small, targeted context instead of your whole profile.

```
form fields ──► matcher ──► question bank ──► answer strategy ──► value for the form
 (labels,        exact alias      ~210 types       fact / template /        filled  (no tokens)
  options,       regex pattern    ~1,800           derived rule / record
  section)       fuzzy score      phrasings        ── ai ──────────────────► needs_ai  (compact context)
                     │                            missing fact ───────────► needs_user (asked once, stored)
                     └── unknown ──► pending queue ──► /learn ──► new alias / question (python next time)
```

## Field JSON (what a form filler sends to `resolve`)

```json
{"fields": [
  {"id": "q7", "label": "Will you require sponsorship?", "type": "select", "options": ["Yes", "No"], "required": true},
  {"id": "e0", "label": "Company", "type": "text", "section": "experience", "index": 0},
  {"id": "w",  "label": "Why do you want to work at Acme?", "type": "textarea", "max_chars": 1000},
  {"id": "c",  "label": "Current Company", "type": "text", "required": true,
   "description": "If it doesn't apply to you, please enter \"None\"."},
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
- `description`: the form's own instructions under the field (the filler reads `aria-describedby` and
  description/help text in the field's block). When the stored data has no answer and the instructions
  say what to enter if the question does not apply ("None", "N/A"), that is filled (free-text fields
  only). Every `needs_user`, `needs_ai`, `uncertain` and `unmatched` result carries it as `instructions`.

Each result has a `status`: `filled` (use `value`), `needs_ai` (agent writes it from `ai_task` and
the shared `ai_context`), `needs_user` (ask once, store with the given command), `uncertain`
(agent picks one of `candidates`), `unmatched` (new question, queued for `/learn`), `skipped`.

## CLI

```
init | status | missing [--tier] [--level] | portal [--port N] [--no-browser] [--keep-open]
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
app start|list|show|update|report|stats|timing|giveup
choice list|forget                                       (picks remembered where no option matched exactly)
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
                adapters.py (Greenhouse/Lever/iCIMS/Ashby/Workday/Workable quirks), runner.py, browser.py (shared window)
  report.py     per-application result record (data/applications/)
  timing.py     development: where the time went (off unless JOBFILL_TIMING is set)
  portal/       setup portal: layout.py (pages and wording), form.py (values in and out through the
                store), server.py (127.0.0.1, key per database), static/ (the page)
.claude/commands/  agent workflows: setup, apply, fill, learn, update (AGENTS.md points other agents here)
documents/      your source documents (gitignored)     data/  the database and drafts (gitignored)
examples/       fictional profile + sample form        tests/ unittest suite
```

## Measuring time (for development)

To find out where filling is slow, turn on the timing log for one checkout by putting this in its
`.claude/settings.local.json` (gitignored), then starting a new session there:

```json
{
  "env": {"JOBFILL_TIMING": "1"},
  "hooks": {
    "Stop": [{"hooks": [{"type": "command", "command": "python \"$CLAUDE_PROJECT_DIR/jobfill/timing.py\" pause --hook 2>/dev/null || true"}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python \"$CLAUDE_PROJECT_DIR/jobfill/timing.py\" resume --hook 2>/dev/null || true"}]}]
  }
}
```

`JOBFILL_TIMING` makes every jobfill command log when it ran; the hooks mark the wait from the agent ending its turn
to your reply. They take about a tenth of a second per turn and print nothing. Each application's record then gets a
"Time" section, and:

```bash
python -m jobfill app timing
```

```bash
python -m jobfill app timing 62
```

The first shows each run (a batch, as a whole), each application, the agent's time per written answer, the applications
given up on and the work before that, and the slowest steps and fields inside jobfill. The second shows every step of
one application.

| Time | How it is known |
|---|---|
| jobfill | Measured inside each command. `apply fill` also splits it: connecting to the browser, opening the page, reading the form, resolving, typing, per field. |
| The agent | The silence before a command is the agent working towards it, whatever it did meanwhile. Before `answer check` / `answer record` it is writing that answer (answers checked in one go share it by length); before `learn`, `pending`, `match` it is classifying questions; before a command with no application it belongs to the batch. |
| Waiting on you | `apply watch`, `login` and the time around it, and the wait the hooks mark. A wait inside the agent's turn (a question tool) counts as the agent's. |
| Nobody | A silence over 15 minutes counts for no one and ends the run. |

Several agent sessions on one database are kept apart by `CLAUDE_CODE_SESSION_ID` (or `JOBFILL_SESSION`). A link that
ends with no application (Easy Apply only) shows only in its run's clock.

## Privacy

All personal data stays in `data/` and `documents/`, both gitignored. EEO, criminal-record,
visa/sponsorship, date-of-birth and salary answers are never inferred from documents; the agent asks.
The AI context for written answers carries only the profile slices a question needs: never EEO
answers, contact details or address.
