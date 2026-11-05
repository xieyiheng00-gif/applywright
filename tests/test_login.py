"""Portal accounts: secret, derived passwords, login flow, mail connector, terminal-only commands."""

import importlib.util
import subprocess
import sys
import unittest
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote

from jobfill import accounts
from jobfill.mail import extract

from .helpers import fresh_store, temp_dir

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ROOT = Path(__file__).resolve().parent.parent
HOST = "acme.wd5.myworkdayjobs.com"


class TestPasswords(unittest.TestCase):
    def test_derived_password(self):
        pw = accounts.derive_password("s3cret", "Acme")
        self.assertEqual(pw, accounts.derive_password("s3cret", "Acme"))               # deterministic
        self.assertEqual(pw, accounts.derive_password("s3cret", "ACME, Inc."))         # same company
        self.assertNotEqual(pw, accounts.derive_password("s3cret", "Globex"))
        self.assertNotEqual(pw, accounts.derive_password("s3cret", "Acme", version=2))  # after a reset
        self.assertNotEqual(pw, accounts.derive_password("other", "Acme"))
        self.assertEqual(len(pw), 16)
        for chars in (accounts.UPPER, accounts.LOWER, accounts.DIGITS, accounts.SYMBOLS):
            self.assertTrue(any(c in chars for c in pw), pw)

    def test_secret_file_is_created_once(self):
        folder = temp_dir(self)
        first = accounts.secret(folder)
        self.assertEqual(len(first), 64)
        self.assertEqual(accounts.secret(folder), first)                                # never overwritten
        text = (folder / "secret.env").read_text(encoding="utf-8")
        self.assertIn("# JOBFILL_IMAP_PASSWORD=", text)                                  # connector stays off

    def test_workday_accounts_are_keyed_by_company(self):
        key = accounts.account_key
        self.assertEqual(key("https://visa.wd5.myworkdayjobs.com/en-US/Jobs/job/X_R1"), "visa.workday")
        self.assertEqual(key("https://wd5.myworkdaysite.com/en-US/recruiting/visa/Jobs/job/X_R1"), "visa.workday")
        self.assertEqual(key("visa.wd1.myworkdayjobs.com"), "visa.workday")
        self.assertEqual(key("https://www.amazon.jobs/en/jobs/1"), "www.amazon.jobs")
        store = fresh_store(owner=self)   # a row saved under the old, host-style key is still found
        store.upsert_account("visa.wd5.myworkdayjobs.com", "visa", "a@example.com", "active")
        self.assertEqual(accounts.find_account(store, "visa.workday")["host"], "visa.wd5.myworkdayjobs.com")

    def test_login_email_defaults_to_contact_email(self):
        store = fresh_store(with_profile=True, owner=self)
        self.assertEqual(accounts.login_email(store), "jordan.rivera@example.com")
        store.set_fact("account.login_email", "jobs@example.com")
        self.assertEqual(accounts.login_email(store), "jobs@example.com")


class TestMailExtract(unittest.TestCase):
    def message(self, sender, subject, html):
        msg = EmailMessage()
        msg["From"], msg["Subject"] = sender, subject
        msg.set_content("see html")
        msg.add_alternative(html, subtype="html")
        return msg

    def test_code_and_portal_link_only(self):
        msg = self.message("Acme Careers <noreply@myworkday.com>", "Verify your account",
                           '<p>Your verification code is <b>482913</b>.</p>'
                           '<a href="https://evil.example.net/verify?x=1">verify</a>'
                           '<a href="https://acme.wd5.myworkdayjobs.com/verify?token=abc123">Verify</a>')
        found = extract(msg, [HOST], "acme")
        self.assertEqual(found["code"], "482913")
        self.assertEqual(found["link"], "https://acme.wd5.myworkdayjobs.com/verify?token=abc123")

    def test_unrelated_mail_is_ignored(self):
        msg = self.message("Bank <alerts@bank.example>", "Your code", "<p>Your code is 111222</p>")
        self.assertIsNone(extract(msg, [HOST], "acme"))

    def test_digits_inside_links_are_not_codes(self):
        msg = self.message("noreply@acme.wd5.myworkdayjobs.com", "Confirm your email",
                           '<a href="https://acme.wd5.myworkdayjobs.com/confirm/998877">Confirm</a>')
        found = extract(msg, [HOST], "acme")
        self.assertIsNone(found["code"])
        self.assertTrue(found["link"].endswith("/confirm/998877"))


class TestTerminalOnly(unittest.TestCase):
    def run_cli(self, *args):
        db = temp_dir(self) / "t.db"
        return subprocess.run([sys.executable, "-m", "jobfill", "--db", str(db), *args], cwd=ROOT,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)

    def test_login_and_password_refuse_without_a_terminal(self):
        for args in (["login", "--app", "1"], ["account", "password", "--company", "Acme", "--show"]):
            r = self.run_cli(*args)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("only runs when you start it yourself", r.stderr)

    def test_password_hidden_without_show(self):
        r = self.run_cli("account", "password", "--company", "Acme")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--show", r.stdout)


