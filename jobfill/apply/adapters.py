"""Per-ATS knowledge: how to reach the form, where it lives, and its quirks.

Field reading and filling are generic (forms.py); an adapter only adds what differs between
systems. None of them ever clicks a final submit button on its own: `submit()` runs only from
`jobfill apply submit --confirm`, after the candidate said so.
"""

from __future__ import annotations

import re

from ..derive import choose_source
from ..text import closest_options, names_same, normalize, phrase_in
from .forms import OPTIONS_JS, NoExactChoice, close_combobox, sel

CONFIRMATION = re.compile(r"(thank you|thanks) for (applying|your application|your interest)|application (has been |was )?"
                          r"(received|submitted)|we('ve| have) received your application|successfully submitted", re.I)
SUBMIT_WORDS = re.compile(r"^(submit|send|apply|finish|complete)( (my |your )?application| now)?$", re.I)
NEXT_WORDS = re.compile(r"^(next|continue|save and continue|save & continue|next step|proceed|continue to next step)$", re.I)


class Adapter:
    name = "generic"
    ready_selector = "input, select, textarea"
    root_selector: str | None = None
    submit_selector = "button[type=submit], input[type=submit]"
    step_selector: str | None = "[aria-current=step]"   # the current step of a multi-page form's progress bar

    def open(self, page, url: str) -> None:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self.wait_ready(page)

    def frame(self, page):
        return page.main_frame

    def wait_ready(self, page, timeout: int = 15000) -> None:
        try:
            self.frame(page).wait_for_selector(self.ready_selector, timeout=timeout, state="attached")
        except Exception:
            pass
        page.wait_for_timeout(800)

    def blockers(self, page) -> list[str]:
        """Things only the candidate can do: log in, solve a CAPTCHA."""
        found = []
        fr = self.frame(page)
        if fr.locator("input[type=password]:visible").count():
            found.append("login_required")
        if page.locator("iframe[src*='hcaptcha'], iframe[src*='recaptcha'], iframe[title*='captcha' i]").count():
            visible = page.locator("iframe[src*='hcaptcha']:visible, iframe[src*='recaptcha/api2/bframe']:visible").count()
            if visible:
                found.append("captcha")
        # Cloudflare Turnstile (Workable): a check the candidate must pass when it shows itself.
        if "captcha" not in found and page.locator("iframe[src*='challenges.cloudflare.com']:visible").count():
            found.append("captcha")
        text = fr.locator("body").inner_text(timeout=5000)[:20000]
        if re.search(r"no longer (accepting applications|available|open)|position (has been )?(filled|closed)|"
                     r"job (is )?(closed|expired)", text, re.I):
            found.append("job_closed")
        return found

    def prepare(self, page, store, dry_run: bool = False) -> list[str]:
        """Steps before the form (email gates etc.). Returns blockers. A dry run never types."""
        return self.blockers(page)

    def after_fill(self, frame, field: dict, value) -> None:
        pass

    def adjust(self, fields: list[dict]) -> list[dict]:
        """Say what a field really asks when its label does not (Workable's "Address" is a location)."""
        return fields

    def custom_fill(self, frame, field: dict, value, store=None):
        """Take over a widget this system renders specially; None means use the generic filler. ``store`` is
        there for widgets that need another stored fact (the phone's country code)."""
        if field.get("kind") == "selectinput":   # Workday's prompt, also on a portal opened as generic
            return fill_selectinput(frame, field, value)
        return None

    def after_upload(self, frame) -> None:
        frame.wait_for_timeout(1000)

    def expand(self, frame, store, dry_run: bool = False) -> list[str]:
        """Open repeating blocks ("Add" under Work Experience) so every stored entry has one. Returns notes."""
        return []

    def entries(self, frame, store, dry_run: bool = False, notes: list | None = None):
        """For systems that edit one repeating entry at a time (Workable): open a block for each stored entry
        the page lacks and yield a selector for it; once the runner has filled it, save it. Notes go to ``notes``."""
        return iter(())

    def _buttons(self, frame):
        return frame.locator("button:visible, input[type=submit]:visible, input[type=button]:visible, "
                             "a[role=button]:visible")

    def _button_text(self, loc) -> str:
        try:
            return normalize(loc.inner_text(timeout=1000) or loc.get_attribute("value") or "")
        except Exception:
            return normalize(loc.get_attribute("value") or "")

    def next_button(self, frame):
        buttons = self._buttons(frame)
        for i in range(buttons.count()):
            b = buttons.nth(i)
            t = self._button_text(b)
            if NEXT_WORDS.match(t) and not SUBMIT_WORDS.match(t):
                return b
        return None

    def submit_button(self, frame):
        loc = frame.locator(self.submit_selector).filter(visible=True)
        if loc.count():
            return loc.last
        buttons = self._buttons(frame)
        for i in range(buttons.count()):
            if SUBMIT_WORDS.match(self._button_text(buttons.nth(i))):
                return buttons.nth(i)
        return None

    def errors(self, frame) -> list[str]:
        return frame.evaluate(r"""() => Array.from(document.querySelectorAll(
              "[role=alert], .error, .error-message, [class*='error' i]:not(input):not(form), [aria-invalid=true]"))
            .filter(e => e.getClientRects().length && e.textContent.trim())
            .map(e => e.textContent.replace(/\s+/g, ' ').trim()).filter(t => t.length < 200).slice(0, 15)""")

    def confirmed(self, page) -> bool:
        return bool(CONFIRMATION.search(self.frame(page).locator("body").inner_text(timeout=5000)[:20000]))


