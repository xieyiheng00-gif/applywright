"""Workday's prompt widget ("How Did You Hear About Us?"): pick a menu choice, read the pill."""

import importlib.util
import unittest
from pathlib import Path

from jobfill.apply import adapters
from jobfill.derive import choose_source
from jobfill.apply.runner import fill_frame, summarize

from .helpers import fresh_store, temp_dir

PAGE = (Path(__file__).resolve().parent / "fixtures" / "workday_like.html").as_uri()


class TestSourceChoice(unittest.TestCase):
    """How a source is matched to a company's own choices ("Group: Option" for two-level menus)."""

    def test_named_choice_under_its_most_likely_category(self):
        self.assertEqual(choose_source("LinkedIn", ["Social Media: LinkedIn", "Job Board: LinkedIn"]), "Job Board: LinkedIn")
        self.assertEqual(choose_source("LinkedIn", ["Career Site", "LinkedIn Job Posting"]), "LinkedIn Job Posting")

    def test_category_when_the_source_is_not_offered(self):
        self.assertEqual(choose_source("LinkedIn", ["Company Website", "Social Media", "Other"]), "Social Media")
        self.assertEqual(choose_source("LinkedIn", ["Job Board: Indeed", "Job Board: Other", "Other"]), "Job Board: Other")

    def test_another_named_site_is_not_a_category_answer(self):
        # "Job Board: Indeed" is a job board, but not where the candidate found the job: ask instead.
        self.assertIsNone(choose_source("LinkedIn", ["Job Board: Indeed", "Job Board: Glassdoor", "Other"]))


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Playwright not installed")
class TestWorkdayPrompt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.store = fresh_store(with_profile=True, owner=self)   # how-heard default: LinkedIn
        self.app = self.store.start_application("Visa", "Engineer", ats="workday")
        self.workday = adapters.get("workday")

    def open(self, flags=""):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.goto(PAGE + flags)
        return page

    def fill(self, page, overwrite=False):
        report = fill_frame(self.workday.frame(page), self.store, self.app, self.workday, overwrite=overwrite)
        self.assertFalse(page.evaluate("() => window.pageBack"))         # only the menu's own Back, never the page's
        return report, summarize(report, self.app)

    def pills(self, page):
        return page.evaluate("() => Array.from(document.querySelectorAll('[data-automation-id=selectedItem]')).map(p => p.title)")

    def source_entry(self, report):
        return next(v for v in report["fields"].values() if v["label"].startswith("How Did You Hear"))

    def test_read_as_a_menu_not_a_text_box(self):
        page = self.open()
        report, _ = self.fill(page, overwrite=True)
        entry = self.source_entry(report)
        self.assertEqual((entry["kind"], entry["question_id"], entry["fill"]), ("selectinput", "how_heard", "ok"))
        self.assertEqual(self.pills(page), ["LinkedIn"])
        self.assertEqual(page.input_value("#source--source"), "")       # the box stays empty: only the pill counts

    def test_linkedin_found_through_job_boards_when_search_finds_nothing(self):
        page = self.open("#nosearch")
        report, summary = self.fill(page, overwrite=True)
        self.assertEqual(self.source_entry(report)["fill"], "ok")
        self.assertEqual(self.pills(page), ["LinkedIn"])
        self.assertNotIn("fill_failed", summary)

    def test_menu_without_category_marks_undoes_wrong_picks(self):
        page = self.open("#nosearch&nomarks")    # "Career Site" etc. look like categories but are choices
        self.fill(page, overwrite=True)
        self.assertEqual(self.pills(page), ["LinkedIn"])

    def test_menu_is_closed_after_the_pick(self):
        page = self.open("#stayopen")              # left open, the menu would cover "Family Name"
        report, _ = self.fill(page, overwrite=True)
        self.assertEqual(self.pills(page), ["LinkedIn"])
        self.assertTrue(page.is_hidden("[data-automation-id=activeListContainer]"))
        self.assertEqual(page.input_value("#name--last"), "Rivera")

    def test_late_and_rebuilt_menus(self):
        for flags in ("#slow=700&rebuild", "#nosearch&slow=500&rebuild"):
            with self.subTest(flags=flags):
                page = self.open(flags)
                report, summary = self.fill(page, overwrite=True)
                self.assertEqual(self.source_entry(report)["fill"], "ok")
                self.assertEqual(self.pills(page), ["LinkedIn"])
                self.assertEqual(page.evaluate("() => window.picks"), 1)  # no wrong pick clicked while loading

    def test_each_company_s_wording(self):
        for flags, picked, pill in (("#menu=posting", "LinkedIn Job Posting", "LinkedIn Job Posting"),
                                    ("#menu=posting&nosearch", "LinkedIn Job Posting", "LinkedIn Job Posting"),
                                    ("#menu=social", "Social Media", "Social Media"),
                                    ("#menu=other", "Job Board > Other", "Other")):
            with self.subTest(flags=flags):
                page = self.open(flags)
                report, _ = self.fill(page, overwrite=True)
                self.assertEqual(self.source_entry(report)["fill"], "ok")
                self.assertEqual(self.pills(page), [pill])
                # "social" has no category marks, so each entry was opened (and undone) to see if it is one.
                self.assertEqual(page.evaluate("() => window.picked")[-1], picked)
                if "social" not in flags:
                    self.assertEqual(page.evaluate("() => window.picks"), 1)

    def test_existing_pill_is_left_alone(self):
        page = self.open()
        page.evaluate("() => pick('LinkedIn')")
        report, _ = self.fill(page, overwrite=True)
        self.assertEqual(self.pills(page), ["LinkedIn"])
        self.assertEqual(page.evaluate("() => window.picks"), 1)
        self.assertEqual(self.source_entry(report)["fill"], "ok")

    def test_two_failed_runs_hand_off_with_the_real_choices(self):
        self.store.set_fact("source.how_heard_default", "Hacker News")   # not in Visa's menu
        page = self.open()
        first, s1 = self.fill(page, overwrite=True)
        self.assertEqual([f["label"] for f in s1["fill_failed"]], ["How Did You Hear About Us?*"])
        second, s2 = self.fill(page)
        hand = s2["handoff"][0]
        self.assertEqual(hand["attempts"], 2)
        self.assertIn("Job Boards > LinkedIn", hand["why"])              # options to choose from, for the agent
        self.assertIn("Career Site", hand["why"])
        third, s3 = self.fill(page)
        self.assertTrue(self.source_entry(third)["fill"].startswith("not tried"))
        self.assertEqual(self.pills(page), [])


