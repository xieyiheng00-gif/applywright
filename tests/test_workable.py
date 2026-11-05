"""Workable: detection (its URLs, its public API, company pages) and the filler on tests/fixtures/workable_like.html."""

import importlib.util
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from jobfill.apply import adapters
from jobfill.apply import detect as detect_mod
from jobfill.apply.detect import detect
from jobfill.apply.forms import extract
from jobfill.apply.runner import fill_frame, summarize

from .helpers import fresh_store

PAGE = (Path(__file__).resolve().parent / "fixtures" / "workable_like.html").as_uri()
HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None
APPLY = "https://apply.workable.com/acme/j/A1B2C3D4E5/apply/"

# Shapes of https://apply.workable.com/api/v2/accounts/<account>/jobs/<shortcode> and /api/v1/accounts/<account>.
JOB = {"id": 6125026, "shortcode": "A1B2C3D4E5", "title": "Robotics Engineer", "remote": False,
       "location": {"city": "Athens", "country": "Greece", "countryCode": "GR", "region": "Attica"},
       "state": "published", "workplace": "hybrid", "department": ["Engineering"],
       "description": "<p>Build robots that sort parcels.</p>", "requirements": "<ul><li>Python</li><li>ROS</li></ul>",
       "benefits": "<p><strong>Lunch</strong> every day</p>"}
ACCOUNT = {"id": 514327, "subdomain": "acme", "name": "Acme Robotics", "url": "https://acme.example"}


def fake_api(job=JOB, account=ACCOUNT, error=None):
    calls = []

    def fetch_json(url, timeout=30.0):
        calls.append(url)
        if url == "https://apply.workable.com/api/v2/accounts/acme/jobs/A1B2C3D4E5":
            if error:
                raise error
            return job
        if url == "https://apply.workable.com/api/v1/accounts/acme":
            return account
        raise AssertionError(f"unexpected request {url}")
    return fetch_json, calls


