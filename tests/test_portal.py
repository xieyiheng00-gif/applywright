"""The setup portal: every fact has a place, saves go through the store, and the server stays local."""

import http.client
import importlib.util
import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from jobfill import db
from jobfill.catalog import CHOICE_SETS, FACTS, RECORD_KINDS
from jobfill.portal import form, layout
from jobfill.portal.server import PortalServer
from jobfill.store import RECORD_SETUP, Store

from .helpers import fresh_store

HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None


def layout_items(type_):
    return [i for p in layout.PAGES for sec in p["sections"] for i in sec["items"] if i["type"] == type_]


class TestLayout(unittest.TestCase):
    def test_every_built_in_fact_is_on_exactly_one_page(self):
        placed = [i["key"] for i in layout_items("fact")]
        self.assertEqual(sorted(placed), sorted(set(placed)), "a fact is placed twice")
        self.assertEqual(set(placed), {f["key"] for f in FACTS})

    def test_record_blocks_use_real_fields(self):
        kinds = [b["record"] for b in layout_items("records")]
        self.assertEqual(sorted(kinds), sorted(set(kinds)))
        self.assertTrue(set(RECORD_SETUP) <= set(kinds) <= set(RECORD_KINDS))
        for b in layout_items("records"):
            names = [f["name"] for f in b["fields"]]
            self.assertTrue(set(names) <= set(RECORD_KINDS[b["record"]]["fields"]), b["record"])
            self.assertIn(RECORD_KINDS[b["record"]]["key"][0], names, b["record"])
            self.assertTrue(set(b["summary"]) <= set(names), b["record"])
            for f in b["fields"]:
                if isinstance(f["options"], str):
                    self.assertIn(f["options"], CHOICE_SETS)

    def test_choice_labels_name_real_choices(self):
        for set_name, labels in layout.CHOICE_LABELS.items():
            self.assertTrue(set(labels) <= set(CHOICE_SETS[set_name]["values"]), set_name)

    def test_required_marks_follow_the_catalog(self):
        sch = form.schema(fresh_store(owner=self))
        required = {f["key"] for f in form._items(sch, "fact") if f["required"]}
        self.assertEqual(required, {f["key"] for f in FACTS if f["setup"] == "required"})
        blocks = {b["record"] for b in form._items(sch, "records") if b["required"]}
        self.assertEqual(blocks, {k for k, lvl in RECORD_SETUP.items() if lvl == "required"})

    def test_widgets_follow_fact_types(self):
        fields = {f["key"]: f for f in form._items(form.schema(fresh_store(owner=self)), "fact")}
        self.assertEqual(fields["eeo.race"]["widget"], "checkboxes")
        self.assertEqual(fields["eeo.gender"]["widget"], "select")
        self.assertEqual(fields["eeo.gender"]["options"][-1], {"value": "decline", "label": "I don't wish to answer"})
        self.assertEqual(fields["work_auth.requires_sponsorship_now"]["widget"], "yesno")
        self.assertEqual(fields["documents.resume_path"]["widget"], "file")
        self.assertEqual(fields["profile.summary"]["widget"], "textarea")

    def test_learned_facts_get_a_place(self):
        store = fresh_store(owner=self)
        store.define_fact("custom.shirt_size", 2, "string", "T-shirt size", "Which T-shirt size do you wear?")
        page = next(p for p in form.schema(store)["pages"] if p["id"] == layout.EXTRA_PAGE)
        extra = page["sections"][-1]
        self.assertEqual(extra["title"], "More Questions")
        self.assertEqual([(f["key"], f["label"], f["widget"]) for f in extra["items"]],
                         [("custom.shirt_size", "T-shirt size", "text")])


