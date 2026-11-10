# jobfill

jobfill fills in job application forms for you. You answer each question once. After that, plain
Python types your answers into every application, and an AI agent (Claude Code, Codex, ...) writes
only the open questions, such as "Why do you want to work here?".

## What it does

- **Keeps your answers in one place.** Name, contact details, work authorization, education,
  experience, skills and the rest are stored once, in a database on your own computer.
- **Fills the form in a browser window you can watch.** Greenhouse, Lever, iCIMS, Ashby, Workday,
  Workable and SmartRecruiters forms are filled by plain Python, page by page. The agent fills other company portals.
- **Writes the open questions for each job.** "Why us?", "Tell us about yourself" and "Tell us about
  a time..." are written from your stored experience and the job posting, not recycled from an
  answer given to another company.
- **Asks only what it does not know.** A new question is asked once, stored, and answered by plain
  Python the next time any form asks it.
- **Leaves you in charge.** You can type in the same window, and jobfill keeps what you typed. It
  never submits without your go-ahead, never solves CAPTCHAs, and the agent never types a password.
- **Stays on your computer.** Your data lives in `data/` and `documents/`, which git ignores.

## How to use it

You need Python 3.10 or later and an AI coding agent such as Claude Code, opened in this folder.

### 1. Install (once)

```bash
python -m pip install playwright
```

```bash
python -m playwright install chromium
```

```bash
python -m jobfill init
```

The first two install the browser that jobfill fills forms in, the only package it needs besides
Python. The third creates the database.

### 2. Build your profile (once)

Put your resume and other career documents in `documents/` (optional), then tell the agent:

```
/setup
```

It reads the documents and asks for everything still missing in one numbered list of questions.
Nothing is stored until you confirm. To fill in a web form instead, run:

```bash
python -m jobfill portal
```

### 3. Apply

```
/apply <job URL>
```

A browser window opens. jobfill fills in what it knows, and the agent writes the open questions and
asks you about anything new. If the site needs an account (Workday always does), the agent asks you
to run this in your own terminal:

```bash
python -m jobfill login --app <ID>
```

### 4. Check and submit

Read the form in the window and change anything you like. When it is right, tell the agent to
submit, or click Submit yourself.

## Commands

Tell the agent (slash commands in Claude Code; in other agents say it in words, see
[AGENTS.md](AGENTS.md)):

| Command | What it does |
|---|---|
| `/setup` | Builds your profile from your documents and one list of questions |
| `/apply <job URL>` | Fills in the application at that link |
| `/update` | Changes your profile: a new job, project or skill, a changed visa, facts that went stale |
| `/learn` | Teaches jobfill the questions it did not recognize |
| `/fill` | Answers a form you give as a file of fields instead of a link |

Run in your terminal:

| Command | What it does |
|---|---|
| `python -m jobfill init` | Creates the database (safe to run again) |
| `python -m jobfill portal` | Opens a local web form to fill in or review your profile |
| `python -m jobfill status` | Shows what is still missing or out of date |
| `python -m jobfill login --app <ID>` | Signs in to, or creates, your account on a job site. Only you run this, never the agent |
| `python -m jobfill app list` | Lists your applications |
| `python -m jobfill app report <ID>` | Writes the record of one application: every question, its answer and who answered it |
| `python -m jobfill app stats` | Totals how much plain Python, the AI and you answered across all applications |
| `python -m jobfill apply close` | Closes the jobfill browser window |
| `python -m jobfill --help` | Lists every command |

## More

| Read | For |
|---|---|
| [jobfill/apply/README.md](jobfill/apply/README.md) | Supported sites, working in the same window, accounts and login, tailored CVs, the record of each application |
| [jobfill/catalog/README.md](jobfill/catalog/README.md) | The three kinds of question, how open questions are written, how new questions are learned |
| [jobfill/portal/README.md](jobfill/portal/README.md) | The setup portal |
| [documents/README.md](documents/README.md) | Which documents to add, and where |
| [examples/README.md](examples/README.md) | A made-up profile and form to try first |
| [jobfill/README.md](jobfill/README.md) | How it works inside, the form format, every command, the code layout, measuring time |
| [CLAUDE.md](CLAUDE.md) | The rules the agent follows |
