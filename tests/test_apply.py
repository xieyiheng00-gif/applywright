import importlib.util
import unittest
from pathlib import Path

from jobfill.apply import adapters
from jobfill.apply.detect import detect

from .helpers import fresh_store, temp_dir

FIXTURES = Path(__file__).resolve().parent / "fixtures"
HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None


class TestDetect(unittest.TestCase):
    def d(self, url, html=None):
        return detect(url, network=False, html=html)

    def test_known_ats_urls(self):
        gh = self.d("https://job-boards.greenhouse.io/anthropic/jobs/5456619008")
        self.assertEqual((gh.kind, gh.ats, gh.handler, gh.company), ("job", "greenhouse", "python", "Anthropic"))
        lv = self.d("https://jobs.lever.co/palantir/6ed76ce8-4156-4b60-b120-403538bd66cd")
        self.assertEqual((lv.ats, lv.handler), ("lever", "python"))
        self.assertTrue(lv.apply_url.endswith("/apply"))
        ic = self.d("https://careers-acme.icims.com/jobs/12345/software-engineer/job")
        self.assertEqual((ic.ats, ic.handler, ic.apply_url),
                         ("icims", "python", "https://careers-acme.icims.com/jobs/12345/software-engineer/login"))

    def test_ashby(self):
        job = self.d("https://jobs.ashbyhq.com/openai/240d459b-696d-43eb-8497-fab3e56ecd9b")
        self.assertEqual((job.kind, job.ats, job.handler), ("job", "ashby", "python"))
        self.assertEqual(job.apply_url,
                         "https://jobs.ashbyhq.com/openai/240d459b-696d-43eb-8497-fab3e56ecd9b/application")
        self.assertEqual(self.d("https://jobs.ashbyhq.com/openai/240d459b-696d-43eb-8497-fab3e56ecd9b/application").ats,
                         "ashby")
        self.assertEqual(self.d("https://jobs.ashbyhq.com/openai").kind, "not_job")
        embedded = self.d("https://acme.ai/careers?ashby_jid=240d459b-696d-43eb-8497-fab3e56ecd9b",
                          html='<script src="https://jobs.ashbyhq.com/acme/embed?version=2"></script>' + "<p>x</p>" * 90)
        self.assertEqual((embedded.ats, embedded.apply_url),
                         ("ashby", "https://jobs.ashbyhq.com/acme/240d459b-696d-43eb-8497-fab3e56ecd9b/application"))

    def test_workday_is_filled_by_python_after_login(self):
        wd = self.d("https://nvidia.wd5.myworkdayjobs.com/en-US/External/job/US-CA-Santa-Clara/Senior-Engineer_JR2014108")
        self.assertEqual((wd.kind, wd.ats, wd.handler, wd.company, wd.role),
                         ("job", "workday", "python", "Nvidia", "Senior Engineer"))
        self.assertIn("jobfill login", wd.reason)
        self.assertEqual(self.d("https://nvidia.wd5.myworkdayjobs.com/en-US/External").kind, "not_job")

    def test_not_a_job(self):
        for url in ("hello", "ftp://example.com/file", "https://boards.greenhouse.io/anthropic",
                    "https://jobs.lever.co/palantir", "https://careers-acme.icims.com/jobs/search?ss=1",
                    "https://www.linkedin.com/feed/"):
            self.assertEqual(self.d(url).kind, "not_job", url)
        page = "<html><head><title>Recipes</title></head><body>" + "Bake the bread for forty minutes. " * 40 + "</body></html>"
        self.assertEqual(self.d("https://example.com/recipes/bread", html=page).kind, "not_job")

    def test_job_boards_are_followed(self):
        li = self.d("https://www.linkedin.com/jobs/view/4012345678")
        self.assertEqual((li.kind, li.handler), ("job", "follow"))

    def test_company_portals_go_to_the_agent(self):
        amz = self.d("https://www.amazon.jobs/en/jobs/2734567/software-development-engineer")
        self.assertEqual((amz.ats, amz.handler, amz.company), ("amazon", "ai", "Amazon"))
        tt = self.d("https://lifeattiktok.com/search/7412345678901234567")
        self.assertEqual((tt.ats, tt.company), ("tiktok", "TikTok"))

    def test_embedded_greenhouse_on_company_site(self):
        html = ('<html><body><div id="grnhse_app"></div><script src="https://boards.greenhouse.io/embed/job_board/js?for=acme">'
                '</script>' + "<p>About the role</p>" * 50 + "</body></html>")
        d = self.d("https://acme.com/careers?gh_jid=4001234", html=html)
        self.assertEqual((d.ats, d.handler), ("greenhouse", "python"))
        self.assertEqual(d.apply_url, "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=4001234")

    def test_lever_link_and_json_ld(self):
        lever = '<a href="https://jobs.lever.co/globex/6ed76ce8-4156-4b60-b120-403538bd66cd">Apply</a>'
        self.assertEqual(self.d("https://globex.com/careers/data-engineer", html=lever).ats, "lever")
        ld = ('<script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",'
              '"title":"Data Engineer","hiringOrganization":{"@type":"Organization","name":"Initech"},'
              '"description":"<p>Build pipelines</p>"}</script>')
        d = self.d("https://initech.com/jobs/42", html=ld)
        self.assertEqual((d.kind, d.handler, d.company, d.role), ("job", "ai", "Initech", "Data Engineer"))