class TestSave(unittest.TestCase):
    def setUp(self):
        self.store = fresh_store(with_profile=True, owner=self)
        self.root = Path(tempfile.mkdtemp())

    def page(self):
        st = form.state(self.store, self.root)
        return {"facts": st["values"], "records": st["records"]}

    def save(self, sub, **kw):
        return form.save(self.store, self.root, sub, **kw)

    def test_stored_profile_round_trips_unchanged(self):
        st = form.state(self.store, self.root)["values"]
        self.assertEqual(st["availability.earliest_start"], "06/01/2027")
        self.assertEqual(st["prefs.preferred_locations"], "San Francisco, CA\nSeattle, WA\nNew York, NY")
        self.assertEqual(st["work_auth.requires_sponsorship_future"], "yes")
        self.assertEqual(st["legal.relatives_at_companies"], [])      # "None", not blank
        self.assertEqual(self.save(self.page())["changed"], {"facts": 0, "records": 0})

    def test_answers_are_stored_canonically(self):
        sub = self.page()
        sub["facts"].update({"eeo.gender": "female", "eeo.race": ["asian", "white"],
                             "work_auth.requires_sponsorship_now": "yes", "comp.salary_min_annual": "$120k",
                             "availability.earliest_start": "08/2027", "prefs.desired_titles": "SWE, Infra\nML Engineer",
                             "legal.relatives_at_companies": "Acme Robotics"})
        self.assertTrue(self.save(sub)["ok"])
        get = self.store.get_fact
        self.assertEqual((get("eeo.gender"), get("eeo.race")), ("female", ["asian", "white"]))
        self.assertIs(get("work_auth.requires_sponsorship_now"), True)
        self.assertEqual(get("comp.salary_min_annual"), 120000)
        self.assertEqual(get("availability.earliest_start"), "2027-08")
        self.assertEqual(get("prefs.desired_titles"), ["SWE, Infra", "ML Engineer"])   # one per line keeps commas
        self.assertEqual(get("legal.relatives_at_companies"), ["Acme Robotics"])
        self.assertEqual(self.store.conn.execute(
            "SELECT source FROM facts WHERE key='eeo.gender'").fetchone()[0], "portal")

    def test_blank_clears_and_absent_keys_stay(self):
        self.assertTrue(self.save({"facts": {"contact.github": "", "eeo.race": []}})["ok"])
        self.assertIsNone(self.store.get_fact("contact.github"))
        self.assertIsNone(self.store.get_fact("eeo.race"))
        self.assertEqual(self.store.get_fact("contact.email"), "jordan.rivera@example.com")
        self.assertEqual(len(self.store.records("education")), 2)

    def test_one_bad_value_writes_nothing(self):
        res = self.save({"facts": {"contact.email": "not-an-email", "contact.website": "https://jo.example.com",
                                   "availability.earliest_start": "someday"},
                         "records": {"skill": [{"name": "Rust", "years": "a few"}]}})
        self.assertFalse(res["ok"])
        self.assertEqual(set(res["errors"]), {"fact:contact.email", "fact:availability.earliest_start",
                                              "record:skill:0:years"})
        self.assertTrue(res["errors"]["fact:contact.email"].startswith("Does not look like an email"))
        self.assertIsNone(self.store.get_fact("contact.website"))
        self.assertEqual(len(self.store.records("skill")), 5)

    def test_submit_requires_what_setup_requires(self):
        empty = fresh_store(owner=self)
        res = form.save(empty, self.root, {"facts": {"identity.first_name": "Sam"}}, submit=True)
        self.assertFalse(res["ok"])
        missing = {k for k in res["errors"] if res["errors"][k] == form.REQUIRED}
        self.assertEqual(missing, {"fact:" + f["key"] for f in FACTS if f["setup"] == "required"}
                         - {"fact:identity.first_name"})
        self.assertIn("record:education", res["errors"])
        self.assertIn("record:language", res["errors"])
        self.assertIsNone(empty.get_fact("identity.first_name"))
        draft = form.save(empty, self.root, {"facts": {"identity.first_name": "Sam"}})
        self.assertTrue(draft["ok"])
        self.assertEqual(empty.get_fact("identity.first_name"), "Sam")

    def test_submit_completes_setup_and_defaults_the_login_email(self):
        res = self.save(self.page(), submit=True)
        self.assertTrue(res["ok"])
        self.assertTrue(res["state"]["status"]["setup_complete"])
        self.assertEqual(self.store.get_fact("account.login_email"), "jordan.rivera@example.com")

    def test_records_are_added_updated_and_removed(self):
        rid = self.store.records("experience")[0]["_id"]
        self.store.update_record(rid, {"team": "ML platform"})        # a field the page does not show
        sub = self.page()
        exp = sub["records"]["experience"][0]
        exp["location"] = "Remote"
        exp["highlights"] += "\nOnboarded two new interns"
        sub["records"]["experience"].append({"title": "Research Assistant", "company": "UW-Madison",
                                             "start_date": "Sep 2025", "current": True})
        sub["records"]["experience"].append({"title": "", "company": ""})   # an empty card
        sub["records"]["skill"] = [s for s in sub["records"]["skill"] if s["name"] != "Go"]
        sub["removed"] = {"skill": [s["_id"] for s in self.store.records("skill") if s["name"] == "Go"]}
        self.assertTrue(self.save(sub)["ok"])
        jobs = {r["title"]: r for r in self.store.records("experience")}
        self.assertEqual(set(jobs), {"Data Engineering Intern", "Research Assistant"})
        intern = jobs["Data Engineering Intern"]
        self.assertEqual((intern["location"], intern["team"]), ("Remote", "ML platform"))
        self.assertEqual(len(intern["highlights"]), 3)
        self.assertEqual((jobs["Research Assistant"]["start_date"], jobs["Research Assistant"]["current"]),
                         ("2025-09", True))
        self.assertNotIn("Go", {s["name"] for s in self.store.records("skill")})
        self.assertEqual(len(self.store.records("education")), 2)

    def test_a_page_left_open_keeps_what_changed_elsewhere(self):
        sub = self.page()                                           # the page as it loaded
        intern = self.store.records("experience")[0]
        self.store.update_record(intern["_id"], {"location": "Remote"})     # then the agent changed things
        self.store.add_record("project", {"name": "Realtime Feature Store"})
        self.store.set_fact("contact.website", "https://jo.example.com")
        sub["facts"] = {"identity.preferred_name": "Jojo"}         # the one answer changed on the page
        sub["records"] = {kind: [{"_id": e["_id"]} for e in entries] for kind, entries in sub["records"].items()}
        self.assertTrue(self.save(sub)["ok"])
        self.assertEqual(self.store.get_fact("identity.preferred_name"), "Jojo")
        self.assertEqual(self.store.get_fact("contact.website"), "https://jo.example.com")
        self.assertEqual(self.store.records("experience")[0]["location"], "Remote")
        self.assertEqual(len(self.store.records("project")), 2)

    def test_emptied_card_is_removed(self):
        sub = self.page()
        sub["records"]["language"][0] = {"_id": sub["records"]["language"][0]["_id"], "name": "", "proficiency": ""}
        self.assertTrue(self.save(sub)["ok"])
        self.assertEqual(len(self.store.records("language")), 1)

    def test_unticked_checkbox_and_cleared_field_leave_the_record(self):
        sub = self.page()
        edu = sub["records"]["education"][0]
        edu["expected"], edu["gpa"] = False, ""
        self.assertTrue(self.save(sub)["ok"])
        stored = self.store.records("education")[0]
        self.assertNotIn("expected", stored)
        self.assertNotIn("gpa", stored)

    def test_entry_without_its_name_is_an_error(self):
        res = self.save({"records": {"project": [{"name": "", "summary": "A thing"}]}})
        self.assertEqual(res["errors"], {"record:project:0:name": form.REQUIRED})
        self.assertEqual(len(self.store.records("project")), 1)

    def test_required_entry_fields_are_checked_on_submit(self):
        sub = self.page()
        sub["records"]["language"].append({"name": "French", "proficiency": ""})
        self.assertTrue(self.save(sub)["ok"])                      # a draft may be incomplete
        sub = self.page()
        i = [e["name"] for e in sub["records"]["language"]].index("French")
        res = self.save(sub, submit=True)
        self.assertEqual(res["errors"], {f"record:language:{i}:proficiency": form.REQUIRED})