DROPDOWNS = (Path(__file__).resolve().parent / "fixtures" / "workday_dropdown_like.html").as_uri()
BUTTON = {"How Did You Hear About Us?*": "#source--source", "Country*": "#country--country",
          "State*": "#address--countryRegion", "Phone Device Type*": "#phoneNumber--phoneType"}
# Questionnaire dropdowns: the question is only in the fieldset's <legend> (aria-label " Select One Required").
LEGEND = {"Are you at least 18 years old?*": "[name=adult]",
          "Are you currently serving on the board of directors of any for-profit company?*": "[name=board]"}


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Playwright not installed")
class TestWorkdayDropdowns(unittest.TestCase):
    """Workday's "Select One" buttons (a <button aria-haspopup=listbox> and a list added at the page's end)."""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.store = fresh_store(with_profile=True, owner=self)
        self.app = self.store.start_application("Acme", "Engineer", ats="workday")
        self.workday = adapters.get("workday")

    def open(self, flags=""):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.goto(DROPDOWNS + flags)
        return page

    def shows(self, page):
        return {label: page.inner_text(css) for label, css in BUTTON.items()}

    def fill(self, page):
        report = fill_frame(self.workday.frame(page), self.store, self.app, self.workday, overwrite=True)
        self.assertFalse(page.evaluate("() => window.pageBack"))
        self.assertEqual(page.locator("ul[role=listbox]").count(), 0)      # every list closed again
        return report

    def test_read_as_questions_with_their_options(self):
        from jobfill.apply.forms import extract
        page = self.open()
        fields = {f["label"]: f for f in extract(page.main_frame) if f["kind"] == "dropdown"}
        self.assertEqual(sorted(fields), sorted({**BUTTON, **LEGEND}))   # legend-labelled ones included
        source = fields["How Did You Hear About Us?*"]
        self.assertTrue(source["required"])
        self.assertEqual(source["value"], "")                                # "Select One" is no answer
        self.assertIn("LinkedIn", source["options"])
        self.assertNotIn("Select One", source["options"])
        self.assertEqual(fields["Country*"]["value"], "United States of America")
        self.assertEqual(len(fields["State*"]["options"]), 51)
        self.assertEqual(fields["Are you at least 18 years old?*"]["options"], ["Yes", "No"])
        self.assertTrue(fields["Are you at least 18 years old?*"]["required"])
        self.assertEqual(page.locator("ul[role=listbox]").count(), 0)       # reading the options chose nothing

    def test_fill_picks_each_answer(self):
        page = self.open()
        report = self.fill(page)
        self.assertEqual(self.shows(page), {"How Did You Hear About Us?*": "LinkedIn",
                                            "Country*": "United States of America",
                                            "State*": "Wisconsin", "Phone Device Type*": "Mobile"})
        fills = {e["label"]: e["fill"] for e in report["fields"].values() if e["label"] in BUTTON}
        self.assertEqual(set(fills.values()), {"ok"}, fills)
        self.assertEqual(page.inner_text(LEGEND["Are you at least 18 years old?*"]), "Yes")
        # A question the bank does not know yet is read, not skipped: it comes back for the candidate.
        board = next(e for e in report["fields"].values() if e["label"].startswith("Are you currently serving"))
        self.assertEqual(board["status"], "unmatched")

    def test_long_list_that_renders_only_rows_in_view(self):
        page = self.open("#virtual")
        self.fill(page)
        self.assertEqual(page.inner_text("#address--countryRegion"), "Wisconsin")

    def test_late_rebuilt_lists_and_a_re_rendered_button(self):
        page = self.open("#slow=600&rebuild&rerender")
        self.fill(page)
        self.assertEqual(self.shows(page)["How Did You Hear About Us?*"], "LinkedIn")
        self.assertEqual(self.shows(page)["State*"], "Wisconsin")