class Greenhouse(Adapter):
    name = "greenhouse"
    ready_selector = "#first_name, input[name='job_application[first_name]'], #application-form, #application_form"
    root_selector = "#application-form, #application_form, form[action*='applications'], form"
    submit_selector = "#submit_app, button[type=submit]:has-text('Submit'), input[type=submit][value*='Submit']"

    def frame(self, page):
        # Company career sites embed the form in an iframe.
        for fr in page.frames:
            if "greenhouse.io" in fr.url and ("embed/job_app" in fr.url or "/jobs/" in fr.url) and fr != page.main_frame:
                return fr
        return page.main_frame

    def open(self, page, url: str) -> None:
        super().open(page, url)
        fr = self.frame(page)
        if not fr.locator("#first_name, input[name='job_application[first_name]']").count():
            btn = fr.locator("a:has-text('Apply'), button:has-text('Apply')").first
            if btn.count():
                btn.click()
                self.wait_ready(page)


class Lever(Adapter):
    name = "lever"
    ready_selector = "#application-form, form[action*='/apply'], input[name='name']"
    root_selector = "#application-form, form[action*='/apply'], form"
    submit_selector = "#btn-submit, button[type=submit]"

    def after_upload(self, frame) -> None:
        # Lever parses the resume and prefills fields; wait so it cannot overwrite our values later.
        for _ in range(40):
            frame.wait_for_timeout(250)
            busy = frame.locator(".resume-upload-working:visible, .loading-label:visible").count()
            if not busy:
                break
        frame.wait_for_timeout(500)

    def custom_fill(self, frame, field: dict, value, store=None):
        if field.get("name") != "location":
            return None
        # Lever's location only counts when a suggestion is picked (it sets a hidden location id);
        # typed text alone is cleared on blur. Type key by key, then click the best suggestion.
        loc = frame.locator(f'[data-jobfill-id="{field["id"]}"]')
        loc.click()
        loc.fill("")
        loc.type(str(value), delay=40)
        options = frame.locator(".dropdown-location:visible")
        for _ in range(20):
            frame.wait_for_timeout(250)
            if options.count():
                break
        if not options.count():
            return False, f"no location suggestion for {value!r}"
        texts = [options.nth(i).inner_text().strip() for i in range(options.count())]
        city = normalize(str(value).split(",")[0])
        best = next((i for i, t in enumerate(texts) if normalize(t).startswith(city)), 0)
        options.nth(best).click()
        frame.wait_for_timeout(300)
        return True, ""


class Ashby(Adapter):
    name = "ashby"
    ready_selector = "#_systemfield_name, .ashby-application-form-field-entry"
    submit_selector = "button.ashby-application-form-submit-button, button:has-text('Submit Application')"

    def open(self, page, url: str) -> None:
        super().open(page, url)
        if not page.locator(".ashby-application-form-field-entry").count():
            # Overview page: switch to the "Application" tab.
            tab = page.locator("a:has-text('Application'), [role=tab]:has-text('Application')").first
            if tab.count():
                tab.click()
                self.wait_ready(page)

    def after_upload(self, frame) -> None:
        frame.wait_for_timeout(1500)


# ---- Workable (apply.workable.com/<company>/j/<code>/apply/): one page in three sections (Personal information,
# Profile, Details) and a privacy consent, all React widgets:
# - a cookie banner covers the page with a backdrop until it is answered: jobfill declines the optional cookies;
# - yes/no and choice questions are role=radio boxes around hidden inputs, the consent a role=checkbox box
#   (read and clicked through the box: extract.js, forms._click_choice);
# - a select is a read-only combobox opening a list, which Escape does not close (a second click does);
# - Phone is intl-tel-input: typing "+<code> <number>" sets the country, whatever the page guessed;
# - dates (MM/YYYY) are react-datepicker inputs that keep typed keys only, a value set at once is dropped;
# - Education / Experience take one entry at a time: "+ Add" opens an editor, "Update" saves it as a card;
# - "Address" is a location ("Include your city, region, and country"), prefilled from the visitor's IP;
# - the resume uploads as soon as it is chosen, then the drop zone shows its name; the "Autofill application"
#   box (import a resume to prefill) is never used;
# - a Cloudflare Turnstile check may show at submit: only the candidate passes it.

