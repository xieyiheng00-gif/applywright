"""SmartRecruiters: detection (its URLs, its public API, company pages) and the filler on
tests/fixtures/smartrecruiters_like.html, a form whose every field is a web component drawn in a shadow root."""

import importlib.util
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from jobfill.apply import adapters
from jobfill.apply import detect as detect_mod
from jobfill.apply.adapters import _same_place
from jobfill.apply.detect import detect
from jobfill.apply.forms import extract
from jobfill.apply.runner import fill_frame, summarize

from .helpers import fresh_store, temp_dir

PAGE = (Path(__file__).resolve().parent / "fixtures" / "smartrecruiters_like.html").as_uri()
HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None
UUID = "e34c031b-d148-40e2-aa3e-a04025340b84"
POSTING = "https://jobs.smartrecruiters.com/AcmeRobotics/744000012345678"
FORM = f"https://jobs.smartrecruiters.com/oneclick-ui/company/AcmeRobotics/publication/{UUID}?dcr_ci=AcmeRobotics"
API = "https://api.smartrecruiters.com/v1/companies/AcmeRobotics/postings/744000012345678"

# Shape of https://api.smartrecruiters.com/v1/companies/<company>/postings/<id or uuid> (AbbVie's on 2026-11-09, trimmed).
JOB = {"id": "744000012345678", "name": "Robotics Engineer", "uuid": UUID, "refNumber": "R00152866",
       "company": {"name": "Acme Robotics", "identifier": "AcmeRobotics"},
       "location": {"city": "Madison", "region": "WI", "country": "us", "remote": False, "hybrid": True,
                    "fullLocation": "Madison, WI, United States"},
       "jobAd": {"sections": {
           "companyDescription": {"title": "Company Description", "text": "<p><strong>About Acme</strong></p><p>We build robots.</p>"},
           "jobDescription": {"title": "Job Description", "text": "<p>Build robots that sort parcels.</p>"},
           "qualifications": {"title": "Qualifications", "text": "<ul><li>Python</li><li>ROS</li></ul>"},
           "additionalInformation": {"title": "Additional Information", "text": "<p>Lunch every day</p>"}}},
       "active": True, "visibility": "PUBLIC",
       "applyUrl": "https://jobs.smartrecruiters.com/AcmeRobotics/744000012345678-robotics-engineer?oga=true"}


def fake_api(job=JOB, error=None):
    calls = []

    def fetch_json(url, timeout=30.0):
        calls.append(url)
        if url in (API, f"https://api.smartrecruiters.com/v1/companies/AcmeRobotics/postings/{UUID}"):
            if error:
                raise error
            return job
        raise AssertionError(f"unexpected request {url}")
    return fetch_json, calls