@unittest.skipUnless(HAS_PLAYWRIGHT, "Playwright not installed")
class TestFiller(unittest.TestCase):
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

    def run_fixture(self, name, ats, company, store=None, hash=""):
        from jobfill.apply.runner import fill_frame, summarize
        store = store or fresh_store(with_profile=True, owner=self)
        store.set_fact("documents.resume_path", str(self.pdf))
        app = store.start_application(company, "Engineer")
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.goto((FIXTURES / name).as_uri() + hash)
        adapter = adapters.get(ats)
        report = fill_frame(adapter.frame(page), store, app, adapter, overwrite=True)
        return page, report, summarize(report, app)

    def dom(self, page, js):
        return page.evaluate(js)

    def test_greenhouse_like_form(self):
        page, report, summary = self.run_fixture("greenhouse_like.html", "greenhouse", "Acme Robotics")
        labels = {v["label"]: v for v in report["fields"].values()}
        self.assertIn("Resume/CV*", labels)                 # file input labelled by its block, not "Attach"
        self.assertFalse([l for l in labels if not l])      # React-Select helper inputs skipped
        self.assertNotIn("fill_failed", summary)
        v = lambda sel: self.dom(page, f"() => document.querySelector('{sel}').value")
        self.assertEqual((v("#first_name"), v("#last_name"), v("#phone")), ("Jordan", "Rivera", "608-555-0142"))
        combos = self.dom(page, "() => Array.from(document.querySelectorAll('.select__single-value')).map(e => e.textContent)")
        self.assertEqual(combos[0], "United States +1")                       # country option with suffix
        self.assertEqual(combos[1], "Yes")                                    # sponsorship
        self.assertEqual(combos[2], "University of Wisconsin - Madison")      # search-as-you-type dropdown
        self.assertEqual(combos[3], "Master's Degree")
        self.assertEqual(combos[4], "Non-binary")
        self.assertTrue(self.dom(page, "() => document.getElementById('certify').checked"))
        self.assertEqual(self.dom(page, "() => document.getElementById('resume').files[0].name"), "resume.pdf")
        self.assertEqual([t["question_id"] for t in summary["needs_ai"]], ["why_company"])
        self.assertEqual([u["label"] for u in summary["unmatched"]], ["What is your favorite color?"])

    def test_follow_up_question_revealed_by_an_answer(self):
        store = fresh_store(with_profile=True, owner=self)
        store.set_fact("eeo.hispanic_latino", "no")
        store.set_fact("eeo.race", "white")
        page, report, _ = self.run_fixture("greenhouse_like.html", "greenhouse", "Acme Robotics", store)
        self.assertEqual(report["passes"], 2)
        self.assertEqual(self.dom(page, "() => document.getElementById('race').selectedOptions[0].text"), "White")

    def test_lever_like_form(self):
        page, report, summary = self.run_fixture("lever_like.html", "lever", "Globex")
        self.assertNotIn("fill_failed", summary)
        get = lambda name: self.dom(page, f"() => document.querySelector('[name=\"{name}\"]').value")
        self.assertEqual(get("name"), "Jordan Rivera")
        self.assertEqual(get("location"), "Madison, Wisconsin, United States")   # autocomplete suggestion picked
        checked = self.dom(page, "() => Array.from(document.querySelectorAll('input:checked')).map(e => e.name + '=' + e.value)")
        self.assertIn("cards[aaa][field0]=Yes", checked)      # authorized to work
        self.assertIn("cards[bbb][field0]=Yes", checked)      # needs sponsorship (sample profile)
        self.assertIn("cards[ccc][field0]=English (ENG)", checked)
        self.assertIn("cards[ccc][field0]=Spanish (SPA)", checked)
        self.assertNotIn("cards[ccc][field0]=French (FRA)", checked)
        self.assertEqual(get("eeo[veteran]"), "Not a veteran")
        labels = {v["label"] for v in report["fields"].values()}
        self.assertIn("Resume/CV ✱", labels)                 # hidden "Analyzing resume..." text not in the label

    def test_second_fill_keeps_manual_edits(self):
        from jobfill.apply.runner import fill_frame
        store = fresh_store(with_profile=True, owner=self)
        page, _, _ = self.run_fixture("lever_like.html", "lever", "Globex", store)
        page.fill("[name=phone]", "555-0000")       # the candidate corrected it by hand
        app = store.applications()[0]["id"]
        report = fill_frame(adapters.get("lever").frame(page), store, app, adapters.get("lever"), overwrite=False)
        self.assertEqual(page.input_value("[name=phone]"), "555-0000")
        phone = next(v for v in report["fields"].values() if v["label"] == "Phone")
        self.assertTrue(phone["fill"].startswith("kept"))

    def test_field_instructions_are_read_and_followed(self):
        from jobfill.apply.forms import extract
        page, report, summary = self.run_fixture("described_like.html", "greenhouse", "Acme Robotics")
        desc = {f["label"]: f.get("description") for f in extract(page.main_frame, None)}
        self.assertIn('please enter "None"', desc["Current Company*"])            # aria-describedby, error slot skipped
        self.assertIn("150 words", desc["Why Acme Robotics?"])                     # Ashby description in the field block
        self.assertIsNone(desc["What is your favorite color?"])                    # never the neighbour's
        # No current employer (only an internship): the form's own "enter None" is the answer.
        self.assertEqual((page.input_value("#q_company"), page.input_value("#q_title")), ("None", "None"))
        # The writing task carries the field's instructions.
        why = next(t for t in summary["needs_ai"] if t["question"] == "Why Acme Robotics?")
        self.assertIn("150 words", why["instructions"])

    def test_long_list_is_searched_for_the_exact_name(self):
        # Opened, the School list shows only its first entries (A...): jobfill searches it for the stored name.
        page, report, summary = self.run_fixture("school_search_like.html", "greenhouse", "Acme Capital")
        shown = self.dom(page, "() => Array.from(document.querySelectorAll('.select__single-value')).map(e => e.textContent)")
        self.assertEqual(shown, ["University of Wisconsin, Madison", ""])   # same name, other punctuation
        # The bachelor's school is not in the catalogue: asked, with what the searches found as choices.
        [ask] = [v for v in summary["needs_user"] if v["label"] == "School"]
        self.assertIn("Universidad Nacional de Colombia", ask["choices"])
        self.assertTrue(ask["set_with"])

    def test_list_search_never_picks_a_look_alike(self):
        # "Wisconsin" finds Milwaukee and Eau Claire, "Madison" finds James Madison: none of them is the school.
        page, report, summary = self.run_fixture("school_search_like.html", "greenhouse", "Acme Capital",
                                                 hash="#nomadison")
        shown = self.dom(page, "() => Array.from(document.querySelectorAll('.select__single-value')).map(e => e.textContent)")
        self.assertEqual(shown, ["", ""])
        asks = [v for v in summary["needs_user"] if v["label"] == "School"]
        self.assertEqual(len(asks), 2)
        self.assertIn("University of Wisconsin-Milwaukee", asks[0]["choices"])

    def test_ashby_like_form(self):
        page, report, summary = self.run_fixture("ashby_like.html", "ashby", "Acme AI")
        by_label = {v["label"]: v for v in report["fields"].values()}
        self.assertNotIn("fill_failed", summary)
        # Unlabeled "autofill from resume" drop zone is ignored; the real Resume field gets the file.
        self.assertEqual([v["label"] for v in report["fields"].values() if v["kind"] == "file"], ["Resume"])
        self.assertEqual(page.evaluate("() => document.getElementById('_systemfield_resume').files.length"), 1)
        # Yes/No toggle buttons are one question, answered by pressing the button.
        hq = by_label["Are you able to work from our San Francisco HQ 3 days per week?"]
        self.assertEqual((hq["kind"], hq["question_id"], hq["value"]), ("buttons", "onsite_ok", "Yes"))
        self.assertEqual(page.get_attribute("[data-option=yes]", "aria-pressed"), "true")
        # Checkboxes with one name each are still one question, labelled by their fieldset.
        heard = by_label["How did you hear about Acme AI?"]
        self.assertEqual((heard["kind"], heard["question_id"]), ("checkbox", "how_heard"))
        self.assertEqual(page.evaluate("() => Array.from(document.querySelectorAll('fieldset input:checked')).map(e => e.id)"),
                         ["q2-0", "v-1"])
        # Fieldset <label> is the question text, not the "Input gender" helper paragraph.
        self.assertEqual(by_label["Gender"]["status"], "needs_user")      # sample profile: non-binary, no such option
        self.assertEqual(by_label["Veteran Status"]["value"], "I am not a protected veteran")
        self.assertEqual(page.input_value("#question_3"), "June 1, 2027")   # plain-English question, readable date

    def test_source_question_shapes(self):
        page, report, summary = self.run_fixture("source_like.html", "generic", "Acme")
        self.assertNotIn("fill_failed", summary)
        got = page.evaluate("""() => ({
            combined: document.querySelector('#s1').selectedOptions[0].text,
            optgroup: document.querySelector('#s2').selectedOptions[0].parentElement.label + ' / ' +
                      document.querySelector('#s2').selectedOptions[0].text,
            category: document.querySelector('#s3').selectedOptions[0].text,
            detail: document.querySelector('#s3b') && document.querySelector('#s3b').selectedOptions[0].text,
            menu: document.querySelector('.select__single-value').textContent + ' / ' + window.pickedGroup})""")
        self.assertEqual(got, {"combined": "Job Board: LinkedIn", "optgroup": "Job Board / LinkedIn",
                               "category": "Job Board", "detail": "LinkedIn", "menu": "LinkedIn / Job Board"})
        self.assertEqual(report["passes"], 2)   # the follow-up appeared after the category was picked

    def test_icims_email_gate_respects_consent_and_dry_run(self):
        store = fresh_store(with_profile=True, owner=self)
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.goto((FIXTURES / "icims_like.html").as_uri())
        icims = adapters.get("icims")
        self.assertIn("email_gate", icims.prepare(page, store, dry_run=True))
        self.assertEqual(page.input_value("#email"), "")                    # dry run typed nothing
        store.unset_fact("consent.privacy_ack")
        self.assertIn("consent_required", icims.prepare(page, store))       # no standing consent: stop
        self.assertFalse(page.is_checked("#accept_gdpr"))
        store.set_fact("consent.privacy_ack", True)
        icims.prepare(page, store)
        self.assertTrue(page.is_checked("#accept_gdpr"))
        self.assertEqual(page.evaluate("() => document.body.dataset.submitted"), "jordan.rivera@example.com")

    def test_two_failures_hand_the_field_to_the_agent(self):
        from unittest import mock
        from jobfill import report
        from jobfill.apply import runner
        store = fresh_store(with_profile=True, owner=self)
        app = store.start_application("Globex", "Engineer")
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.goto((FIXTURES / "lever_like.html").as_uri())
        lever = adapters.get("lever")
        tried, real_fill = [], runner.fill

        def phone_widget_breaks(frame, f, value, **kw):   # a widget jobfill cannot operate
            if f["label"] == "Phone":
                tried.append(value)
                return False, "widget not supported"
            return real_fill(frame, f, value, **kw)

        def run(overwrite=False):
            return runner.summarize(runner.fill_frame(lever.frame(page), store, app, lever, overwrite=overwrite,
                                                      files=False), app)

        with mock.patch.object(runner, "fill", phone_widget_breaks):
            first = run(overwrite=True)
            self.assertEqual([f["label"] for f in first["fill_failed"]], ["Phone"])   # once: retried next run
            self.assertNotIn("handoff", first)
            second = run()
            self.assertEqual([(h["label"], h["attempts"]) for h in second["handoff"]], [("Phone", 2)])
            self.assertEqual(second["counts"]["handoff"], 1)
            third = run()
            self.assertEqual(len(tried), 2)                       # not tried a third time
            self.assertEqual(third["handoff"][0]["why"], "not tried: failed 2 times with this value")
            rec = {f["question"]: f for f in report.build(store, app)["fields"]}
            self.assertEqual(rec["Phone"]["answered_by"], "open")
            # A corrected value is a new attempt.
            store.set_fact("contact.phone", "608-555-7777")
            run()
            self.assertEqual(tried[-1], "608-555-7777")

    def test_submit_button_is_found_but_never_clicked_by_fill(self):
        page, _, _ = self.run_fixture("lever_like.html", "lever", "Globex")
        frame = adapters.get("lever").frame(page)
        self.assertEqual(adapters.get("lever").submit_button(frame).inner_text().strip(), "Submit application")
        self.assertIsNone(adapters.get("lever").next_button(frame))
        self.assertTrue(page.url.endswith("lever_like.html"))   # still on the form: nothing was submitted


