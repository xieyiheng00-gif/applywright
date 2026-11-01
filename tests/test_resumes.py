import sqlite3
import tempfile
import unittest
from pathlib import Path

from jobfill import db, resumes
from jobfill.resolver import Resolver
from jobfill.store import Store

from tests.helpers import TODAY, fresh_store

CVS = ["main_acme_labs_research_engineer_pre_training.pdf",
       "main_delta_ai_software_engineer_gpu_kernels.pdf",
       "main_delta_ai_software_engineer_inference_systems.pdf",
       "main_orbit_research_engineer_intern_benchmarking.pdf"]


class TestTailoredResume(unittest.TestCase):
    """A CV is picked only when its file name names the company and clearly matches the role."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        folder = self.root / "documents" / "resume" / "tailored cv"
        folder.mkdir(parents=True)
        for name in CVS:
            (folder / name).write_bytes(b"%PDF-1.4 test")
        (self.root / "documents" / "resume" / "jordan_rivera_resume.pdf").write_bytes(b"%PDF-1.4 test")
        self.store = fresh_store(with_profile=True, owner=self)   # default: documents/resume/jordan_rivera_resume.pdf

    def pick(self, company, role, explicit=None):
        return resumes.choose(self.store, self.root, company, role, explicit)

    def test_company_and_role_pick_the_tailored_cv(self):
        for company in ("Acme Labs", "AcmeLabs", "Acme Labs, Inc."):
            out = self.pick(company, "Research Engineer - Pre-training")
            self.assertEqual(out["file"], "documents/resume/tailored cv/" + CVS[0], company)

    def test_the_role_decides_between_cvs_for_one_company(self):
        out = self.pick("Delta AI", "Software Engineer, GPU Kernels")
        self.assertEqual(out["file"], "documents/resume/tailored cv/" + CVS[1])
        self.assertEqual(out["other_cvs_for_company"], ["documents/resume/tailored cv/" + CVS[2]])

    def test_a_tie_is_not_picked_but_listed(self):
        out = self.pick("Delta AI", "Software Engineer")
        self.assertIsNone(out["file"])
        self.assertEqual(len(out["cvs_for_company"]), 2)
        self.assertEqual(out["default"], "documents/resume/jordan_rivera_resume.pdf")

    def test_another_company_never_gets_a_cv(self):
        for company in ("Globex", "Orbital", "Acme Robotics", "Delta"):   # role words match, company does not
            out = self.pick(company, "Research Engineer")
            self.assertIsNone(out["file"], company)
            self.assertNotIn("cvs_for_company", out, company)

    def test_the_default_resume_is_never_a_tailored_candidate(self):
        self.store.set_fact("documents.resume_path", "documents/resume/tailored cv/" + CVS[0])
        self.assertIsNone(self.pick("Acme Labs", "Research Engineer - Pre-training")["file"])

    def test_explicit_resume_wins_and_must_exist(self):
        out = self.pick("Acme Labs", "Research Engineer - Pre-training",
                        "documents/resume/tailored cv/" + CVS[3])
        self.assertEqual((out["file"], out["why"]), ("documents/resume/tailored cv/" + CVS[3], "given with --resume"))
        with self.assertRaises(FileNotFoundError):
            self.pick("Acme Labs", None, "documents/resume/missing.pdf")


class TestResumeQuestion(unittest.TestCase):
    """The resume field uploads the application's own CV, else the default resume."""

    def setUp(self):
        self.store = fresh_store(with_profile=True, owner=self)

    def value(self, app_id):
        out = Resolver(self.store, app_id, today=TODAY).resolve([{"id": "r", "label": "Resume/CV", "type": "file"}])
        return out["fields"][0]["value"]

    def test_application_resume_then_default(self):
        tailored = self.store.start_application("Acme Labs", "Research Engineer", resume="cv/acme_labs.pdf")
        plain = self.store.start_application("Acme", "Engineer")
        self.assertEqual(self.value(tailored), "cv/acme_labs.pdf")
        self.assertEqual(self.value(plain), "documents/resume/jordan_rivera_resume.pdf")
        self.store.update_application(plain, resume="cv/acme.pdf")
        self.assertEqual(self.value(plain), "cv/acme.pdf")


class TestMigration(unittest.TestCase):
    def test_older_database_gets_the_resume_column(self):
        path = Path(tempfile.mkdtemp()) / "old.db"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE applications (id INTEGER PRIMARY KEY, company TEXT NOT NULL, role TEXT, url TEXT, "
                     "ats TEXT, country TEXT, source TEXT, referrer TEXT, posting TEXT, notes TEXT, "
                     "status TEXT NOT NULL DEFAULT 'in_progress', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.commit()
        conn.close()
        store = Store(db.connect(str(path)))
        self.addCleanup(store.conn.close)
        app_id = store.start_application("Acme", "Engineer", resume="cv/acme.pdf")
        self.assertEqual(store.application(app_id)["resume"], "cv/acme.pdf")


if __name__ == "__main__":
    unittest.main()