class TestWorkableDetect(unittest.TestCase):
    def d(self, url, html=None):
        return detect(url, network=False, html=html)

    def test_posting_links(self):
        for url in ("https://apply.workable.com/acme/j/A1B2C3D4E5/", APPLY, "https://apply.workable.com/acme/j/a1b2c3d4e5",
                    "https://acme.workable.com/j/A1B2C3D4E5", "https://apply.workable.com/acme/j/A1B2C3D4E5/?utm_source=x"):
            d = self.d(url)
            self.assertEqual((d.kind, d.ats, d.handler, d.company), ("job", "workable", "python", "Acme"), url)
            self.assertEqual(d.apply_url, APPLY, url)
            self.assertEqual(d.extra, {"account": "acme", "shortcode": "A1B2C3D4E5"})
        # The short link names no company; without the network the form is still reachable by its code.
        short = self.d("https://apply.workable.com/j/A1B2C3D4E5")
        self.assertEqual((short.kind, short.handler, short.company, short.apply_url),
                         ("job", "python", None, "https://apply.workable.com/j/A1B2C3D4E5/apply/"))

    def test_job_lists_and_removed_jobs(self):
        for url in ("https://apply.workable.com/acme/", "https://apply.workable.com/acme/?not_found=true",
                    "https://acme.workable.com/"):
            self.assertEqual(self.d(url).kind, "not_job", url)
        self.assertIn("removed", self.d("https://apply.workable.com/acme/?not_found=true").reason)
        self.assertIn("job list", self.d("https://apply.workable.com/acme/").reason)
        # Jobs by Workable is Workable's job board: the employer's form is one click away.
        board = self.d("https://jobs.workable.com/view/jqGLENkwi9VAGEoT2q9Qjc/robotics-engineer-in-athens-at-acme")
        self.assertEqual((board.kind, board.ats, board.handler), ("job", "workable", "follow"))
        # Workable's own site is not a company's job list.
        self.assertIsNone(self.d("https://www.workable.com/pricing").ats)

    def test_company_pages(self):
        filler = "<p>About us</p>" * 90
        linked = self.d("https://acme.com/careers/robotics-engineer",
                        html=f'<a href="https://apply.workable.com/acme/j/A1B2C3D4E5/">Apply</a>{filler}')
        self.assertEqual((linked.ats, linked.handler, linked.apply_url), ("workable", "python", APPLY))
        framed = self.d("https://acme.com/jobs/7", html='<iframe src="https://apply.workable.com/acme/j/A1B2C3D4E5/apply?embed=1">')
        self.assertEqual(framed.apply_url, APPLY)
        # Workable's widget draws the company's job list: a list, not one job.
        widget = ('<div id="whr_embed_hook"></div><script src="https://www.workable.com/assets/embed.js"></script>'
                  "<script>whr(document).ready(function(){ whr_embed(514327, {detail: 'titles', base: 'jobs'}); });</script>")
        listing = self.d("https://acme.com/careers", html=widget)
        self.assertEqual(listing.kind, "not_job")
        self.assertIn("Workable job list", listing.reason)
        two = self.d("https://acme.com/careers", html='<a href="https://apply.workable.com/j/A1B2C3D4E5">A</a>'
                                                     '<a href="https://apply.workable.com/j/F6E5D4C3B2">B</a>' + filler)
        self.assertEqual((two.kind, two.ats), ("not_job", None))

    def test_posting_read_through_the_public_api(self):
        fetch_json, calls = fake_api()
        with mock.patch.object(detect_mod, "fetch_json", fetch_json), \
                mock.patch.object(detect_mod, "fetch", side_effect=AssertionError("no page fetch needed")):
            d = detect("https://apply.workable.com/acme/j/A1B2C3D4E5/")
        self.assertEqual((d.company, d.role, d.location), ("Acme Robotics", "Robotics Engineer", "Athens, Attica, Greece (Hybrid)"))
        for part in ("Build robots that sort parcels.", "ROS", "Lunch every day"):
            self.assertIn(part, d.posting)
        self.assertEqual(len(calls), 2)

    def test_removed_posting_and_api_trouble(self):
        gone = urllib.error.HTTPError("https://apply.workable.com/api/v2/accounts/acme/jobs/A1B2C3D4E5", 404,
                                      "Not Found", {}, None)
        self.addCleanup(gone.close)
        fetch_json, _ = fake_api(error=gone)
        with mock.patch.object(detect_mod, "fetch_json", fetch_json):
            d = detect(APPLY)
        self.assertEqual(d.kind, "not_job")
        self.assertIn("does not exist", d.reason)
        # Any other failure: still a job, filled from the form itself.
        fetch_json, _ = fake_api(error=urllib.error.URLError("timed out"))
        with mock.patch.object(detect_mod, "fetch_json", fetch_json):
            d = detect(APPLY)
        self.assertEqual((d.kind, d.handler, d.company), ("job", "python", "Acme Robotics"))
        self.assertIn("api_error", d.extra)

    def test_short_link_names_its_company_on_its_page(self):
        page = ('<link rel="canonical" href="https://apply.workable.com/acme/j/A1B2C3D4E5" />'
                '<meta name="subdomain" content="acme">')
        fetch_json, _ = fake_api()
        with mock.patch.object(detect_mod, "fetch_json", fetch_json), mock.patch.object(detect_mod, "fetch", return_value=page):
            d = detect("https://apply.workable.com/j/A1B2C3D4E5")
        self.assertEqual((d.apply_url, d.company, d.role), (APPLY, "Acme Robotics", "Robotics Engineer"))

    def test_survey_link_from_the_reminder_email_is_not_a_job(self):
        d = self.d("https://apply.workable.com/acme/candidates/3f9a2c/eeoc_profile/new/")
        self.assertEqual((d.kind, d.ats), ("not_job", "workable"))
        self.assertIn("reminder email", d.reason)