class TestUpload(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_files_land_in_documents_and_are_never_overwritten(self):
        path = form.save_upload(self.root, "documents.resume_path", "My CV.pdf", b"%PDF one")
        self.assertEqual(path, "documents/resume/My CV.pdf")
        self.assertEqual(form.save_upload(self.root, "documents.resume_path", "My CV.pdf", b"%PDF one"), path)
        self.assertEqual(form.save_upload(self.root, "documents.resume_path", "My CV.pdf", b"%PDF two"),
                         "documents/resume/My CV (2).pdf")
        self.assertEqual((self.root / path).read_bytes(), b"%PDF one")
        self.assertEqual(form.save_upload(self.root, "documents.transcript_path", "..\\..\\t.pdf", b"x"),
                         "documents/transcripts/t.pdf")

    def test_rejects_other_files(self):
        for key, name, data in (("documents.resume_path", "cv.exe", b"x"), ("contact.email", "cv.pdf", b"x"),
                                ("documents.resume_path", "cv.pdf", b"")):
            with self.assertRaises(form.ValidationError):
                form.save_upload(self.root, key, name, data)


class PortalThread:
    """A portal on a free port, serving a database file from its own thread (SQLite needs that)."""

    def __init__(self, with_profile=False, *, restart_of=None):
        """``restart_of``: another PortalThread whose database and port this one takes over."""
        self.dir = restart_of.dir if restart_of else Path(tempfile.mkdtemp())
        self.db_path = self.dir / "jobfill.db"
        self.port = restart_of.server.port if restart_of else 0
        if restart_of is None:
            conn = db.connect(self.db_path)
            if with_profile:
                Store(conn).import_patch(json.loads((Path(__file__).resolve().parent.parent / "examples" /
                                                     "sample_profile.json").read_text(encoding="utf-8")), dry_run=False)
            conn.close()
        self.events, self.ready = [], threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.ready.wait(10)

    def _run(self):
        conn = db.connect(self.db_path)
        self.server = PortalServer(Store(conn), self.dir, self.port, database="data/test.db",
                                   on_event=self.events.append)
        self.ready.set()
        try:
            self.server.serve_forever(poll_interval=0.05)
        finally:
            self.server.server_close()
            conn.close()

    def stop(self):
        if self.thread.is_alive():
            self.server.shutdown()
        self.thread.join(5)

    def store(self) -> Store:
        return Store(db.connect(self.db_path))


class TestServer(unittest.TestCase):
    def setUp(self):
        self.portal = PortalThread()
        self.addCleanup(self.portal.stop)
        self.base = f"http://127.0.0.1:{self.portal.server.port}"
        self.token = self.portal.server.token

    def request(self, path, body=None, token=True, headers=None):
        req = urllib.request.Request(self.base + path, data=body, method="POST" if body is not None else "GET",
                                     headers={**({"X-Jobfill-Token": self.token} if token else {}), **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read()

    def test_page_is_served_and_the_api_needs_the_key(self):
        status, page = self.request("/", token=False)
        self.assertEqual(status, 200)
        self.assertIn(b"portal.js", page)
        self.assertEqual(self.request("/api/form", token=False)[0], 403)
        status, body = self.request("/api/form")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["schema"]["pages"][0]["title"], "My Information")
        self.assertEqual(json.loads(body)["database"], "data/test.db")
        self.assertIn(f"?t={self.token}", self.portal.server.url)

    def test_the_key_belongs_to_the_database(self):
        store, other = fresh_store(owner=self), fresh_store(owner=self)
        keys = []
        for st in (store, store, other):
            server = PortalServer(st, Path(tempfile.mkdtemp()), 0)
            keys.append(server.token)
            server.server_close()
        self.assertEqual(keys[0], keys[1])      # a restarted portal accepts the tab left open
        self.assertNotEqual(keys[0], keys[2])   # a tab for another database is refused

    def test_other_hosts_are_refused(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.portal.server.port, timeout=10)
        conn.request("GET", "/api/form", headers={"Host": "attacker.example:80", "X-Jobfill-Token": self.token})
        self.assertEqual(conn.getresponse().status, 403)
        conn.close()

    def test_save_upload_and_submit(self):
        status, body = self.request("/api/save", json.dumps({"facts": {"identity.first_name": "Sam"}}).encode())
        self.assertEqual((status, json.loads(body)["ok"]), (200, True))
        status, body = self.request("/api/upload?key=documents.resume_path", b"%PDF-1.4",
                                    headers={"X-Filename": "Sam%20Lee%20CV.pdf"})
        self.assertEqual(json.loads(body), {"path": "documents/resume/Sam Lee CV.pdf"})
        self.assertTrue((self.portal.dir / "documents" / "resume" / "Sam Lee CV.pdf").is_file())
        status, body = self.request("/api/save", json.dumps({"facts": {}, "submit": True}).encode())
        self.assertFalse(json.loads(body)["ok"])                  # required answers missing
        self.assertTrue(self.portal.thread.is_alive())
        store = self.portal.store()
        self.addCleanup(store.conn.close)
        self.assertEqual(store.get_fact("identity.first_name"), "Sam")
        self.assertEqual([e for e in self.portal.events if "attached" in e],
                         [{"attached": "documents.resume_path", "file": "documents/resume/Sam Lee CV.pdf"}])

    def test_submit_stops_the_server(self):
        self.portal.stop()
        portal = PortalThread(with_profile=True)
        self.addCleanup(portal.stop)
        req = urllib.request.Request(f"http://127.0.0.1:{portal.server.port}/api/save", method="POST",
                                     data=json.dumps({"facts": {}, "submit": True}).encode(),
                                     headers={"X-Jobfill-Token": portal.server.token})
        with urllib.request.urlopen(req, timeout=10) as res:
            self.assertTrue(json.loads(res.read())["ok"])
        portal.thread.join(5)
        self.assertFalse(portal.thread.is_alive())
        self.assertEqual(portal.server.submitted["setup_complete"], True)
        self.assertEqual(portal.events[-1]["submitted"], True)


@unittest.skipUnless(HAS_PLAYWRIGHT, "Playwright not installed")
class TestPortalPage(unittest.TestCase):
    """The page itself: required marks, stopping on a page with missing answers, saving, submitting."""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def open(self, with_profile=False):
        portal = PortalThread(with_profile)
        self.addCleanup(portal.stop)
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.on("pageerror", lambda e: self.fail(f"page error: {e}"))
        page.goto(portal.server.url)
        page.wait_for_selector("h2:text('My Information')")
        return portal, page

    def test_required_questions_stop_the_page_until_answered(self):
        portal, page = self.open()
        self.assertGreater(page.locator(".field .req").count(), 5)
        page.click("text=Save and Continue")
        page.wait_for_selector(".banner")
        self.assertIn("Errors Found", page.inner_text(".banner"))
        self.assertIn("This field is required.", page.inner_text("[data-err='fact:identity.first_name']"))
        for key, value in (("identity.first_name", "Sam"), ("identity.last_name", "Lee"),
                           ("contact.email", "sam@example.com"), ("contact.phone", "608-555-0100"),
                           ("address.city", "Madison"), ("address.state", "WI"), ("address.postal_code", "53703"),
                           ("address.country", "United States")):
            page.fill("#f-" + key.replace(".", "-"), value)
        page.check("#f-identity-pronouns")           # first pronoun box
        page.click("text=Save and Continue")
        page.wait_for_selector("h2:text('Work Authorization')")
        store = portal.store()
        self.addCleanup(store.conn.close)
        self.assertEqual(store.get_fact("identity.first_name"), "Sam")
        self.assertEqual(store.get_fact("identity.pronouns"), ["he_him"])

    def test_records_and_review(self):
        portal, page = self.open()
        page.click("nav.stepper button[title='Education']")
        page.wait_for_selector("h2:text('Education')")
        page.click("text=Add Education")
        page.fill("#r-education-0-school", "University of Wisconsin-Madison")
        page.fill("#r-education-0-degree", "Bachelor of Science")
        page.select_option("#r-education-0-degree_level", "bachelor")
        page.fill("#r-education-0-field_of_study", "Statistics")
        page.fill("#r-education-0-end_date", "05/2026")
        page.click("text=Save for Later")
        page.wait_for_selector(".save-state:text('Saved at')")
        store = portal.store()
        self.addCleanup(store.conn.close)
        self.assertEqual([(e["school"], e["end_date"]) for e in store.records("education")],
                         [("University of Wisconsin-Madison", "2026-05")])
        page.click("nav.stepper button[title='Review']")
        page.wait_for_selector("h2:text('Review')")
        self.assertIn("Bachelor of Science", page.inner_text(".card"))
        page.click("button:text('Submit')")
        page.wait_for_selector(".banner")
        self.assertIn("First Name: This field is required.", page.inner_text(".banner"))
        self.assertTrue(portal.thread.is_alive())

    def test_a_stopped_portal_is_explained_and_the_tab_saves_after_a_restart(self):
        portal, page = self.open(with_profile=True)
        portal.stop()
        page.fill("#f-identity-preferred_name", "Jojo")
        page.click("text=Save for Later")
        page.wait_for_selector("#problem")
        self.assertIn("Not saved: jobfill is not running", page.inner_text("#problem"))
        self.assertEqual(page.input_value("#f-identity-preferred_name"), "Jojo")    # nothing typed is lost
        again = PortalThread(restart_of=portal)
        self.addCleanup(again.stop)
        page.click("text=Save for Later")
        page.wait_for_selector(".save-state:text('Saved at')")
        self.assertEqual(page.locator("#problem").count(), 0)
        store = again.store()
        self.addCleanup(store.conn.close)
        self.assertEqual(store.get_fact("identity.preferred_name"), "Jojo")
        self.assertEqual(store.get_fact("identity.first_name"), "Jordan")

    def test_submitting_a_complete_profile(self):
        portal, page = self.open(with_profile=True)
        self.assertEqual(page.input_value("#f-identity-first_name"), "Jordan")
        page.click("nav.stepper button[title='Review']")
        page.wait_for_selector("h2:text('Review')")
        page.click("button:text('Submit')")
        page.wait_for_selector("h2:text('Profile submitted')")
        portal.thread.join(5)
        self.assertFalse(portal.thread.is_alive())
        self.assertTrue(portal.server.submitted["setup_complete"])


if __name__ == "__main__":
    unittest.main()
