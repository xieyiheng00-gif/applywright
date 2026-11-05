import json
import unittest

from jobfill import db
from jobfill.store import ValidationError

from .helpers import EXAMPLES, fresh_store, temp_dir


class TestImport(unittest.TestCase):
    def setUp(self):
        self.store = fresh_store(owner=self)
        self.patch = json.loads((EXAMPLES / "sample_profile.json").read_text(encoding="utf-8"))

    def test_dry_run_writes_nothing_then_apply_then_idempotent(self):
        report = self.store.import_patch(self.patch, dry_run=True)
        self.assertGreater(len(report["additive"]), 50)
        self.assertEqual(report["errors"], [])
        self.assertEqual(self.store.facts(), {})
        self.store.import_patch(self.patch, dry_run=False)
        again = self.store.import_patch(self.patch, dry_run=True)
        self.assertEqual((again["additive"], again["conflicts"]), ([], []))

    def test_conflicts_need_explicit_replace(self):
        self.store.import_patch(self.patch, dry_run=False)
        update = {"facts": {"contact.phone": "608-555-9999", "contact.website": "https://jo.example.com"},
                  "records": {"experience": [{"company": "Example Analytics", "title": "Data Engineering Intern",
                                              "start_date": "2026-06", "location": "Remote",
                                              "may_contact": True}]}}
        report = self.store.import_patch(update, dry_run=False)
        ids = {c["id"] for c in report["conflicts"]}
        self.assertIn("fact:contact.phone", ids)
        self.assertEqual(self.store.get_fact("contact.phone"), "608-555-0142")      # conflict not applied
        self.assertEqual(self.store.get_fact("contact.website"), "https://jo.example.com")  # additive applied
        exp = self.store.records("experience")[0]
        self.assertTrue(exp["may_contact"])                                          # new field merged
        self.assertEqual(exp["location"], "Chicago, IL")                             # changed field held back
        loc_id = next(i for i in ids if i.endswith(".location"))
        self.store.import_patch(update, dry_run=False, replace={"fact:contact.phone", loc_id})
        self.assertEqual(self.store.get_fact("contact.phone"), "608-555-9999")
        self.assertEqual(self.store.records("experience")[0]["location"], "Remote")

    def test_new_project_is_added_not_merged(self):
        self.store.import_patch(self.patch, dry_run=False)
        self.store.import_patch({"records": {"project": [{"name": "Realtime Feature Store", "summary": "x"}]}},
                                dry_run=False)
        self.assertEqual(len(self.store.records("project")), 2)


class TestFacts(unittest.TestCase):
    def setUp(self):
        self.store = fresh_store(owner=self)

    def test_coercion(self):
        s = self.store
        self.assertIs(s.set_fact("legal.is_18_plus", "Yes"), True)
        self.assertEqual(s.set_fact("comp.salary_min_annual", "$120k"), 120000)
        self.assertEqual(s.set_fact("eeo.gender", "Female"), "female")
        self.assertEqual(s.set_fact("eeo.veteran_status", "I am not a protected veteran"), "not_veteran")
        self.assertEqual(s.set_fact("eeo.race", "Asian, White"), ["asian", "white"])
        self.assertEqual(s.set_fact("work_auth.us_status", "H-1B"), "h1b")
        self.assertEqual(s.set_fact("work_auth.authorized_countries", "United States, Canada"),
                         ["United States", "Canada"])
        with self.assertRaises(ValidationError):
            s.set_fact("eeo.gender", "banana")
        with self.assertRaises(ValidationError):
            s.set_fact("availability.earliest_start", "next week")
        with self.assertRaises(ValidationError):
            s.set_fact("no.such_fact", "x")

    def test_history_is_kept(self):
        self.store.set_fact("contact.email", "a@example.com")
        self.store.set_fact("contact.email", "b@example.com")
        rows = self.store.conn.execute("SELECT old_value, new_value FROM fact_history WHERE key='contact.email'").fetchall()
        self.assertEqual([json.loads(r["new_value"]) for r in rows], ["a@example.com", "b@example.com"])

    def test_tier3_facts_are_rejected(self):
        with self.assertRaises(ValidationError):
            self.store.define_fact("custom.why_us", 3, "text", "Why us", "Why us?")

    def test_status_and_missing(self):
        st = self.store.status()
        self.assertFalse(st["setup_complete"])
        self.assertIn("identity.first_name", st["missing_required"])
        prompts = {f["key"]: f for f in self.store.missing(tier=1, level="required")["facts"]}
        self.assertTrue(prompts["eeo.gender"]["sensitive"])
        self.assertIn("decline", prompts["eeo.gender"]["choices"])
        full = fresh_store(with_profile=True, owner=self)
        self.assertTrue(full.status()["setup_complete"])

    def test_stale_tier2_facts_are_flagged(self):
        self.store.set_fact("comp.salary_min_annual", 100000)
        self.store.conn.execute("UPDATE facts SET updated_at='2020-01-01T00:00:00+00:00'")
        self.assertIn("comp.salary_min_annual", self.store.status()["stale"])


class TestMigration(unittest.TestCase):
    def test_old_answers_table_gets_new_columns(self):
        import sqlite3
        path = temp_dir(self) / "old.db"
        old = sqlite3.connect(path)
        old.execute("""CREATE TABLE answers (id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL,
                       field_key TEXT NOT NULL, question_id TEXT, label TEXT NOT NULL, value TEXT NOT NULL,
                       source TEXT NOT NULL, tier INTEGER, created_at TEXT NOT NULL,
                       UNIQUE (application_id, field_key))""")
        old.commit()
        old.close()
        conn = db.connect(path)
        self.addCleanup(conn.close)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(answers)")}
        self.assertTrue({"status", "fill", "section", "idx", "attempts", "attempt_value"} <= cols)


class TestSeed(unittest.TestCase):
    def test_reseed_keeps_learned_rows(self):
        store = fresh_store(owner=self)
        store.add_alias("why_company", "what pulls you toward {company}")
        store.define_fact("prefs.tshirt_size", 2, "string", "T-shirt size", "Your T-shirt size")
        db.seed(store.conn)
        aliases = {a["text"] for a in store.aliases() if a["question_id"] == "why_company"}
        self.assertIn("what pulls you toward {company}", aliases)
        store._defs = None
        self.assertIn("prefs.tshirt_size", store.fact_defs())


if __name__ == "__main__":
    unittest.main()