# Workable's EEO survey questions and answers, verbatim from its careers bundle (fixed US-government wording).
SURVEY_OPTIONS = {
    "Race or ethnicity": ["Hispanic or Latino", "White (Not Hispanic or Latino)",
                          "Black or African American (Not Hispanic or Latino)",
                          "Native Hawaiian or Other Pacific Islander (Not Hispanic or Latino)",
                          "Asian (Not Hispanic or Latino)", "American Indian or Alaska Native (Not Hispanic or Latino)",
                          "Two or More Races (Not Hispanic or Latino)", "I don't wish to answer"],
    "Gender": ["Male", "Female", "I don't wish to answer"],
    "Veteran status": ["I IDENTIFY AS ONE OR MORE OF THE CLASSIFICATIONS OF PROTECTED VETERAN LISTED ABOVE",
                       "I AM NOT A PROTECTED VETERAN", "I CHOOSE NOT TO SELF-IDENTIFY MY PROTECTED VETERAN STATUS"],
    "Disability status": ["Yes, I have a disability, or have had one in the past",
                          "No, I do not have a disability and have not had one in the past", "I do not want to answer"],
}


class TestWorkableSurveyAnswers(unittest.TestCase):
    """The stored EEO facts answer Workable's survey wording; "decline" is a valid answer to every question."""

    def answers(self, **facts):
        from jobfill.resolver import Resolver
        store = fresh_store(with_profile=True, owner=self)
        for key, value in facts.items():
            store.set_fact(f"eeo.{key}", value)
        app = store.start_application("Acme Robotics", "Engineer")
        fields = [{"id": label, "label": label, "type": "radio", "options": options}
                  for label, options in SURVEY_OPTIONS.items()]
        return {f["id"]: (f["status"], f.get("value")) for f in Resolver(store, app, log=False).resolve(fields)["fields"]}

    def test_stored_answers(self):
        got = self.answers(gender="female", race=["white"], hispanic_latino="no", veteran_status="not_veteran",
                           disability_status="no")
        self.assertEqual(got, {"Race or ethnicity": ("filled", "White (Not Hispanic or Latino)"),
                               "Gender": ("filled", "Female"),
                               "Veteran status": ("filled", "I AM NOT A PROTECTED VETERAN"),
                               "Disability status": ("filled", "No, I do not have a disability and have not had one in "
                                                               "the past")})

    def test_decline_everywhere(self):
        got = self.answers(gender="decline", race=["decline"], hispanic_latino="decline", veteran_status="decline",
                           disability_status="decline")
        self.assertEqual({k: v[1] for k, v in got.items()},
                         {"Race or ethnicity": "I don't wish to answer", "Gender": "I don't wish to answer",
                          "Veteran status": "I CHOOSE NOT TO SELF-IDENTIFY MY PROTECTED VETERAN STATUS",
                          "Disability status": "I do not want to answer"})

    def test_an_answer_the_survey_does_not_offer_is_asked(self):
        got = self.answers(gender="non_binary")   # the EEOC form offers Male, Female, or no answer
        self.assertEqual(got["Gender"], ("needs_user", None))


