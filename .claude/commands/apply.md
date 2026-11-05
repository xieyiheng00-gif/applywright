# /apply <job URL> - Fill a job application

`$ARGUMENTS` is the job posting URL. Plain Python decides the application system and fills every
field the database can answer; you spend tokens only on what is left. Never submit without the
candidate's explicit go-ahead in chat.

If `$ARGUMENTS` is empty, ask for the job URL and stop.

## Step 1: Detect the system (no AI, no browser)

`python -m jobfill apply start "<url>"` (add `--source "<where they found it>"` and
`--referrer "<name>"` if the candidate mentioned them). Act on the result:

| Result | Do |
|---|---|
| exit code 3, `"error": "Not a job posting: ..."` | Reply with that error in one line and stop. |
| `handler: "unsupported"` | Reply that jobfill does not support this system yet and give the URL to apply manually. Stop. |
| `handler: "follow"` (LinkedIn, Indeed, ...) | Open the URL in the built-in browser, find the employer's own apply link (not Easy Apply), and run `apply start` again with that link. If there is none (Easy Apply only), say so and stop. |
| `handler: "agent_check"` | The HTML alone could not tell. Open it in the built-in browser and read the text (`get_page_text`). Not a job posting: reply with the error and stop. A job: look at where "Apply" leads or which iframe holds the form; rerun `apply start` with that apply URL, or with `--ats custom --company "<Company>" --role "<Role>"` for a company portal. |
| `handler: "python"` (Greenhouse, Lever, iCIMS, Ashby, Workday, Workable) | Step 2. |
| `handler: "ai"` (Amazon, TikTok, Google, other portals and ATSs) | Step 3, unless the portal needs an account: then rerun `apply start "<url>" --ats generic` so it is filled in the jobfill window, where `jobfill login` signs in, and go to Step 2. |

The output includes `app_id`, company, role, and earlier applications to the same company
(mention those). The posting text is saved for tier-3 answers.

`resume` says which CV this application uploads: a tailored CV from `documents/resume/` whose file name
names the company and matches the role, a `--resume <file>` the candidate gave, or the default
(`documents.resume_path`). Tell the candidate which file it is. If it lists `cvs_for_company` (CVs for this
company, none clearly matching the role), ask whether one of them fits and set it with
`python -m jobfill app update <ID> --resume "<file>"` before filling. To swap the file after a fill, set
it the same way and replace the upload with `apply set --field <resume field> --value "<file>"`.

## Step 2: Python filler (Greenhouse, Lever, iCIMS, Ashby, Workday, Workable)