class FakeMail:
    def __init__(self, found):
        self.found, self.calls = found, 0

    def wait_for_verification(self, since, hosts, company, **kw):
        self.calls += 1
        return self.found


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Playwright not installed")
class TestLoginFlow(unittest.TestCase):
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
        self.store.set_fact("consent.account_terms", True)
        self.data = temp_dir(self)
        self.password = accounts.derive_password(accounts.secret(self.data), "Acme")

    def run_login(self, hash_="", mail=None):
        from jobfill.apply.login import login_flow
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.goto((FIXTURES / "auth_like.html").as_uri() + hash_)
        outcome = login_flow(page, page.main_frame, self.store, self.data, HOST, "Acme", mail=mail, wait_ms=200)
        return page, outcome

    def test_new_account_with_emailed_code(self):
        mail = FakeMail({"code": "123456", "link": None})
        page, out = self.run_login(mail=mail)
        self.assertEqual(out.status, "signed_in", out.message)
        self.assertTrue(page.is_visible("#app"))
        self.assertEqual(page.evaluate("() => window.created"), ["jordan.rivera@example.com", self.password])
        self.assertFalse(page.evaluate("() => window.trapFilled"))              # honeypot never touched
        self.assertEqual(self.store.account(HOST)["status"], "active")
        self.assertEqual(mail.calls, 1)

    def test_without_mail_connector_the_candidate_types_the_code(self):
        _, out = self.run_login()
        self.assertEqual(out.status, "needs_verification")
        self.assertIn("jordan.rivera@example.com", out.message)
        # The site asked for a code, so it accepted the sign-up: the next login signs in, not creates.
        self.assertEqual(self.store.account(HOST)["status"], "created")

    def test_rejected_sign_up_is_not_recorded_as_created(self):
        page, out = self.run_login("#reject=1")
        self.assertEqual(out.status, "failed")
        self.assertIn("tries creating it again", out.message)
        self.assertEqual(self.store.account(HOST)["status"], "pending")
        # Next login: still a sign-up (not a sign-in that the site would reject).
        page2, _ = self.run_login("#reject=1")
        self.assertTrue(page2.is_visible("#create"))

    def test_sign_up_confirmed_by_the_sign_in_form(self):
        page, out = self.run_login("#after=signin")
        self.assertEqual(out.status, "signed_in", out.message)
        self.assertEqual(page.evaluate("() => window.created"), ["jordan.rivera@example.com", self.password])
        self.assertEqual(self.store.account(HOST)["status"], "active")

    def test_known_account_signs_in_directly(self):
        self.store.upsert_account(HOST, "acme", "jordan.rivera@example.com", "active")
        page, out = self.run_login("#existing=" + quote(f"jordan.rivera@example.com|{self.password}"))
        self.assertEqual(out.status, "signed_in", out.message)
        self.assertIsNone(page.evaluate("() => window.created"))                # no second account

    def test_known_account_switches_from_create_account_to_sign_in(self):
        # Reopening a portal whose account exists: it opens on "Create Account" (as some tenants do).
        self.store.upsert_account(HOST, "acme", "jordan.rivera@example.com", "active")
        page, out = self.run_login("#start=create&existing=" + quote(f"jordan.rivera@example.com|{self.password}"))
        self.assertEqual(out.status, "signed_in", out.message)
        self.assertTrue(page.is_visible("#app"))
        self.assertIsNone(page.evaluate("() => window.created"))                # no create attempt at all

    def test_account_unknown_here_but_existing_on_the_site(self):
        # Made by jobfill elsewhere (another copy, or the company's other Workday address): the site says the
        # email exists, jobfill switches to sign-in, and the derived password works because it only depends
        # on the company.
        page, out = self.run_login("#start=create&code=none&existing=" +
                                   quote(f"jordan.rivera@example.com|{self.password}"))
        self.assertEqual(out.status, "signed_in", out.message)
        self.assertTrue(page.is_visible("#app"))
        self.assertEqual(self.store.account(HOST)["status"], "active")

    def test_account_recorded_under_the_other_workday_address(self):
        self.store.upsert_account("acme.wd1.myworkdayjobs.com", "acme", "jordan.rivera@example.com", "active")
        from jobfill.apply.login import login_flow
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.goto((FIXTURES / "auth_like.html").as_uri() + "#start=create&existing=" +
                  quote(f"jordan.rivera@example.com|{self.password}"))
        out = login_flow(page, page.main_frame, self.store, self.data, "acme.workday", "Acme", wait_ms=200)
        self.assertEqual(out.status, "signed_in", out.message)
        self.assertIsNone(page.evaluate("() => window.created"))
        self.assertEqual([a["host"] for a in self.store.accounts()], ["acme.wd1.myworkdayjobs.com"])   # no duplicate

    def test_terms_need_standing_consent(self):
        self.store.set_fact("consent.account_terms", False)
        page, out = self.run_login()
        self.assertEqual(out.status, "consent_required")
        self.assertFalse(page.is_checked("#terms"))

    def test_account_made_by_hand_with_another_password(self):
        _, out = self.run_login("#code=none&existing=" + quote("jordan.rivera@example.com|MyOwnPass1!"))
        self.assertEqual(out.status, "failed")
        self.assertIn("rejected the sign-in", out.message)
        self.assertEqual(self.store.account(HOST)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