class TestExactMenuEntries(unittest.TestCase):
    """A menu entry counts only if it is the answer (plus a qualifier Workday adds), never a look-alike."""

    def pick(self, texts, want, question_id=None):
        return adapters._pick([{"text": t} for t in texts], want, question_id)

    def test_look_alikes_are_not_the_answer(self):
        self.assertEqual(self.pick(["Interactive SQL", "SQL", "SQL Server"], "SQL"), 1)
        self.assertIsNone(self.pick(["Interactive SQL", "SQL Server"], "SQL"))
        self.assertIsNone(self.pick(["Docker - Micron"], "Docker"))
        self.assertEqual(self.pick(["Docker - Micron", "Docker"], "Docker"), 1)

    def test_qualified_forms_are(self):
        self.assertEqual(self.pick(["Python (Programming Language)"], "Python"), 0)
        self.assertEqual(self.pick(["Canada (+1)", "United States of America (+1)"], "United States of America"), 1)
        self.assertEqual(self.pick(["Job Boards > LinkedIn"], "LinkedIn"), 0)

    def test_naming_the_source_counts_only_for_how_heard(self):
        self.assertIsNone(self.pick(["LinkedIn Job Posting"], "LinkedIn"))
        self.assertEqual(self.pick(["LinkedIn Job Posting"], "LinkedIn", "how_heard"), 0)