class TestSmartRecruitersDetect(unittest.TestCase):
    def d(self, url, html=None):
        return detect(url, network=False, html=html)

    def test_posting_links(self):
        for url in (POSTING, POSTING + "-robotics-engineer", POSTING + "-robotics-engineer?oga=true", POSTING + "/"):
            d = self.d(url)
            self.assertEqual((d.kind, d.ats, d.handler, d.company), ("job", "smartrecruiters", "python", "AcmeRobotics"), url)
            self.assertEqual(d.apply_url, POSTING, url)   # without the API: the posting, whose "I'm interested" leads on
            self.assertEqual(d.extra, {"company_id": "AcmeRobotics", "posting_id": "744000012345678"})
        self.assertEqual(self.d("https://jobs.smartrecruiters.com/acme-robotics/744000012345678").company, "Acme Robotics")

    def test_form_links(self):
        for url in (FORM, f"https://jobs.smartrecruiters.com/external-referrals/company/AcmeRobotics/publication/{UUID}"):
            d = self.d(url)
            self.assertEqual((d.kind, d.handler, d.apply_url), ("job", "python", FORM), url)
            self.assertEqual(d.extra, {"company_id": "AcmeRobotics", "uuid": UUID})

    def test_job_lists_and_smartrecruiters_own_site(self):
        for url in ("https://careers.smartrecruiters.com/AcmeRobotics", "https://jobs.smartrecruiters.com/AcmeRobotics",
                    "https://jobs.smartrecruiters.com/", "https://jobs.smartrecruiters.com/AcmeRobotics/?search=robot"):
            d = self.d(url)
            self.assertEqual((d.kind, d.ats), ("not_job", "smartrecruiters"), url)
            self.assertIn("job list", d.reason)
        self.assertIn("own site", self.d("https://www.smartrecruiters.com/pricing/").reason)

    def test_company_pages(self):
        filler = "<p>About us</p>" * 90
        linked = self.d("https://acme.com/careers/robotics-engineer",
                        html=f'<a href="{POSTING}-robotics-engineer">Apply</a>{filler}')
        self.assertEqual((linked.ats, linked.handler, linked.apply_url), ("smartrecruiters", "python", POSTING))
        two = self.d("https://acme.com/careers", html=f'<a href="{POSTING}">A</a>'
                                                     '<a href="https://jobs.smartrecruiters.com/AcmeRobotics/744000099999999">B</a>' + filler)
        self.assertEqual(two.kind, "not_job")
        self.assertIn("SmartRecruiters job list", two.reason)
        widget = self.d("https://acme.com/careers", html='<div class="smartrecruiters-widget" data-company="AcmeRobotics"></div>')
        self.assertEqual(widget.kind, "not_job")

    def test_posting_read_through_the_public_api(self):
        fetch_json, calls = fake_api()
        with mock.patch.object(detect_mod, "fetch_json", fetch_json), \
                mock.patch.object(detect_mod, "fetch", side_effect=AssertionError("no page fetch needed")):
            d = detect(POSTING + "-robotics-engineer")
        self.assertEqual((d.company, d.role, d.location), ("Acme Robotics", "Robotics Engineer", "Madison, WI, United States (Hybrid)"))
        self.assertEqual(d.apply_url, FORM)   # straight to the form, by the posting's uuid
        self.assertEqual(d.extra, {"company_id": "AcmeRobotics", "posting_id": "744000012345678", "uuid": UUID})
        for part in ("Company Description", "About Acme", "Build robots that sort parcels.", "ROS", "Lunch every day"):
            self.assertIn(part, d.posting)
        self.assertEqual(calls, [API])
        with mock.patch.object(detect_mod, "fetch_json", fetch_json):   # the form's link: the posting by its uuid
            self.assertEqual(detect(FORM).role, "Robotics Engineer")

    def test_removed_closed_and_api_trouble(self):
        gone = urllib.error.HTTPError(API, 404, "Not Found", {}, None)
        self.addCleanup(gone.close)
        with mock.patch.object(detect_mod, "fetch_json", fake_api(error=gone)[0]):
            d = detect(POSTING)
        self.assertEqual(d.kind, "not_job")
        self.assertIn("does not exist", d.reason)
        with mock.patch.object(detect_mod, "fetch_json", fake_api(job={**JOB, "active": False})[0]):
            self.assertIn("closed", detect(POSTING).reason)
        # Any other failure: still a job, reached through the posting's "I'm interested".
        with mock.patch.object(detect_mod, "fetch_json", fake_api(error=urllib.error.URLError("timed out"))[0]):
            d = detect(POSTING)
        self.assertEqual((d.kind, d.handler, d.apply_url), ("job", "python", POSTING))
        self.assertIn("api_error", d.extra)

    def test_places(self):
        self.assertTrue(_same_place("Madison, WI, United States", "Madison, WI, United States"))
        self.assertTrue(_same_place("Madison, WI, United States", "Madison, Wisconsin"))
        self.assertTrue(_same_place("Mexico City, CDMX, Mexico", "Mexico City, Mexico"))
        self.assertTrue(_same_place("Chicago, IL, United States", "Chicago, IL, USA"))
        self.assertFalse(_same_place("Madison, AL, United States", "Madison, WI, United States"))
        self.assertFalse(_same_place("Madison Heights, MI, United States", "Madison, MI"))
        self.assertFalse(_same_place("North Chicago, IL, United States", "Chicago, IL"))


@unittest.skipUnless(HAS_PLAYWRIGHT, "Playwright not installed")
class FillerCase(unittest.TestCase):
    """A browser for the class, a store with the sample profile per test (the two classes below run in
    parallel under tests.run)."""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()
        cls.pdf = temp_dir(cls) / "resume.pdf"
        cls.pdf.write_bytes(b"%PDF-1.4 test")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.store = fresh_store(with_profile=True, owner=self)
        self.store.set_fact("documents.resume_path", str(self.pdf))
        self.app = self.store.start_application("Acme Robotics", "Engineer", ats="smartrecruiters")
        self.sr = adapters.get("smartrecruiters")

    def open(self, flags=""):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        self.sr.open(page, PAGE + flags)
        return page

    def fill(self, page, overwrite=True, typed=None):
        report = fill_frame(self.sr.frame(page), self.store, self.app, self.sr, overwrite=overwrite, typed=typed)
        return report, summarize(report, self.app)

    def values(self, page) -> dict:
        return page.evaluate("() => ({...firstPageValues(), autofill: window.autofillUsed, submitted: window.submitted, "
                             "editors: document.querySelectorAll('[data-test$=-edit-form]').length})")