WORKABLE_LOCATION = "City, region and country"   # what Workable's "Address" asks, in the question bank's words
DATEPICKER = "xpath=ancestor::*[contains(@class, 'react-datepicker__input-container')][1]"
INTL_PHONE = "xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' iti ')][1]"


def _digits(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


BLUR_JS = "(e) => e.blur()"   # leave a field without focusing the next one (Tab would open the next calendar)


def _type_date(frame, loc, value) -> tuple[bool, str]:
    """A react-datepicker input: typed key by key (a value set at once is dropped), then Escape closes the
    calendar it opened, which would cover the next field."""
    want = str(value)
    loc.click(timeout=5000)
    loc.press("Control+A")
    loc.press("Backspace")
    loc.type(want, delay=40)
    loc.press("Escape")
    loc.evaluate(BLUR_JS)
    frame.wait_for_timeout(200)
    got = loc.input_value()
    if got == want:
        return True, ""
    if not got:   # seen live: an expected graduation (05/2027) is dropped, 11/2026 kept in November 2026
        return False, (f"typed {want!r}, but the date picker left it empty: Workable takes no date after the "
                       "current month, so the entry shows as ongoing")
    return False, f"typed {want!r}, but it shows {got!r}"


def _fill_phone(frame, loc, value, store) -> tuple[bool, str]:
    """intl-tel-input: the stored country code typed first ("+1 608-555-0142") picks the country, whichever one
    the page preselected from the visitor's location."""
    number = str(value).strip()
    code = _digits(store.get_fact("contact.phone_country_code")) if store is not None else ""
    if code and not number.startswith("+"):
        number = f"+{code} {number}"
    loc.click(timeout=5000)
    loc.fill("")
    loc.type(number, delay=20)
    loc.evaluate(BLUR_JS)
    frame.wait_for_timeout(200)
    if not loc.input_value().strip():
        return False, "the phone number did not take"
    shown = loc.locator(INTL_PHONE).locator(".iti__selected-dial-code")
    if code and shown.count() and _digits(shown.first.inner_text()) != code:
        return False, f"the phone's country code shows {shown.first.inner_text().strip()}, not +{code}"
    return True, ""


def _pick_listed(frame, field: dict, value) -> tuple[bool, str]:
    """Workable's select: a read-only combobox. Open it, click the option that is the answer, and check that the
    input shows it; a list left open covers the next field."""
    loc = frame.locator(sel(field["id"]))
    want = str(value[0] if isinstance(value, list) and value else value)
    if normalize(loc.input_value()) == normalize(want):
        return True, ""
    if loc.get_attribute("aria-expanded") != "true":
        loc.click(timeout=5000)
    texts: list[str] = []
    for _ in range(12):
        frame.wait_for_timeout(250)
        texts = frame.evaluate(OPTIONS_JS, field["id"])
        if texts:
            break
    i = next((k for k, t in enumerate(texts) if normalize(t) == normalize(want)), None)
    if i is None:
        i = next((k for k, t in enumerate(texts) if names_same(t, want)), None)
    if i is None:
        close_combobox(frame, loc)
        return False, f"no option {want!r} in its list" + (f" (it offers {texts[:12]})" if texts else "")
    frame.locator(f'[data-jobfill-opt="{i}"]').click(timeout=3000)
    frame.wait_for_timeout(200)
    if loc.get_attribute("aria-expanded") == "true":
        close_combobox(frame, loc)
    got = loc.input_value()
    return (True, "") if normalize(got) == normalize(texts[i]) else \
        (False, f"clicked {texts[i]!r}, but it shows {got!r}")


class Workable(Adapter):
    name = "workable"
    ready_selector = "form[data-ui=application-form]"
    root_selector = "form[data-ui=application-form]"
    submit_selector = "form[data-ui=application-form] button[data-ui=apply-button]"
    SECTIONS = ("education", "experience")

    def open(self, page, url: str) -> None:
        super().open(page, url)
        self._decline_cookies(page)
        if not page.locator(self.ready_selector).count():
            # The posting's overview: its Application tab (or Apply button) leads to the form.
            link = page.locator("a[data-ui=application-form-tab], a:has-text('Apply for this job'), "
                                "button:has-text('Apply for this job')").first
            if link.count():
                link.click()
                self.wait_ready(page)
                self._decline_cookies(page)

    @staticmethod
    def _decline_cookies(page) -> None:
        """The cookie banner's backdrop covers the form until it is answered: decline the optional cookies (the
        privacy-preserving answer; the candidate can change it under "Cookie settings")."""
        button = page.locator("[data-ui=cookie-consent-decline]:visible").first
        if button.count():
            try:
                button.click(timeout=3000)
                page.wait_for_timeout(300)
            except Exception:
                pass

    def prepare(self, page, store, dry_run: bool = False) -> list[str]:
        self._decline_cookies(page)   # a reloaded tab asks again
        return self.blockers(page)

    def blockers(self, page) -> list[str]:
        found = super().blockers(page)
        # A removed job's link lands on the company's job list with ?not_found=true.
        if "not_found=true" in page.url and "job_closed" not in found:
            found.append("job_closed")
        return found

    def adjust(self, fields: list[dict]) -> list[dict]:
        out = []
        for f in fields:
            if f.get("section") in self.SECTIONS and not f.get("entry"):
                continue   # an entry editor the candidate opened: theirs, left alone (see entries())
            if f.get("name") == "address" and f["kind"] == "input" and not f.get("section"):
                f["label"] = WORKABLE_LOCATION
            out.append(f)
        return out

    def custom_fill(self, frame, field: dict, value, store=None):
        loc = frame.locator(sel(field["id"]))
        if field["kind"] == "input" and loc.locator(DATEPICKER).count():
            return _type_date(frame, loc, value)
        if field["kind"] == "input" and field.get("type") == "tel" and loc.locator(INTL_PHONE).count():
            return _fill_phone(frame, loc, value, store)
        if field["kind"] == "combobox" and loc.get_attribute("readonly") is not None:
            return _pick_listed(frame, field, value)
        return super().custom_fill(frame, field, value, store)

    def entries(self, frame, store, dry_run: bool = False, notes: list | None = None):
        """Education / Experience: for each stored entry the page has no card for yet, "+ Add" opens an editor,
        the runner fills it, and "Update" saves it. Cards already there count (from an earlier run, or added by
        the candidate); none is ever edited or deleted. An editor left open is the candidate's: left alone."""
        notes = notes if notes is not None else []
        for kind in self.SECTIONS:
            box = frame.locator(f"{self.root_selector} [data-ui={kind}]")
            if not box.count():
                continue
            name = kind.capitalize()
            cards = lambda: box.locator("[data-ui=group]").count()
            want, have = len(store.records(kind)), cards()
            if want <= have:
                continue
            if box.locator("[data-ui=editor]").count():
                notes.append(f"{name}: an entry is open in its editor, left as it is (save or cancel it, then run "
                             "`apply fill` again)")
                continue
            if dry_run:
                notes.append(f"would add {want - have} {name} entr{'y' if want - have == 1 else 'ies'} for the "
                             "stored ones (Add, fill, Update)")
                continue
            for k in range(have, want):
                add = box.locator("button[data-ui=add-section]")
                if not add.count() or not add.is_enabled():
                    notes.append(f"{name}: its Add button is not available")
                    break
                add.click(timeout=5000)
                editor = box.locator("[data-ui=editor]").first
                try:
                    editor.wait_for(state="visible", timeout=5000)
                except Exception:
                    notes.append(f"{name}: clicking Add did not open an editor")
                    break
                editor.evaluate("(e, k) => e.setAttribute('data-jobfill-index', String(k))", k)
                yield f'{self.root_selector} [data-ui={kind}] [data-jobfill-index="{k}"]'
                frame.evaluate("() => document.activeElement && document.activeElement.blur()")   # close calendars
                editor.locator("[data-ui=save-section]").click(timeout=5000)
                for _ in range(25):
                    frame.wait_for_timeout(200)
                    if not box.locator("[data-ui=editor]").count():
                        break
                else:
                    said = [t.strip() for t in editor.locator("[id$='_error'], [role=alert]").all_inner_texts()
                            if t.strip()]
                    notes.append(f"{name} {k + 1}: not saved ({'; '.join(said) or 'the editor stayed open'}); "
                                 "left open for the candidate")
                    break
            notes.append(f"{name}: {cards() - have} entr{'y' if cards() - have == 1 else 'ies'} added, "
                         f"{cards()} for {want} stored")

    def after_upload(self, frame) -> None:
        # Uploaded as soon as it is chosen; the drop zone then shows the file's name.
        for _ in range(60):
            frame.wait_for_timeout(250)
            if frame.locator("[data-role=dropzone] [data-id=filename]").count():
                break
        frame.wait_for_timeout(300)

    def errors(self, frame) -> list[str]:
        own = frame.evaluate(r"""() => Array.from(document.querySelectorAll("[id$='_error']"))
            .filter(e => e.getClientRects().length && e.textContent.trim())
            .map(e => e.textContent.replace(/\s+/g, ' ').trim()).slice(0, 15)""")
        return list(dict.fromkeys(own + super().errors(frame)))


# ---- Workday prompt ("selectinput"), e.g. "How Did You Hear About Us?". It looks like a dropdown but is a
# search box with a pop-up menu, and treating it as a form field fails in several ways:
# - typing only searches: a choice counts once it is clicked, and then shows as a pill (the box stays empty);
# - the menu is added at the end of the page, not inside the field: entries are read page-wide;
# - menus nest (Job Board -> LinkedIn) and every click rebuilds the list: entries are re-read and re-tagged
#   right before each click, never held across one;
# - entries load late: they count once present, not loading, and the same on two reads in a row;
# - one text can be the category, the choice and the pill: pills are skipped and clicks go by tag;
# - the menu stays open after a pick and covers the next field: it is closed after every field;
# - every company words its choices its own way: when the search finds nothing, the whole menu is read and
#   matched like any option list ("LinkedIn Job Posting", "Social Media", "Job Board > Other").

PILLS_JS = r"""(id) => {
  const el = document.querySelector(`[data-jobfill-id="${id}"]`);
  const box = el.closest("[data-automation-id^='formField']") || el.closest("[data-automation-id='multiSelectContainer']") || el.parentElement;
  return Array.from(box.querySelectorAll("[data-automation-id='selectedItem']"))
    .map((p) => (p.getAttribute("title") || p.innerText || "").replace(/[×✕]/g, "").replace(/\s+/g, " ").trim()).filter(Boolean);
}"""

# The open menu: its visible entries (not the pills), tagged data-jobfill-opt=<n>; whether it is still loading
# or says it has nothing. ``branch`` marks a category that opens a sub-level (chevron / aria-haspopup).
MENU_JS = r"""() => {
  const shown = (e) => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== "hidden";
  const pill = "[data-automation-id='selectedItem'], [data-automation-id='selectedItemList']";
  let items = Array.from(document.querySelectorAll("[role=option], [data-automation-id=promptOption]"))
    .filter((o) => shown(o) && !o.closest(pill));
  items = items.filter((o) => !items.some((p) => p !== o && p.contains(o)));
  document.querySelectorAll("[data-jobfill-opt]").forEach((o) => o.removeAttribute("data-jobfill-opt"));
  const lists = Array.from(document.querySelectorAll("[role=listbox], [data-automation-id=activeListContainer]"))
    .filter((l) => shown(l) && !l.closest(pill));
  return {
    items: items.map((o, i) => {
      o.setAttribute("data-jobfill-opt", String(i));
      const branch = !!o.querySelector("[data-automation-id*='chevron' i], [data-automation-id*='arrow' i], [class*='chevron' i]")
        || o.getAttribute("aria-haspopup") === "true" || o.hasAttribute("aria-expanded");
      return {text: (o.getAttribute("data-automation-label") || o.innerText || "").replace(/\s+/g, " ").trim(), branch};
    }),
    busy: lists.some((l) => l.getAttribute("aria-busy") === "true"
      || Array.from(l.querySelectorAll("[data-automation-id*='loading' i], [role=progressbar]")).some(shown)),
    empty: !items.length && lists.some((l) => /no (items|results|matches)/i.test(l.innerText || "")),
    open: items.length > 0 || lists.length > 0,
  };
}"""

# The menu's own Back (up one level), tagged data-jobfill-back. Looked for only around the open menu: the
# page's Back button would leave the page.
BACK_JS = r"""() => {
  const shown = (e) => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== "hidden";
  document.querySelectorAll("[data-jobfill-back]").forEach((b) => b.removeAttribute("data-jobfill-back"));
  const anchor = Array.from(document.querySelectorAll("[data-jobfill-opt], [role=listbox], [data-automation-id=activeListContainer]")).find(shown);
  if (!anchor) return false;
  let box = anchor;
  for (let up = 0; up < 3 && box.parentElement && box.parentElement !== document.body; up++) box = box.parentElement;
  const back = Array.from(box.querySelectorAll("[data-automation-id*='back' i], [aria-label*='back' i], [role=button], button"))
    .find((b) => {
      const id = b.getAttribute("data-automation-id") || "";
      return shown(b) && !b.querySelector("[role=option]") && !/navigation|footer|page/i.test(id)
        && (/back/i.test(id) || /^\s*(go )?back\b/i.test(b.getAttribute("aria-label") || b.innerText || ""));
    });
  if (back) back.setAttribute("data-jobfill-back", "1");
  return !!back;
}"""

REMOVE_PILL_JS = r"""([id, text]) => {
  const el = document.querySelector(`[data-jobfill-id="${id}"]`);
  const box = el.closest("[data-automation-id^='formField']") || el.closest("[data-automation-id='multiSelectContainer']") || el.parentElement;
  const norm = (s) => (s || "").replace(/[×✕]/g, "").replace(/\s+/g, " ").trim().toLowerCase();
  const pill = Array.from(box.querySelectorAll("[data-automation-id='selectedItem']"))
    .find((p) => norm(p.getAttribute("title") || p.innerText) === norm(text));
  const x = pill && pill.querySelector("[data-automation-id*='delete' i], [data-automation-id*='remove' i], [aria-label*='remove' i], [aria-label*='delete' i], [role=button], button");
  if (x) x.click();
  return !!x;
}"""

# A click on the page outside the menu, which is what closes Workday's pop-ups.
OUTSIDE_CLICK_JS = r"""() => ["mousedown", "mouseup", "click"].forEach((t) =>
  document.body.dispatchEvent(new MouseEvent(t, {bubbles: true, cancelable: true})))"""

HOW_HEARD = ("how_heard", "how_heard_detail")


def _same(a: str, b: str) -> bool:
    return normalize(a) == normalize(b)


def _pick(items: list[dict], want: str, question_id: str | None = None) -> int | None:
    """The menu entry that is ``want``: the same text first, then a qualified form of it (``names_same``). For "How
    did you hear", an entry naming the source counts too ("LinkedIn Job Posting")."""
    texts = [i["text"] for i in items]
    for test in (lambda t: _same(t, want), lambda t: names_same(t, want),
                 lambda t: question_id in HOW_HEARD and bool(normalize(t))
                 and (phrase_in(normalize(want), normalize(t)) or phrase_in(normalize(t), normalize(want)))):
        hit = next((k for k, t in enumerate(texts) if test(t)), None)
        if hit is not None:
            return hit
    return None


def _choose(want: str, entries: list[tuple[str, str]], question_id: str | None) -> tuple[str, str] | None:
    """The (category, choice) meaning ``want`` among every entry of the menu."""
    labels = {(f"{group}: {text}" if group else text): (group, text) for group, text in entries}
    if question_id in HOW_HEARD:   # by category: "Job Board: LinkedIn" first, else "Social Media", "Job Board: Other"
        hit = choose_source(want, list(labels), detail=question_id == "how_heard_detail")
    else:
        k = _pick([{"text": t} for _, t in entries], want)
        hit = list(labels)[k] if k is not None else None
    return labels.get(hit) if hit else None


def _read(frame) -> dict:
    return frame.evaluate(MENU_JS)


def _menu(frame, before: list[dict] | None = None, wait_ms: int = 4000) -> list[dict]:
    """Menu entries once they have loaded, or changed from ``before`` (after a search or a drill-down): present
    (or "No Items"), not loading, and the same on two reads in a row. Else what shows when the time is up."""
    last = None
    for _ in range(max(2, wait_ms // 200)):
        frame.wait_for_timeout(200)
        m = _read(frame)
        now = None if m["busy"] or not (m["items"] or m["empty"]) else m["items"]
        if now is not None and now != before and now == last:
            return now
        last = now
    return _read(frame)["items"]


def _click_entry(frame, text: str) -> bool:
    """Click the entry reading ``text``. The menu is read again first and after a miss: Workday rebuilds it as
    it loads, so an element found a moment ago may be detached by the time it is clicked."""
    for _ in range(3):
        items = _read(frame)["items"]
        k = next((n for n, i in enumerate(items) if i["text"] == text), None)
        if k is not None:
            try:
                frame.locator(f'[data-jobfill-opt="{k}"]').click(timeout=2000)
                return True
            except Exception:
                pass
        frame.wait_for_timeout(300)
    return False


def _to_top(frame, items: list[dict]) -> list[dict]:
    """Workday reopens the menu at the level last shown: step back up to the top level."""
    for _ in range(3):
        if not frame.evaluate(BACK_JS):
            break
        try:
            frame.locator("[data-jobfill-back]").first.click(timeout=2000)
        except Exception:
            break
        items = _menu(frame, before=items, wait_ms=1500)
    return items


def _open_top(frame, loc) -> list[dict]:
    """Open the menu afresh at its top level: no search text, no category open."""
    loc.press("Escape")
    loc.fill("")
    loc.click()
    return _to_top(frame, _menu(frame))


def _at_top(frame, loc, top: list[dict]) -> list[dict]:
    now = _read(frame)["items"]
    if now != top:
        now = _to_top(frame, now)
    return now if now == top else _open_top(frame, loc)


def _close_menu(frame, loc) -> None:
    """Close the pop-up: Workday leaves it open after a pick, and it then covers the next field."""
    for step in (lambda: loc.press("Escape"), lambda: frame.evaluate(OUTSIDE_CLICK_JS), lambda: loc.press("Tab")):
        try:
            if not _read(frame)["open"]:
                return
            step()
        except Exception:
            return
        frame.wait_for_timeout(300)


def _search(frame, loc, want: str, picked, question_id: str | None = None, seen: list | None = None) -> bool:
    """Type ``want`` and press Enter (Workday then searches every level), and click the result meaning it.
    ``seen`` collects what the search offered."""
    loc.press("Escape")
    loc.fill("")
    loc.click()
    shown = _menu(frame, wait_ms=1500)   # what the click opened, to tell the results apart from it
    loc.type(want, delay=20)
    loc.press("Enter")
    found = _menu(frame, before=shown)
    if seen is not None:
        seen.extend(i["text"] for i in found if i["text"] not in seen)
    k = _pick(found, want, question_id)
    return k is not None and _click_entry(frame, found[k]["text"]) and picked(found[k]["text"])


def _browse(frame, loc, field_id: str, want: str, start: list[str], pills,
            question_id: str | None = None) -> list[tuple[str, str]]:
    """Every choice of the menu as (category, choice), opening each category (Job Boards -> LinkedIn). In a
    menu that does not mark its categories an entry is opened to find out; if it was a plain choice, that
    selected it, and it is removed again. Not needed when ``want`` is right there at the top level."""
    top = _open_top(frame, loc)
    marked = any(i["branch"] for i in top)
    if not marked and _pick(top, want, question_id) is not None:
        return [("", i["text"]) for i in top]
    entries: list[tuple[str, str]] = []
    for entry in top:
        if marked and not entry["branch"]:
            entries.append(("", entry["text"]))
            continue
        now = _at_top(frame, loc, top)
        if not _click_entry(frame, entry["text"]):
            continue
        frame.wait_for_timeout(500)
        added = [p for p in pills() if p not in start]
        if added:
            for p in added:
                frame.evaluate(REMOVE_PILL_JS, [field_id, p])
                frame.wait_for_timeout(300)
            entries.append(("", entry["text"]))
            continue
        sub = _menu(frame, before=now, wait_ms=2000)
        if sub != now:
            entries += [(entry["text"], i["text"]) for i in sub if i["text"] != entry["text"]]
    return entries


def _pick_path(frame, loc, path: tuple[str, str], picked) -> bool:
    group, text = path
    now = _open_top(frame, loc)
    if group:
        if not _click_entry(frame, group):
            return False
        _menu(frame, before=now, wait_ms=2000)
    return _click_entry(frame, text) and picked(text)


def fill_selectinput(frame, field: dict, value) -> tuple[bool, str]:
    loc = frame.locator(f'[data-jobfill-id="{field["id"]}"]')
    pills = lambda: frame.evaluate(PILLS_JS, field["id"])
    qid = field.get("question_id")
    values = value if isinstance(value, list) else [value]
    lacking: list[str] = []   # entries of a list (skills) the menu has no exact choice for
    try:
        for want in values:
            want = str(want)
            if any(_same(p, want) for p in pills()):
                continue   # already chosen
            start = pills()

            def picked(text):   # a new pill for ``text`` ("LinkedIn", or "Job Boards LinkedIn")
                frame.wait_for_timeout(500)
                t = normalize(text)
                return any(normalize(p) == t or normalize(p).endswith(" " + t) for p in pills() if p not in start)

            # 1. Search, then 2. read the whole menu and pick what means the same. A list (skills) is only
            # searched: its menu is a catalogue of thousands, and a look-alike entry is a wrong answer.
            if _search(frame, loc, want, picked, qid):
                continue
            if len(values) > 1:
                lacking.append(want)
                continue
            if qid not in HOW_HEARD:
                # One name (a school, a field of study) in a catalogue: what the candidate picked for it before,
                # else no look-alike but a question for them, with what the menu offers.
                if any(_search(frame, loc, alt, picked) for alt in field.get("alternatives") or []):
                    continue
                offered: list[str] = []
                _search(frame, loc, want, lambda t: False, seen=offered)
                if not offered and (word := max(re.findall(r"[^\W\d_]{4,}", want), key=len, default="")):
                    _search(frame, loc, word, lambda t: False, seen=offered)   # nothing for the whole name
                raise NoExactChoice(want, closest_options(want, offered))
            entries = _browse(frame, loc, field["id"], want, start, pills, qid)
            path = _choose(want, entries, qid)
            if path is None:
                offers = [f"{group} > {text}" if group else text for group, text in entries]
                return False, f"no menu choice matches {want!r}; it offers: {offers[:40]}"
            if any(_same(p, path[1]) for p in pills()):
                continue   # what it stands for is already chosen
            if not _pick_path(frame, loc, path, picked):
                return False, f"could not pick {' > '.join(p for p in path if p)!r} in the menu"
        if lacking and len(lacking) == len(values):
            return False, f"the menu has no exact entry for any of {lacking}"
        return True, f"left out (no exact entry in the menu): {', '.join(lacking)}" if lacking else ""
    finally:
        _close_menu(frame, loc)


class Workday(Adapter):
    """Workday: each company is its own tenant with its own account. Apply -> Apply Manually ->
    Sign in with email, then a multi-page form ("Save and Continue")."""
    name = "workday"
    ready_selector = ("[data-automation-id=signInContent], [data-automation-id=applyFlowPage], "
                      "[data-automation-id=adventureButton], input")
    submit_selector = "[data-automation-id=bottom-navigation-next-button]:has-text('Submit')"
    step_selector = "[data-automation-id=progressBarActiveStep], [aria-current=step]"

    def open(self, page, url: str) -> None:
        super().open(page, url)
        # Each step's button renders only after the previous click has loaded; a step that never shows up
        # (already signed in, or a tenant without that choice) is skipped.
        for step in ("adventureButton", "applyManually", "SignInWithEmailButton"):
            button = page.locator(f"[data-automation-id={step}]:visible").first
            try:
                button.wait_for(state="visible", timeout=10000)
            except Exception:
                continue
            button.click()
        page.wait_for_timeout(1500)
        self.wait_ready(page)

    # Repeating sections and the stored entries that fill them, one block ("Work Experience 1") per entry.
    SECTIONS = {"Work-Experience-section": "experience", "Education-section": "education"}

    def expand(self, frame, store, dry_run: bool = False) -> list[str]:
        """Click "Add" / "Add Another" until each section has a block per stored entry. Blocks already there
        (blank or not) count; none is ever deleted."""
        notes = []
        for section, kind in self.SECTIONS.items():
            group = frame.locator(f'[role=group][aria-labelledby="{section}"]')
            if not group.count():
                continue
            name = section.removesuffix("-section").replace("-", " ")
            blocks = lambda: group.locator("h5[id$='-panel'], h4[id$='-panel']").count()
            want, have = len(store.records(kind)), blocks()
            if want <= have:
                continue
            if dry_run:
                notes.append(f"would add {want - have} {name} block(s) for the stored entries")
                continue
            for _ in range(want - have):
                before = blocks()
                group.locator("[data-automation-id=add-button]").last.click(timeout=5000)
                for _ in range(25):   # the new block renders a moment after the click
                    frame.wait_for_timeout(200)
                    if blocks() > before:
                        break
                else:
                    notes.append(f"{name}: clicking Add did not open a new block")
                    break
            notes.append(f"{name}: {blocks() - have} block(s) added, {blocks()} for {want} stored entries")
        return notes

    def after_upload(self, frame) -> None:
        # The file shows in the list beside the drop zone once Workday has taken it (the input is cleared).
        for _ in range(60):
            frame.wait_for_timeout(250)
            if frame.locator("[data-automation-id*='file-upload-item' i], [data-automation-id*='successful' i]").count():
                break
            text = frame.locator("[data-automation-id$='FileUpload']").first
            if text.count() and re.search(r"\.(pdf|docx?|rtf|txt|odt)\b", text.inner_text(), re.I):
                break
        frame.wait_for_timeout(500)

    def next_button(self, frame):
        nxt = frame.locator("[data-automation-id=bottom-navigation-next-button]:visible").first
        if nxt.count() and not SUBMIT_WORDS.match(self._button_text(nxt)):
            return nxt
        return super().next_button(frame)


class ICIMS(Adapter):
    name = "icims"
    ready_selector = "input, select, textarea, button"
    submit_selector = "input[type=submit][value*='Submit' i], button:has-text('Submit')"

    def frame(self, page):
        for fr in page.frames:
            if "in_iframe=1" in fr.url or (fr.name == "icims_content_iframe"):
                return fr
        return page.main_frame

    def open(self, page, url: str) -> None:
        super().open(page, url)
        fr = self.frame(page)
        # Job page instead of the login step: follow its "Apply" link.
        if not fr.locator("input:visible").count():
            link = fr.locator("a:has-text('Apply for this job'), a:has-text('Apply'), button:has-text('Apply')").first
            if link.count():
                link.click()
                self.wait_ready(page)

    def prepare(self, page, store, dry_run: bool = False) -> list[str]:
        fr = self.frame(page)
        email = fr.locator("input#email:visible, input[type=email]:visible").first
        others = fr.locator("input:visible:not([type=email]):not([type=checkbox]):not([type=submit]):not([type=hidden])")
        if email.count() and others.count() <= 1 and not fr.locator("input[type=password]:visible").count():
            if dry_run:
                return self.blockers(page) + ["email_gate"]
            # Email gate: iCIMS asks for the email first, then shows the profile form or a login.
            address = store.get_fact("contact.email")
            terms = fr.locator("input[type=checkbox]:visible").first
            if terms.count() and not terms.is_checked():
                # Usually "I accept the privacy/GDPR terms": only with the candidate's standing consent.
                if store.get_fact("consent.privacy_ack") is not True:
                    return self.blockers(page) + ["consent_required"]
                terms.check(force=True)
            if address:
                email.fill(address)
                btn = self.next_button(fr) or fr.locator("#enterEmailSubmitButton, button[type=submit], input[type=submit]").first
                if btn and btn.count():
                    btn.click()
                    page.wait_for_load_state("domcontentloaded")
                    self.wait_ready(page)
        return self.blockers(page)


ADAPTERS = {"greenhouse": Greenhouse(), "lever": Lever(), "icims": ICIMS(), "ashby": Ashby(), "workday": Workday(),
            "workable": Workable(), "generic": Adapter()}


def get(name: str | None) -> Adapter:
    return ADAPTERS.get(name or "generic", ADAPTERS["generic"])