EXPERIENCE = (Path(__file__).resolve().parent / "fixtures" / "workday_experience_like.html").as_uri()


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Playwright not installed")
class TestWorkdayExperience(unittest.TestCase):
    """Workday's "My Experience" page: Add buttons, Month / Year spin buttons, skills, the resume upload."""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.store = fresh_store(with_profile=True, owner=self)   # 1 job, 2 schools, skills incl. SQL
        resume = temp_dir(self) / "jordan_rivera_resume.pdf"
        resume.write_bytes(b"%PDF-1.4 test")
        self.store.set_fact("documents.resume_path", str(resume))
        self.app = self.store.start_application("Acme", "Engineer", ats="workday")
        self.workday = adapters.get("workday")

    def open(self, flags=""):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.goto(EXPERIENCE + flags)
        return page

    def fill(self, page, overwrite=True):
        report = fill_frame(self.workday.frame(page), self.store, self.app, self.workday, overwrite=overwrite)
        return report, summarize(report, self.app)

    def blocks(self, page, section):
        return page.locator(f"[aria-labelledby='{section}'] h5").count()

    def value(self, page, css):
        return page.evaluate("(c) => Array.from(document.querySelectorAll(c))"
                             ".map((e) => e.tagName === 'BUTTON' ? e.innerText : e.value)", css)

    def entry(self, report, label):
        return next(e for e in report["fields"].values() if e["label"] == label)

    def test_adds_a_block_per_entry_and_fills_it(self):
        page = self.open()
        report, summary = self.fill(page)
        self.assertEqual((self.blocks(page, "Work-Experience-section"), self.blocks(page, "Education-section")), (1, 2))
        self.assertEqual(len(summary["added"]), 2)
        self.assertEqual(self.value(page, "[name=jobTitle]"), ["Data Engineering Intern"])
        self.assertEqual(self.value(page, "[id*=startDate][role=spinbutton]"), ["06", "2026"])   # typed, not set
        self.assertEqual(self.value(page, "[id$=lastYearAttended-dateSectionYear-input]"), ["2027", "2025"])
        self.assertEqual(self.value(page, "button[aria-haspopup]"), ["Master's Degree", "Bachelor's Degree"])
        self.assertIn("Example Analytics", self.value(page, "[name=companyName]"))

    def test_skills_get_exact_entries_and_the_resume_is_uploaded_once(self):
        page = self.open()
        first, summary = self.fill(page)
        pills = page.evaluate("() => Array.from(document.querySelectorAll("
                              "'[data-automation-id=formField-skills] [data-automation-id=selectedItem]')).map((p) => p.title)")
        self.assertIn("SQL", pills)
        self.assertIn("Python (Programming Language)", pills)
        self.assertNotIn("Interactive SQL", pills)
        self.assertNotIn("Kubernetes Operations", pills)                   # only a look-alike exists: left out
        self.assertIn("Kubernetes", summary["partly"][0]["note"])
        self.assertEqual(page.evaluate("() => window.uploads"), 1)
        # A second run (the page was not finished in one go) adds nothing: no second resume, no new blocks.
        second, _ = self.fill(page, overwrite="own")
        self.assertEqual(page.evaluate("() => window.uploads"), 1)
        self.assertTrue(self.entry(second, "Resume/CV")["fill"].startswith("kept (already uploaded"))
        self.assertEqual(self.blocks(page, "Education-section"), 2)

    def test_blank_blocks_already_open_are_used(self):
        page = self.open("#blank=1")
        report, summary = self.fill(page)
        self.assertEqual(self.blocks(page, "Work-Experience-section"), 1)   # no Add: the blank block is filled
        self.assertEqual(self.value(page, "[name=jobTitle]"), ["Data Engineering Intern"])

    def test_resume_already_uploaded_is_not_uploaded_again(self):
        page = self.open("#uploaded=resume_2026.pdf")
        report, _ = self.fill(page)
        self.assertEqual(page.evaluate("() => window.uploads"), 0)
        self.assertEqual(self.entry(report, "Resume/CV")["fill"], "kept (already uploaded: resume_2026.pdf)")
        # The record names the file the form holds, not the default resume it would have uploaded.
        row = next(a for a in self.store.answers(self.app) if a["label"] == "Resume/CV")
        self.assertEqual((row["value"], row["source"]), ("resume_2026.pdf", "user"))


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Playwright not installed")
class TestNoExactChoiceLoop(unittest.TestCase):
    """No option is exactly the stored answer (a non-US school, a field of study): jobfill asks; the candidate
    answers in the window or through the agent; the pick is remembered and reused on the next application."""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        from jobfill.apply import runner
        self.runner = runner
        self.store = fresh_store(with_profile=True, owner=self)   # schools: UW-Madison, UNAM; fields: CS, CE
        self.data = temp_dir(self)
        self.workday = adapters.get("workday")

    def open(self, company):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.goto(EXPERIENCE + "#noce&school=dropdown")
        return page, self.store.start_application(company, "Engineer", ats="workday"), {"pages": [], "typed": {}}

    def fill(self, page, app, state, **kw):
        return self.runner.fill_page(self.store, self.data, app, state, page, self.workday, files=False, **kw)

    def asked(self, out):
        return {q["label"]: q for q in out.get("needs_user", []) if q.get("set_with")}

    def test_ask_then_remember_both_ways_and_reuse(self):
        page, app, state = self.open("Acme")
        out = self.fill(page, app, state, fresh=True)
        schools = page.locator("[name=school]")
        # Asked, not guessed: "University of Wisconsin" is a look-alike of "University of Wisconsin-Madison".
        self.assertEqual([schools.nth(i).inner_text() for i in range(2)], ["Select One", "Select One"])
        self.assertEqual(page.locator("[id$=--fieldOfStudy]").nth(1).evaluate(
            "(e) => e.closest('[data-automation-id^=formField]').querySelectorAll('[data-automation-id=selectedItem]').length"), 0)
        self.assertEqual(sum(1 for q in out["needs_user"] if q.get("set_with")), 3)
        self.assertIn("University of Wisconsin", self.asked(out)["School or University*"]["choices"])

        # 1. The candidate picks in the window: the second school and the second field of study.
        schools.nth(1).click()
        page.click("ul[role=listbox] li:has-text('Other')")
        field = page.locator("[id$=--fieldOfStudy]").nth(1)
        field.fill("Electrical")
        field.press("Enter")
        page.click("[data-automation-label='Electrical and Computer Engineering']")
        self.fill(page, app, state)
        # 2. The candidate tells the agent the first school: `apply set`.
        school_id = schools.nth(0).get_attribute("data-jobfill-id")
        self.assertTrue(self.runner.set_field(self.store, app, state, page, self.workday, school_id,
                                              "University of Wisconsin")["ok"])
        learned = {(c["question_id"], c["value"]): c["choice"] for c in self.store.all_learned_choices()}
        self.assertEqual(learned, {("edu_school", "universidad nacional autonoma de mexico"): "Other",
                                   ("edu_field", "computer engineering"): "Electrical and Computer Engineering",
                                   ("edu_school", "university of wisconsin madison"): "University of Wisconsin"})

        # The next application with the same lists: filled from memory, nothing asked.
        page2, app2, state2 = self.open("Globex")
        out2 = self.fill(page2, app2, state2, fresh=True)
        self.assertEqual(self.asked(out2), {})
        schools2 = page2.locator("[name=school]")
        self.assertEqual([schools2.nth(i).inner_text() for i in range(2)], ["University of Wisconsin", "Other"])
        self.assertIn("Electrical and Computer Engineering", page2.evaluate(
            "() => Array.from(document.querySelectorAll('[data-automation-id=selectedItem]')).map((p) => p.title)"))


if __name__ == "__main__":
    unittest.main()