class TestSmartRecruitersFiller(FillerCase):
    """Page 1: personal information, phone, profiles, resume; blockers and buttons."""

    def test_fills_the_first_page(self):
        page = self.open("#mxphone")                                # phone country preset from the visitor's location
        report, summary = self.fill(page)
        self.assertNotIn("fill_failed", summary)
        v = self.values(page)
        self.assertEqual([v[k] for k in ("first", "last", "email", "confirm")],
                         ["Jordan", "Rivera", "jordan.rivera@example.com", "jordan.rivera@example.com"])
        self.assertEqual(v["city"], "Madison, WI, United States")   # the suggestion for the stored place, clicked
        self.assertEqual((v["country"], v["phone"]), ("US", "608-555-0142"))   # set by the stored +1 and country
        self.assertEqual(v["linkedin"], "https://www.linkedin.com/in/jordan-rivera-example")
        self.assertEqual(v["resume"], "resume.pdf")
        self.assertEqual(v["message"], "")                          # an optional cover letter: not written unasked
        self.assertFalse(v["autofill"])                             # the "Easy Apply" import is never used
        self.assertFalse(v["submitted"])
        by_label = {f["label"]: f for f in report["fields"].values()}
        self.assertEqual(by_label["City, region and country"]["question_id"], "location_full")
        self.assertEqual(by_label["Resume"]["question_id"], "resume_file")
        self.assertEqual(by_label["Message to the hiring team"]["question_id"], "cover_letter_text")
        self.assertNotIn("Country code", by_label)                  # set with the number, not asked
        self.assertIn("Facebook", [u["label"] for u in summary["unmatched"]])
        self.assertNotIn("uncertain", summary)                      # no field sent to the agent with wrong guesses
        read = {f["label"]: f for f in self.sr.adjust(extract(page.main_frame, self.sr.root_selector, read_options=False))}
        self.assertTrue(read["Resume"]["required"])
        self.assertTrue(read["First name*"]["required"])

    def test_a_place_without_a_matching_suggestion_is_asked(self):
        self.store.set_fact("address.city", "Springfield")
        self.store.set_fact("address.state", "OR")
        page = self.open()
        _, summary = self.fill(page)
        [asked] = [q for q in summary["needs_user"] if q["label"] == "City, region and country"]
        self.assertIn("apply set", asked["set_with"])                 # the candidate picks; never a look-alike
        self.assertEqual(self.values(page)["city"], "")
        self.assertNotIn("fill_failed", summary)

    def test_blockers_and_the_posting_page(self):
        self.assertEqual(self.sr.blockers(self.open()), [])
        self.assertIn("captcha", self.sr.blockers(self.open("#captcha")))       # DataDome: the candidate's
        self.assertIn("job_closed", self.sr.blockers(self.open("#expired")))
        page = self.open("#posting")                                            # "I'm interested" was clicked
        self.assertEqual(page.locator("#first-name-input input").count(), 1)

    def test_buttons(self):
        frame = self.sr.frame(self.open())
        self.assertEqual(self.sr.next_button(frame).inner_text().strip(), "Next")
        self.assertIsNone(self.sr.submit_button(frame))
        frame = self.sr.frame(self.open("#noscreening"))                       # no questions: Submit on page 1
        self.assertIsNone(self.sr.next_button(frame))
        self.assertEqual(self.sr.submit_button(frame).inner_text().strip(), "Submit")


