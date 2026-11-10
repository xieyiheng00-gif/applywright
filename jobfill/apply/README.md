# Applying from a URL: `/apply <job URL>`

What happens when you give jobfill a job link: which sites it can fill, how it shares the browser
window with you, how accounts and logins work, and the record it keeps of every application.
Back to the [main README](../../README.md).

## Install the browser (once)

```bash
python -m pip install playwright
```

```bash
python -m playwright install chromium
```

## Detect, then fill

1. **Detect** (`apply start <url>`, plain Python): URL rules, then the page HTML (embedded
   Greenhouse/Lever/iCIMS/Ashby/Workable/SmartRecruiters forms, schema.org `JobPosting`). Not a job posting (search
   page, company board, removed posting, unrelated page) is an error. Greenhouse, Lever, Ashby, Workable and
   SmartRecruiters postings are also read through their public APIs, so the posting text costs no browsing.
2. **Fill**, by system:

| System | Who fills | Notes |
|---|---|---|
| Greenhouse, Lever, iCIMS, Ashby | jobfill's Playwright filler (`apply fill`) | visible Chromium window that stays open; React dropdowns, autocompletes, radio/checkbox groups, yes/no toggle buttons, resume upload; re-reads the form for follow-up questions; iCIMS email step and multi-page `apply next` |
| Workday | `jobfill login` (you run it), then the Playwright filler | Apply → Apply Manually → sign in / create account; multi-page `apply next`; search-and-pick menus, including two-level ones (Job Boards → LinkedIn), read by their pills; "Select One" dropdowns |
| Workable (`apply.workable.com/<company>/j/<code>`, `<company>.workable.com`, company pages linking or framing it) | jobfill's Playwright filler | one page; declines the optional cookies (the banner blocks the form until answered); Education / Experience entered one at a time (+ Add, fill, Update; existing cards count, an editor the candidate opened is left alone); date pickers typed key by key (Workable takes no date after the current month: an expected graduation is dropped by the picker and reported under `fill_failed`, the entry reads as ongoing); phone country set by the stored code; "Address" answered as city, region and country; yes/no and choice boxes, read-only selects, the privacy consent; never uses "Autofill application"; a Cloudflare Turnstile check at submit is the candidate's. US jobs may show an optional EEO survey right after Submit: `apply submit` reports it (`survey: "eeo"`), `apply fill` answers it from the stored EEO facts (decline included; the survey's own preselection is not taken as an answer), and it goes in only with another `apply submit --confirm` |
| SmartRecruiters (`jobs.smartrecruiters.com/<company>/<id>`, its "I'm interested" form, company pages linking to it) | jobfill's Playwright filler | fields drawn by web components (read through their shadow roots); City picked among its suggestions by city and region / country (none matching: asked, never a look-alike); phone country set by the stored code; Experience / Education entered one at a time (Add, fill, Save; existing cards count, an editor the candidate opened is left alone); month pickers typed; resume drop zone; page 2 screening questions and consents with `apply next` (never "Select all"); never uses the "Easy Apply" import; a DataDome CAPTCHA is the candidate's |
| Company portals (Amazon, TikTok, Google, ...) and other ATSs (Jobvite, Taleo, ...) | the agent in the built-in browser; portals that need an account use the jobfill window (`--ats generic`) so the login carries over | answers still come from `jobfill resolve` |
| LinkedIn / Indeed / Jobs by Workable listings | the agent follows the employer's apply link first | |

## Tailored CVs

Keep one CV per posting anywhere under `documents/resume/` (e.g.
`documents/resume/tailored cv/main_acme_labs_research_engineer_pre_training.pdf`). `apply start`
picks the one whose file name contains the company and best matches the role, and reports it under
`resume`; with no clear match (another company, or two CVs that fit equally) the application uploads
`documents.resume_path`. Set or change one by hand with `apply start ... --resume <file>` or
`app update <ID> --resume <file>`. The result record names the file the form actually holds.

Where the files go: [documents/README.md](../../documents/README.md).

## What the filler never does

The filler never solves CAPTCHAs, never accepts terms without a standing consent, and never submits:
`apply submit --confirm` runs only after the candidate says so (or they click Submit themselves).
`apply fill --dry-run` previews what would be filled without typing.

## You can work in the window too

Before every fill, set, next or submit, jobfill reads which page of the application is showing
(by the progress bar's current step, or by the page's questions):

- Answers you typed or changed are kept: jobfill only ever replaces values it typed itself, records
  yours as answered by you, and does not ask or write them again.
- You clicked Next yourself: jobfill continues on that page and does not click Next again.
- You clicked Back to check an earlier page: jobfill pauses (no typing, clicking or submitting) until
  you return to the furthest page or move on. `apply watch` tells the agent when that happens; it
  fills an earlier page only if you ask (`--here`).

## Accounts and login (Workday, company portals)

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

## The result record of each application

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

When a robot cannot fill an application, the agent says so with `python -m jobfill app giveup <ID> --why <reason>
--reason "..."`: the status becomes `manual` and the record says why, so you can see which ones are yours to finish.