1. `python -m jobfill apply fill --app <ID>` opens a visible Chromium window (it stays open between
   commands), fills the form from the database, re-reads it for follow-up questions, and prints what
   is left:
   - `blockers: ["login_required"]` - the site needs an account (Workday always; iCIMS and some portals).
     Ask the candidate to run, in their own terminal: `python -m jobfill login --app <ID>`. It creates
     or signs in to the account in the jobfill window with their login email and the per-company
     password, and reads the verification email if the mail connector is set up. **Never run `login`
     or `account password --show` yourself** (they refuse without a terminal), and never type a
     password or a verification code. When they say it worked, rerun `apply fill`.
     If login reports `needs_verification`, `captcha` or `consent_required`, the candidate finishes that
     step in the window and runs login again.
   - `blockers: ["consent_required"]` - the page wants terms/privacy accepted before continuing and the
     candidate has no standing `consent.privacy_ack`. Ask them; if they agree, they tick it in the window
     (or set the fact for the future), then rerun `apply fill`.
   - `blockers: ["job_closed"]` - tell the candidate the posting is closed; stop.
   - `uncertain` / `unmatched` - classify with the `/learn` rules (no questions to the candidate).
     Exception: agreements, policies and consents (arbitration, AI-use policies, AI notetakers or
     recordings, terms, background-check authorizations) are the candidate's decision. Put them in the
     batched question message with the exact wording; only if they want a standing answer, define a
     `consent.*` fact and learn the question onto it.
   - `needs_user` - collect them (plus prompts of facts you just defined) into ONE numbered message;
     store the answers (`fact set`, `record add`). An item with `instructions` carries the form's own
     words under the field ("If it doesn't apply to you, please enter 'None'"): quote them in the
     question and follow them; they outrank a suggestion from the profile.
     An item with `set_with` is a list (a school, a field of study, ...) that has no option exactly
     matching the stored answer, also after jobfill searched the list for the name and its key words:
     jobfill left it empty instead of picking a look-alike. Do not choose for
     the candidate. In the same numbered message, show their stored answer and the `choices` (the closest
     options) and say they can either pick it in the window themselves, or tell you. If they pick it in
     the window, run `apply fill` once to record it. If they tell you, enter their exact choice with the
     `set_with` command. Either way jobfill remembers that pick for that answer and uses it on later
     forms that offer it (`python -m jobfill choice list` shows them, `choice forget` drops one).
   - `needs_ai` - write each answer from its task, its playbook and the shared `ai_context` exactly as
     in `/fill` step 4 (with the `job-answer-writer` skill, inside jobfill's rules), save it to `data/answers/app<ID>_<question_id>.txt`, run `answer check` and fix
     what it reports, then record it:
     `python -m jobfill answer record --app <ID> --question <question_id> --label "<the question's label on the form>" --file data/answers/app<ID>_<question_id>.txt`
     A task with `examples` (why us, why this role, product / mission, why you are leaving) is not
     recorded straight away: write that many example answers from different `ai_context.relevant.angles`
     (the candidate's real experience matched to the posting), put them in the batched numbered message
     for the candidate to pick, edit or replace, then record the chosen one (`/fill` step 4). If the
     posting was not saved (`relevant.note`), save it first so the examples can tie to it.
     A task with `question_id: open_ended_custom` is an open question the bank does not know yet; it is
     written like any other (optional or not: a blank essay on an application reads as no interest), and
     you also classify its label with the `/learn` rules (`python -m jobfill pending` has its id) so plain
     Python recognizes it next time.
   - `fill_failed` - failed once, often a dropdown that loaded slowly. The result says `rerun: true`: run
     `apply fill` once more; it retries these fields.
   - `handoff` - failed twice with the same value, so plain Python stops trying it (retrying a
     deterministic failure only wastes runs). You decide, using `value`, `options` and `why`:
     - the value is right but phrased differently from the options: pick the matching option and enter
       it with `python -m jobfill apply set --app <ID> --field <field> --value "<option>"` (`--json` for
       lists);
     - you cannot tell which option is right, or the value itself is in doubt: ask the candidate in the
       batched question message;
     - the widget is one jobfill cannot operate (`apply set` fails too): ask the candidate to set it in
       the window.
     A field whose value changes (a corrected fact, a new answer) gets two fresh tries.
2. **One fill per page.** Run `apply fill` again on the same page only when the result says `rerun: true`,
   after a blocker is cleared, after the candidate worked in the window, or after a corrected fact
   (`--overwrite` re-types only values jobfill typed itself). Never rerun just because fields are still
   empty or answers are waiting for the candidate. Type each answer straight into its own field instead:
   a recorded written answer the candidate approved with its `set_with` command (`apply set --field <field>
   --file data/answers/app<ID>_<question_id>.txt`), and a `needs_user` answer, once stored, with
   `apply set --field <field> --value "<answer>"`.
3. Multi-page forms (iCIMS, Workday): when a page is done, `python -m jobfill apply next --app <ID>`
   clicks Next / Save and Continue (it never clicks a final submit), then `apply fill` again for the new
   page. Workday's search-and-pick menus ("How Did You Hear About Us?", also two-level ones such as
   Job Boards > LinkedIn) are picked by jobfill, also in the company's own wording ("LinkedIn Job
   Posting", or "Social Media" / "Job Board > Other" when LinkedIn itself is not offered); if nothing
   fits the field comes back as a `handoff` whose `why` lists the menu's real choices. Workday's
   "Select One" buttons (How Did You Hear About Us?, Country, State, Phone Device Type on many tenants)
   are read as dropdowns: jobfill opens each list for its options and picks like a normal dropdown.
   On "My Experience", jobfill clicks Add until Work Experience and Education have one block per stored
   entry (blocks already open count; it never deletes one), types the Month / Year boxes, picks only exact
   skill entries (skills the menu lacks come back under `partly`), and never uploads a file into a field
   that already lists one. Languages / Websites blocks are left to the candidate. Anything else left blank on a Workday page
   goes to the candidate to pick in the window. Every result carries `page` (`n` of `of`, and the
   step `title` when the site shows one).
   **The candidate shares the window.** jobfill checks which page is showing before every `fill`,
   `set`, `next` and `submit`, and follows them:
   - They answered or changed questions themselves: those values are never overwritten (not even with
     `--overwrite`), are recorded as their answers, and are listed in `by_candidate`. Do not ask them
     again or write an AI answer for those questions.
   - They clicked Next themselves (`moved_by_candidate: true`): carry on from that page. `apply next`
     does not click again (`clicked: null`); run `apply fill` for the page they are on.
   - They clicked Back (`paused: "candidate_went_back"`): they are checking or changing an earlier page.
     Stop: no fill, set, next or submit, and do not ask them to hurry. Say in one line that you will
     wait, then run `python -m jobfill apply watch --app <ID>` (in the background if your runtime can;
     it waits up to 10 minutes, `--timeout` to change) and act on its `event`: `returned` or `moved_on`
     → `apply fill`; `went_back` → keep waiting; `submitted` → they submitted it themselves (already
     recorded); `closed` → ask whether to stop; `timeout` → watch again or check in. Fill the earlier
     page only if they ask you to: `apply fill --here`.
   - Whenever you hand the window to the candidate (questions in the window, a review), `apply watch`
     is how you notice what they did next.
   Workable is one page: jobfill declines the optional cookies (the banner blocks the form until it is
   answered), adds Education / Experience one entry at a time (+ Add, fill, Update; cards already there
   count, and an entry editor the candidate opened is theirs: left alone, see `added`), and answers
   "Address" as city, region and country. Its date pickers take no date after the current month: an
   expected graduation comes back under `fill_failed` with that reason and the entry shows as ongoing;
   tell the candidate, do not retry it. It never uses Workable's "Autofill application" box. A
   Cloudflare check at submit is a `captcha` blocker for the candidate. For US jobs Workable may show an
   optional EEO survey right after the application went in (step 5).
4. Review, in ONE message: every written answer (numbered, with character/word count), any
   `handoff` field still unresolved, and a reminder that the window shows the whole filled form.
   Ask them to check the window, then to say "submit" or list changes.
5. Only after an explicit "submit" (or equivalent) from the candidate:
   `python -m jobfill apply submit --app <ID> --confirm`. A CAPTCHA (`blockers: ["captcha"]`) is for
   the candidate to solve in the window; then run submit again. When `submitted` is true the
   application status becomes `submitted`. If they prefer, they click Submit themselves; then run
   `python -m jobfill app update <ID> --status submitted`.
   A result with `survey: "eeo"` (Workable, US jobs) means the application is in and the site now shows its
   optional EEO survey: race or ethnicity and gender, plus veteran and disability status for federal
   contractors. Run `apply fill` once more: it answers from the stored `eeo.*` facts ("decline" is a valid
   answer; the survey's own preselection counts as no answer) and lists under `needs_user` what the survey
   does not offer (it has Male / Female only): ask the candidate, never infer. Show them the answers; the
   survey goes in only after they say so, with `apply submit --app <ID> --confirm` again
   (`survey_submitted: true`). Skipping is their call in the window (Workable then emails a link); never
   click Skip for them. The reminder email's link (`/<company>/candidates/<id>/eeoc_profile/new`) is not a
   job: `apply start` says so.

## Step 3: Agent fills in the built-in browser (company portals, other systems)

Plain Python still answers the questions; you drive the page.

1. `navigate` to the URL in the built-in browser (`mcp__Claude_Browser__*`). Click Apply. Sign-in or
   account creation pages are for the candidate: ask them to do it in the browser pane, then continue.
2. For each form page:
   - Read only the controls: `read_page` with `filter: "interactive"`. Avoid screenshots unless the
     page is unreadable.
   - Write `data/forms/app<ID>_p<N>.json` in the field JSON format from `README.md` (label, type,
     options, required, `section`/`index` for repeated education/experience blocks, max length).
     Use the field's `ref` as its `id`.
   - `python -m jobfill resolve data/forms/app<ID>_p<N>.json --app <ID> --compact`, then handle
     statuses exactly as in Step 2 (classify, one batched question message, written answers recorded
     with `answer record`), and resolve again.
   - Enter every `filled` value with `form_input` (by ref) or `computer` clicks for custom dropdowns.
   - File uploads: if the browser cannot attach the file, ask the candidate to attach the resume
     (path: `python -m jobfill fact get documents.resume_path`) in the browser pane.
   - Click Next / Continue to move on. Never click the final submit button on your own.
   - The candidate may use the browser pane too. Read the page before each action: never change a
     value they entered; if they moved on, continue on their page; if they went back to an earlier
     page, stop and wait until they return or tell you to go on.
3. Review and submit as in Step 2.4-2.5: one review message, explicit go-ahead, then click Submit (or
   the candidate does), then `python -m jobfill app update <ID> --status submitted`.

## Step 4: The result record

Every question on the form is saved for the application as it is handled: the question, the
answer, who answered it (the profile via plain Python, the AI, or the candidate), and whether
jobfill typed it in. `apply fill`, `apply set`, `answer record`, `apply submit` and
`app update --status` keep `data/applications/app<ID>_<company>_<role>.md` (and `.json`) current.

When the application is done (submitted, or abandoned with `app update <ID> --status withdrawn`):
- End with one line linking that Markdown file and its result line, e.g. "31 of 36 answered
  questions came from your profile with no AI (86%); 3 written by AI; 2 by you."
- `python -m jobfill app report <ID>` rewrites it on demand (`--print` shows it);
  `python -m jobfill app stats` shows the totals across all applications.

## Rules

- Answers come from `jobfill resolve` / `apply fill`; never type facts from memory or from the
  documents directly. Missing facts are asked once and stored.
- Never type passwords, never solve CAPTCHAs, never accept terms the candidate has not agreed to
  (`consent.*` facts record their standing choices).
- When finished: `python -m jobfill apply close` closes the jobfill browser window (it otherwise
  stays open for the next application, keeping any logins).