class TestSmartRecruitersEntries(FillerCase):
    """Experience / Education, one editor at a time."""

    def test_entries_are_added_one_at_a_time(self):
        page = self.open()
        report, _ = self.fill(page)
        entries = self.values(page)["entries"]
        self.assertEqual(self.values(page)["editors"], 0)
        self.assertEqual(entries[0], {
            "title": "Data Engineering Intern", "company": "Example Analytics", "location": "Chicago, IL, United States",
            "description": entries[0]["description"], "from": "2026-06-01", "to": "2026-08-01", "current": False})
        self.assertTrue(entries[0]["description"].startswith("Built internal data tooling"))
        self.assertEqual([(e["institution"], e["major"], e["degree"], e["location"], e["from"], e["to"], e["current"])
                          for e in entries[1:]],
                         [("University of Wisconsin-Madison", "Computer Science", "Master of Science",
                           "Madison, WI, United States", "2025-09-01", "2027-05-01", True),
                          ("Universidad Nacional Autonoma de Mexico", "Computer Engineering", "Bachelor of Science",
                           "Mexico City, CDMX, Mexico", "2020-08-01", "2025-05-01", False)])
        self.assertEqual(report["added"], ["Experience: 1 entry added, 1 for 1 stored",
                                           "Education: 2 entries added, 2 for 2 stored"])

    def test_second_fill_adds_nothing_and_keeps_the_candidates_answers(self):
        page = self.open()
        typed: dict = {}
        self.fill(page, typed=typed)
        page.locator("#linkedin-input input").fill("https://www.linkedin.com/in/jordan-r")   # changed in the window
        report, summary = self.fill(page, overwrite=False, typed=typed)
        v = self.values(page)
        self.assertEqual((len(v["entries"]), v["editors"]), (3, 0))
        self.assertNotIn("added", report)
        self.assertEqual(v["linkedin"], "https://www.linkedin.com/in/jordan-r")
        self.assertIn("https://www.linkedin.com/in/jordan-r", [c["value"] for c in summary["by_candidate"]])

    def test_an_entry_the_candidate_opened_is_left_alone(self):
        page = self.open()
        page.locator("[data-test=add-experience] button").click()
        page.locator("[data-test=job-title-autocomplete] input").fill("Barista")
        report, _ = self.fill(page, overwrite=False, typed={})
        v = self.values(page)
        self.assertEqual(v["editors"], 1)
        self.assertEqual(page.locator("[data-test=job-title-autocomplete] input").input_value(), "Barista")
        self.assertIn("Experience: an entry is open in its editor", report["added"][0])
        self.assertEqual(len([e for e in v["entries"] if e]), 2)   # the education entries were still added

    def test_an_entry_that_does_not_save_is_left_open(self):
        page = self.open("#nosave")
        report, _ = self.fill(page)
        self.assertIn("Experience 1: not saved (Something went wrong. Please try again.); left open for the candidate",
                      report["added"])
        self.assertEqual(page.locator("[data-test=job-title-autocomplete] input").input_value(), "Data Engineering Intern")


@unittest.skipUnless(HAS_PLAYWRIGHT, "Playwright not installed")
class TestSmartRecruitersScreening(unittest.TestCase):
    """Page 2: the job's screening questions and the consents, then Submit with the candidate's go-ahead."""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()
        cls.data = temp_dir(cls)
        cls.pdf = cls.data / "resume.pdf"
        cls.pdf.write_bytes(b"%PDF-1.4 test")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.store = fresh_store(with_profile=True, owner=self)
        self.store.set_fact("documents.resume_path", str(self.pdf))
        self.app = self.store.start_application("Acme Robotics", "Engineer", ats="smartrecruiters")
        self.state = {"tag": "jobfill-app-test", "apply_url": PAGE}
        self.sr = adapters.get("smartrecruiters")

    def test_questions_consents_and_submit(self):
        from jobfill.apply.runner import fill_page, next_page, submit_page
        page = self.browser.new_page()
        self.addCleanup(page.close)
        self.sr.open(page, PAGE)
        fill_page(self.store, self.data, self.app, self.state, page, self.sr, fresh=True)
        moved = next_page(self.state, page, self.sr, self.app)
        self.assertEqual((moved["moved"], moved["page"]["n"], moved["errors"]), (True, 2, []))
        summary = fill_page(self.store, self.data, self.app, self.state, page, self.sr)
        self.assertNotIn("fill_failed", summary)
        self.assertEqual(page.evaluate("() => document.querySelector('sr-screening-questions-form').answers()"),
                         {"q_auth": "Yes", "q_sponsor": "Yes", "q_heard": "LinkedIn", "q_why": "", "q_certify": True})
        consents = page.evaluate("() => Array.from(document.querySelectorAll('[data-test=consent] spl-checkbox')).map((c) => c.checked)")
        self.assertEqual(consents, [False, True, False])   # not "Select all"; the privacy notice; talent pool left
        self.assertEqual([t["question_id"] for t in summary["needs_ai"]], ["why_company"])
        self.assertIn("I agree to be contacted about future job opportunities (talent pool)",
                      [u["label"] for u in summary["unmatched"]])
        self.assertFalse(page.evaluate("() => window.submitted"))   # nothing goes in without the go-ahead
        result = submit_page(self.store, self.data, self.app, self.state, page, self.sr)
        self.assertTrue(result["submitted"])
        self.assertIn("Application submitted!", page.inner_text("body"))
        self.assertEqual(page.evaluate("() => window.submitted.answers.q_heard"), "LinkedIn")
        self.assertEqual(self.store.application(self.app)["status"], "submitted")

    def test_submit_on_page_one_is_not_found(self):
        from jobfill.apply.runner import ApplyError, submit_page
        page = self.browser.new_page()
        self.addCleanup(page.close)
        self.sr.open(page, PAGE)
        with self.assertRaises(ApplyError):
            submit_page(self.store, self.data, self.app, self.state, page, self.sr)
        self.assertFalse(page.evaluate("() => window.submitted"))


if __name__ == "__main__":
    unittest.main()