@unittest.skipUnless(HAS_PLAYWRIGHT, "Playwright not installed")
class TestWorkableFiller(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()
        cls.pdf = Path(tempfile.mkdtemp()) / "resume.pdf"
        cls.pdf.write_bytes(b"%PDF-1.4 test")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.store = fresh_store(with_profile=True, owner=self)
        self.store.set_fact("documents.resume_path", str(self.pdf))
        self.app = self.store.start_application("Acme Robotics", "Engineer", ats="workable")
        self.workable = adapters.get("workable")

    def open(self, flags=""):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        self.workable.open(page, PAGE + flags)
        return page

    def fill(self, page, overwrite=True, typed=None):
        report = fill_frame(self.workable.frame(page), self.store, self.app, self.workable, overwrite=overwrite,
                            typed=typed)
        return report, summarize(report, self.app)

    def state(self, page) -> dict:
        return page.evaluate(r"""() => ({
          cookie: window.cookieChoice, submitted: window.submitted, autofill: window.autofillUsed,
          cards: Array.from(document.querySelectorAll('[data-ui=group]')).map((g) =>
            Array.from(g.querySelectorAll('dl')).map((d) => d.innerText).join(' ').replace(/\s+/g, ' ').trim()),
          editors: document.querySelectorAll('[data-ui=editor]').length,
          dial: document.querySelector('.iti__selected-dial-code').textContent, phone: document.querySelector('#input_phone').value,
          address: document.querySelector('#address').value, english: document.querySelector('#input_QA_4_input').value,
          english_id: document.querySelector('input[name=QA_4]').value,
          radios: Array.from(document.querySelectorAll('[role=radio][aria-checked=true]')).map((r) => r.id),
          consent: document.querySelector('#gdpr_box').getAttribute('aria-checked'),
          resume: (document.querySelector('[data-id=filename]') || {}).innerText,
          open_popups: document.querySelectorAll('dialog[open], .react-datepicker-popper').length})""")

    def test_fills_the_form(self):
        page = self.open()
        report, summary = self.fill(page)
        self.assertNotIn("fill_failed", summary)
        s = self.state(page)
        self.assertEqual(s["cookie"], "decline")                  # the banner's backdrop is gone, optional cookies declined
        self.assertEqual([page.input_value(f"#{n}") for n in ("firstname", "lastname", "email")],
                         ["Jordan", "Rivera", "jordan.rivera@example.com"])
        self.assertEqual((s["dial"], s["phone"]), ("+1", "608-555-0142"))   # country set by the typed code, not the IP guess
        self.assertEqual(s["address"], "Madison, WI, United States")       # a location, replacing the IP guess
        by_label = {v["label"]: v for v in report["fields"].values()}
        self.assertEqual(by_label["City, region and country"]["question_id"], "location_full")
        self.assertEqual((s["english"], s["english_id"]), ("Fluent", "1"))  # read-only select: picked in its list
        self.assertEqual(s["radios"], ["wrapper_q2y", "wrapper_q3y", "wrapper_q5b"])   # authorized, sponsorship, LinkedIn
        self.assertEqual(s["consent"], "true")
        self.assertEqual(s["resume"], "resume.pdf")
        self.assertEqual(by_label["Resume"]["question_id"], "resume_file")   # not "Resume Choose file"
        self.assertEqual(s["open_popups"], 0)                     # no list or calendar left covering a field
        self.assertFalse(s["submitted"])
        self.assertFalse(s["autofill"])                           # "Import resume from" is never used
        self.assertEqual(len(page.context.pages), 1)              # the Privacy Notice link was not clicked
        self.assertEqual([t["question_id"] for t in summary["needs_ai"]].count("why_company"), 1)
        read = {f["label"]: f for f in extract(page.main_frame, self.workable.root_selector, read_options=False)}
        self.assertTrue(read["What is your level of English?"]["required"])   # marked on its hidden value input

    def test_entries_are_added_one_at_a_time(self):
        page = self.open()
        report, _ = self.fill(page)
        s = self.state(page)
        self.assertEqual(s["editors"], 0)
        self.assertEqual(s["cards"][:2], [
            "School University of Wisconsin-Madison Field of study Computer Science Degree Master of Science "
            "Period September 2025 - May 2027",
            "School Universidad Nacional Autonoma de Mexico Field of study Computer Engineering Degree Bachelor of "
            "Science Period August 2020 - May 2025"])
        self.assertIn("Title Data Engineering Intern Company Example Analytics Period June 2026 - August 2026", s["cards"][2])
        self.assertEqual(report["added"], ["Education: 2 entries added, 2 for 2 stored",
                                           "Experience: 1 entry added, 1 for 1 stored"])
        schools = [v["value"] for v in report["fields"].values() if v.get("question_id") == "edu_school"]
        self.assertEqual(schools, ["University of Wisconsin-Madison", "Universidad Nacional Autonoma de Mexico"])

    def test_second_fill_adds_nothing_and_keeps_the_candidates_answers(self):
        page = self.open()
        typed: dict = {}
        self.fill(page, typed=typed)
        page.fill("#QA_1", "Open to discuss")     # the candidate changed their salary answer in the window
        report, summary = self.fill(page, overwrite=False, typed=typed)
        s = self.state(page)
        self.assertEqual((len(s["cards"]), s["editors"]), (3, 0))
        self.assertNotIn("added", report)
        self.assertEqual(page.input_value("#QA_1"), "Open to discuss")
        self.assertIn("Open to discuss", [c["value"] for c in summary["by_candidate"]])

    def test_an_entry_the_candidate_opened_is_left_alone(self):
        page = self.open()
        page.click("[data-ui=experience] [data-ui=add-section]")
        page.fill("[data-ui=experience] [name=title]", "Barista")
        report, _ = self.fill(page, overwrite=False, typed={})
        s = self.state(page)
        self.assertEqual(s["editors"], 1)
        self.assertEqual([page.input_value(f"[data-ui=experience] [name={n}]") for n in ("title", "company")], ["Barista", ""])
        self.assertIn("Experience: an entry is open in its editor", report["added"][-1])
        self.assertEqual(len(s["cards"]), 2)      # the education entries were still added

    def test_an_entry_that_does_not_save_is_left_open(self):
        page = self.open("#nosave")
        report, _ = self.fill(page)
        self.assertIn("Experience 1: not saved (Something went wrong. Please try again.); left open for the candidate",
                      report["added"])
        self.assertEqual(page.input_value("[data-ui=experience] [name=title]"), "Data Engineering Intern")
        self.assertEqual(len(self.state(page)["cards"]), 2)

    def test_future_end_date_is_reported_not_retried_blindly(self):
        page = self.open("#maxdate=2026-11")
        _, summary = self.fill(page)
        [failed] = summary["fill_failed"]
        self.assertEqual(failed["value"], "05/2027")              # expected graduation
        self.assertIn("takes no date after the current month", failed["why"])
        self.assertTrue(self.state(page)["cards"][0].endswith("Period September 2025"))   # saved as ongoing

    def test_blockers(self):
        self.assertEqual(self.workable.blockers(self.open()), [])
        self.assertIn("captcha", self.workable.blockers(self.open("#captcha")))   # Cloudflare Turnstile shows itself
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.goto(PAGE + "?not_found=true")        # a removed job's link lands on the job list
        self.assertIn("job_closed", self.workable.blockers(page))

    def test_submit_button_is_found_but_never_clicked_by_fill(self):
        page = self.open()
        self.fill(page)
        button = self.workable.submit_button(self.workable.frame(page))
        self.assertEqual(button.inner_text().strip(), "Submit application")
        self.assertFalse(page.evaluate("() => window.submitted"))


@unittest.skipUnless(HAS_PLAYWRIGHT, "Playwright not installed")
class TestWorkableSurvey(unittest.TestCase):
    """The optional EEO survey Workable shows right after the application went in (US jobs)."""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()
        cls.data = Path(tempfile.mkdtemp())
        cls.pdf = cls.data / "resume.pdf"
        cls.pdf.write_bytes(b"%PDF-1.4 test")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.store = fresh_store(with_profile=True, owner=self)   # gender non-binary, race and disability declined
        self.store.set_fact("documents.resume_path", str(self.pdf))
        self.app = self.store.start_application("Acme Robotics", "Engineer", ats="workable")
        self.state = {"tag": "jobfill-app-test", "apply_url": PAGE}
        self.workable = adapters.get("workable")

    def submitted_application(self, flags="#eeo&ofccp"):
        """The page after the application went in: filled, then submitted with the candidate's go-ahead."""
        from jobfill.apply.runner import fill_page, submit_page
        page = self.browser.new_page()
        self.addCleanup(page.close)
        self.workable.open(page, PAGE + flags)
        fill_page(self.store, self.data, self.app, self.state, page, self.workable, fresh=True)
        return page, submit_page(self.store, self.data, self.app, self.state, page, self.workable)

    def fill(self, page):
        from jobfill.apply.runner import fill_page
        summary = fill_page(self.store, self.data, self.app, self.state, page, self.workable)
        return summary, (self.data / "apply" / f"app{self.app}" / "report.json").read_text(encoding="utf-8")

    def checked(self, page):
        return page.evaluate("() => Object.fromEntries(Array.from(document.querySelectorAll("
                             "'form[data-ui=eeoc-form] input:checked')).map((i) => [i.name, i.value]))")

    def test_submit_reports_the_survey_and_fill_answers_it(self):
        page, result = self.submitted_application()
        self.assertTrue(result["submitted"])
        self.assertEqual(result["survey"], "eeo")
        self.assertIn("apply fill", result["next"])
        self.assertEqual(self.store.application(self.app)["status"], "submitted")
        summary, report = self.fill(page)
        self.assertNotIn("moved_by_candidate", summary)          # jobfill brought the survey up, not the candidate
        self.assertEqual(self.checked(page), {"race": "undisclosed", "gender": "undisclosed", "veteran": "not_veteran",
                                              "disability": "undisclosed"})
        # Non-binary is not offered (Male / Female / no answer): asked, never picked for them.
        self.assertEqual([q["label"] for q in summary["needs_user"]], ["Gender"])
        for label in ("Race or ethnicity", "Veteran status", "Disability status"):
            self.assertIn(f'"label": "{label}"', report)
        self.assertIsNone(page.evaluate("() => window.surveyAnswers"))   # the survey waits for the go-ahead

    def test_survey_goes_in_only_through_submit(self):
        from jobfill.apply.runner import submit_page
        page, _ = self.submitted_application()
        self.fill(page)
        result = submit_page(self.store, self.data, self.app, self.state, page, self.workable)
        self.assertEqual((result["submitted"], result["survey_submitted"]), (True, True))
        self.assertNotIn("survey", result)
        self.assertEqual(page.evaluate("() => window.surveyAnswers"),
                         {"race": "undisclosed", "gender": "undisclosed", "veteran": "not_veteran",
                          "disability": "undisclosed"})
        self.assertIn("Your EEO form has been submitted successfully.", page.inner_text("body"))
        self.assertFalse(page.evaluate("() => window.surveySkipped || false"))   # skip is the candidate's call

    def test_preselection_is_not_an_answer_but_the_candidates_pick_is(self):
        for key, value in (("gender", "female"), ("race", ["white"]), ("hispanic_latino", "no")):
            self.store.set_fact(f"eeo.{key}", value)
        page, _ = self.submitted_application()
        page.click("form[data-ui=eeoc-form] [role=radio]:has(input[name=disability][value=disabled])")   # theirs
        summary, _ = self.fill(page)
        self.assertEqual(self.checked(page), {"race": "white", "gender": "female", "veteran": "not_veteran",
                                              "disability": "disabled"})
        self.assertEqual([c["label"] for c in summary["by_candidate"]], ["Disability status"])

    def test_survey_without_federal_contractor_sections(self):
        page, result = self.submitted_application("#eeo")
        self.assertEqual(result["survey"], "eeo")
        self.fill(page)
        self.assertEqual(sorted(self.checked(page)), ["gender", "race"])

    def test_no_survey(self):
        page, result = self.submitted_application("")
        self.assertTrue(result["submitted"])
        self.assertNotIn("survey", result)
        self.assertIsNone(self.workable.survey(page))


if __name__ == "__main__":
    unittest.main()