@unittest.skipUnless(HAS_PLAYWRIGHT, "Playwright not installed")
class TestResultRecord(unittest.TestCase):
    """After an application, every question, its answer and who answered it is saved."""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def test_record_and_report(self):
        from jobfill import report
        from jobfill.apply.runner import fill_frame
        store = fresh_store(with_profile=True, owner=self)
        app = store.start_application("Globex", "Data Engineer", ats="lever")
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.goto((FIXTURES / "lever_like.html").as_uri())
        lever = adapters.get("lever")
        fill_frame(lever.frame(page), store, app, lever, overwrite=True, files=False)

        rows = {r["label"]: r for r in store.answers(app)}
        self.assertEqual((rows["Full name✱"]["status"], rows["Full name✱"]["fill"]), ("filled", "ok"))
        self.assertEqual(rows["Full name✱"]["source"], "template")
        self.assertEqual(rows["Why do you want to work at Globex?"]["status"], "needs_ai")
        self.assertEqual(rows["Current company"]["status"], "skipped")
        self.assertTrue(rows["Resume/CV ✱"]["fill"].startswith("skipped"))

        # The agent records its written answer; a later fill must not wipe it.
        why = "I want to build Globex's data platform because ..."
        store.record_answer(app, rows["Why do you want to work at Globex?"]["field_key"],
                            "Why do you want to work at Globex?", why, question_id="why_company", source="ai")
        fill_frame(lever.frame(page), store, app, lever, overwrite=False, files=False)
        rows = {r["label"]: r for r in store.answers(app)}
        self.assertEqual((rows["Why do you want to work at Globex?"]["source"],
                          rows["Why do you want to work at Globex?"]["value"]), ("ai", why))
        self.assertEqual(rows["Why do you want to work at Globex?"]["fill"], "ok")   # typed on the second pass

        rep = report.build(store, app)
        s = rep["summary"]
        self.assertEqual(s["ai"], 1)
        self.assertGreaterEqual(s["profile"], 10)
        self.assertEqual(s["questions"], len(rep["fields"]))
        self.assertGreater(s["python_share"], 80)

        out = temp_dir(self)
        paths = report.write(store, out, app)
        md = Path(paths["markdown"]).read_text(encoding="utf-8")
        self.assertTrue(Path(paths["markdown"]).name.startswith(f"app{app}_globex_data-engineer"))
        self.assertIn("| Your profile (plain Python, no AI) |", md)
        self.assertIn("| Full name✱ | Jordan Rivera | your profile (plain Python, no AI) | typed by jobfill |", md)
        self.assertTrue(Path(paths["json"]).exists())
        self.assertEqual(report.stats(store)["applications"], 1)


if __name__ == "__main__":
    unittest.main()
