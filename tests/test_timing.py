import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from jobfill import cli, db, report, timing
from jobfill.apply import adapters

from .helpers import fresh_store, temp_dir

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REPO = Path(__file__).resolve().parent.parent
HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None
T0 = 1_780_000_000.0   # the made-up log starts here: Windows cannot show times right at 1970


def row(name, started, seconds=0.0, app=None, session="s", **detail):
    return {"session": session, "application_id": app, "name": name, "started": T0 + started, "seconds": seconds,
            "detail": detail}


def book_of(rows):
    """A Book over made-up rows, so the seconds are exact."""
    book = timing.Book.__new__(timing.Book)
    book.rows = rows
    book.segments = timing.account(rows, now=rows[-1]["started"] + 1)
    return book


def spent(rows, who, app="any"):
    return sum(s["seconds"] for s in timing.account(rows, now=rows[-1]["started"] + 1)
               if s["who"] == who and (app == "any" or s["app"] == app))


class TestAccounting(unittest.TestCase):
    def test_a_command_is_jobfill_and_the_silence_before_it_is_the_agent(self):
        info = book_of([row("apply start", 0, 2, app=1), row("apply fill", 12, 8, app=1)]).application(1)
        self.assertEqual((info["python"], info["agent"], info["active"], info["elapsed"]), (10, 10, 20, 20))

    def test_a_batch_does_not_count_the_same_minutes_twice(self):
        # One agent, two applications, their commands interleaved. Adding up each application's own gaps would
        # give the first one 70s of writing and the two together more work than the clock shows.
        rows = [row("apply fill", 0, 10, app=1), row("apply fill", 10, 10, app=2),
                row("answer record", 80, 1, app=1, question="why_company", words=100),
                row("answer record", 111, 1, app=2, question="why_role", words=100)]
        book = book_of(rows)
        one, two = book.application(1), book.application(2)
        self.assertEqual((one["writing"], two["writing"]), (60, 30))
        self.assertEqual(one["active"] + two["active"], 112)   # the clock, exactly
        run = book.runs()[0]
        self.assertEqual((run["applications"], run["active"], run["elapsed"], run["per_application"]),
                         ([1, 2], 112, 112, 56))

    def test_answers_checked_together_share_the_writing_by_length(self):
        rows = [row("apply fill", 0, 10, app=1),
                row("answer check", 110, 0.2, app=1, question="why_company", words=300),
                row("answer check", 110.5, 0.2, app=1, question="why_role", words=100)]
        answers = {a["question"]: a for a in book_of(rows).application(1)["answers"]}
        self.assertEqual(answers["why_company"]["seconds"], 75)
        self.assertEqual(answers["why_company"]["words"], 300)
        self.assertAlmostEqual(answers["why_role"]["seconds"], 25.3, delta=0.2)   # + the moment between the checks

    def test_fixing_an_answer_counts_as_writing_it(self):
        rows = [row("apply fill", 0, 10, app=1),
                row("answer check", 40, 0, app=1, question="why_company", words=100),
                row("answer record", 60, 0, app=1, question="why_company", words=110)]
        answers = book_of(rows).application(1)["answers"]
        self.assertEqual([(a["question"], a["seconds"]) for a in answers], [("why_company", 50)])

    def test_an_answer_the_candidate_wrote_is_not_the_agent_writing(self):
        rows = [row("apply fill", 0, 10, app=1), row("answer record", 40, 0, app=1, question="q", words=9, source="user")]
        self.assertEqual(spent(rows, "writing"), 0)
        self.assertEqual(spent(rows, "agent", app=1), 30)

    def test_classifying_new_questions_belongs_to_the_batch(self):
        rows = [row("apply fill", 0, 10, app=1), row("learn", 50, 0.5), row("pending", 60, 0.5)]
        self.assertEqual(spent(rows, "classifying", app=None), 49.5)
        self.assertEqual(book_of(rows).application(1)["classifying"], 0)
        self.assertEqual(book_of(rows).runs()[0]["shared"], 50.5)   # 49.5 of thinking + the two commands

    def test_turn_marks_separate_the_agent_from_waiting_on_the_candidate(self):
        rows = [row("apply fill", 0, 1, app=1), row("pause", 5), row("resume", 65),
                row("answer record", 70, 0, app=1, question="q", words=9)]
        self.assertEqual(spent(rows, "agent", app=None), 4)      # the agent writing its message
        self.assertEqual(spent(rows, "candidate", app=None), 60)
        self.assertEqual(spent(rows, "writing", app=1), 5)       # the agent again, after the reply
        self.assertEqual(book_of(rows).runs()[0]["candidate"], 60)

    def test_without_a_reply_mark_the_wait_ends_at_the_next_command(self):
        rows = [row("apply fill", 0, 1, app=1), row("pause", 5), row("apply fill", 65, 1, app=1)]
        self.assertEqual(spent(rows, "candidate"), 60)
        self.assertEqual(spent(rows, "agent", app=1), 0)

    def test_login_and_watch_are_the_candidate(self):
        rows = [row("apply fill", 0, 10, app=1, blockers=["login_required"]), row("login", 40, 40, app=1),
                row("apply fill", 100, 10, app=1), row("apply watch", 115, 30, app=1)]
        info = book_of(rows).application(1)
        self.assertEqual(info["candidate"], 30 + 40 + 20 + 30)
        self.assertEqual((info["python"], info["agent"]), (20, 5))
        self.assertEqual(info["first_blocker"], {"blockers": ["login_required"], "after": 10})

    def test_a_long_silence_is_nobody_and_starts_a_new_run(self):
        rows = [row("apply fill", 0, 10, app=1), row("apply fill", 3600, 10, app=1)]
        book = book_of(rows)
        info = book.application(1)
        self.assertEqual((info["python"], info["agent"], info["elapsed"]), (20, 0, 20))
        self.assertEqual(len(book.runs()), 2)

    def test_a_wait_of_hours_is_not_counted_either(self):
        rows = [row("apply fill", 0, 10, app=1), row("pause", 10), row("resume", 7200), row("apply fill", 7205, 1, app=1)]
        self.assertEqual(spent(rows, "candidate"), 0)
        self.assertEqual(spent(rows, "agent", app=1), 5)

    def test_a_killed_command_counts_until_the_next_one_and_says_so(self):
        rows = [row("apply fill", 0, None, app=1), row("apply fill", 120, 5, app=1)]
        segments = timing.account(rows, now=T0 + 200)
        self.assertEqual(segments[0]["seconds"], 120)
        self.assertIn("did not finish", segments[0]["result"])
        self.assertEqual(spent(rows, "agent"), 0)

    def test_the_running_command_counts_until_now(self):
        segment = timing.account([row("apply fill", 0, None, app=1)], now=T0 + 30)[0]
        self.assertEqual(segment["seconds"], 30)
        self.assertEqual(segment["result"], "")

    def test_two_sessions_are_accounted_apart(self):
        rows = [row("apply fill", 0, 1, app=1, session="a"), row("apply fill", 1, 1, app=2, session="b"),
                row("apply fill", 21, 1, app=1, session="a"), row("apply fill", 42, 1, app=2, session="b")]
        self.assertEqual(spent(rows, "agent", app=1), 20)
        self.assertEqual(spent(rows, "agent", app=2), 40)

    def test_commands_running_at_once_leave_no_negative_gap(self):
        rows = [row("apply fill", 0, 30, app=1), row("apply fill", 5, 10, app=2), row("apply fill", 40, 1, app=2)]
        self.assertEqual(spent(rows, "agent"), 10)

    def test_giving_up_says_how_much_work_it_took(self):
        rows = [row("apply start", 0, 2, app=1), row("apply fill", 10, 20, app=1),
                row("app giveup", 90, 0, app=1, why="captcha", reason="Cloudflare at every page")]
        info = book_of(rows).application(1)
        self.assertEqual(info["gave_up"]["active"], 90)
        self.assertEqual((info["gave_up"]["why"], info["gave_up"]["reason"]), ("captcha", "Cloudflare at every page"))
        self.assertIn("Gave up after 1m 30s of work", "\n".join(timing.section(info)))

    def test_work_after_giving_up_is_not_part_of_the_time_before_it(self):
        rows = [row("apply fill", 0, 10, app=1), row("app giveup", 20, 0, app=1, why="other", reason="x"),
                row("app show", 500, 1, app=1)]
        self.assertEqual(book_of(rows).application(1)["gave_up"]["active"], 20)

    def test_where_a_fill_spent_its_time_adds_up_over_runs(self):
        slow = {"label": "School", "kind": "combobox", "seconds": 9}
        rows = [row("apply fill", 0, 20, app=1, phases={"type": 12, "read_form": 3}, slowest=[slow]),
                row("apply fill", 30, 5, app=1, phases={"type": 1, "read_form": 4},
                    slowest=[{"label": "City", "kind": "text", "seconds": 1}])]
        info = book_of(rows).application(1)
        self.assertEqual(info["phases"], {"type": 13, "read_form": 7})
        self.assertEqual(info["slowest_fields"][0], slow)

    def test_the_timeline_shows_every_command_and_only_silences_that_matter(self):
        rows = [row("apply fill", 0, 10, app=1), row("apply set", 10.1, 0.1, app=1),
                row("answer record", 70.2, 0, app=1, question="why_company", words=9)]
        steps = book_of(rows).application(1, timeline=True)["timeline"]
        self.assertEqual([(s["who"], s["what"]) for s in steps],
                         [("python", "apply fill"), ("python", "apply set"), ("writing", "writing why_company"),
                          ("python", "answer record")])
        self.assertEqual((steps[2]["from_start"], steps[2]["seconds"]), (10.2, 60))

    def test_an_application_from_before_timing_has_no_time(self):
        self.assertIsNone(book_of([row("apply fill", 0, 1, app=1)]).application(99))

    def test_durations_read_like_durations(self):
        self.assertEqual([timing.clock(s) for s in (None, 0, 59.4, 60, 754, 3600, 7500)],
                         ["-", "0s", "59s", "1m 00s", "12m 34s", "1h 00m", "2h 05m"])


class TestClock(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"JOBFILL_TIMING": "1"})
        env.start()
        self.addCleanup(env.stop)
        self.store = fresh_store(owner=self)
        self.conn = self.store.conn

    def tearDown(self):
        timing.finish()

    def test_a_row_exists_from_the_start_and_is_closed_at_the_end(self):
        timing.start(self.conn, "apply fill", 7)
        running = timing.load(self.conn)[0]
        self.assertEqual((running["name"], running["application_id"], running["seconds"]), ("apply fill", 7, None))
        timing.note(question="why_company")
        self.assertEqual(timing.load(self.conn)[0]["detail"], {"question": "why_company"})   # at once, not at the end
        timing.finish()
        self.assertIsNotNone(timing.load(self.conn)[0]["seconds"])

    def test_what_a_command_printed_is_kept_short(self):
        timing.start(self.conn, "apply fill", 7)
        timing.result({"app_id": 7, "blockers": ["captcha"], "needs_ai": [{"long": "x" * 999}],
                       "counts": {"filled": 3, "needs_ai": 1, "skipped": 0}, "next": "..."})
        timing.finish()
        self.assertEqual(timing.load(self.conn)[0]["detail"],
                         {"blockers": ["captcha"], "counts": {"filled": 3, "needs_ai": 1}})

    def test_starting_an_application_takes_its_id_from_the_result(self):
        timing.start(self.conn, "apply start")
        timing.result({"app_id": 12, "handler": "python"})
        timing.finish()
        self.assertEqual(timing.load(self.conn)[0]["application_id"], 12)

    def test_only_a_start_takes_an_id_from_the_result(self):
        timing.start(self.conn, "record add")
        timing.result({"id": 3, "kind": "skill"})
        timing.finish()
        self.assertIsNone(timing.load(self.conn)[0]["application_id"])

    def test_phases_keep_the_time_and_the_slow_fields(self):
        timing.start(self.conn, "apply fill", 1)
        with timing.phase("type", "Slow school", "combobox"):
            timing.spent("nothing", 0)
        timing.spent("type", 2.0)
        timing._clock.fields.append({"label": "School", "kind": "combobox", "seconds": 2.0})
        timing.finish()
        d = timing.load(self.conn)[0]["detail"]
        self.assertGreaterEqual(d["phases"]["type"], 2)
        self.assertIn("other", d["phases"])
        self.assertEqual(d["fields_typed"], 2)
        self.assertEqual([f["label"] for f in d["slowest"]], ["School"])   # the instant one is not worth keeping

    def test_a_phase_inside_a_phase_does_not_count_twice(self):
        timing.start(self.conn, "apply fill", 1)
        with timing.phase("outer"):
            with timing.phase("inner"):
                pass
        timing.finish()
        self.assertNotIn("inner", timing.load(self.conn)[0]["detail"]["phases"])

    def test_nothing_happens_outside_a_command(self):
        with timing.phase("type", "x"):
            pass
        timing.note(a=1)
        timing.result({"a": 1})
        timing.belongs_to(3)
        timing.spent("a", 1)
        timing.finish()
        self.assertEqual(timing.load(self.conn), [])

    def test_a_database_problem_never_breaks_the_command(self):
        self.conn.execute("DROP TABLE timing")
        timing.start(self.conn, "apply fill", 1)
        timing.note(a=1)
        timing.finish()

    def test_login_joins_the_run_of_the_agent_that_asked_for_it(self):
        with mock.patch.dict(os.environ, {"JOBFILL_SESSION": "agent"}):
            timing.start(self.conn, "apply fill", 1)
            timing.finish()
        with mock.patch.dict(os.environ, {"JOBFILL_SESSION": "someone's terminal"}):
            timing.start(self.conn, "login", 1)
            timing.finish()
        self.assertEqual([r["session"] for r in timing.load(self.conn)], ["agent", "agent"])


class TestCommands(unittest.TestCase):
    def setUp(self):
        self.folder = temp_dir(self)
        self.db = self.folder / "jobfill.db"
        env = mock.patch.dict(os.environ, {"JOBFILL_SESSION": "test", "JOBFILL_TIMING": "1"})
        env.start()
        self.addCleanup(env.stop)

    def run_cli(self, *argv, code=0):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                got = cli.main(["--db", str(self.db), *argv])
            except SystemExit as e:
                got = e.code
        self.assertEqual(got, code, err.getvalue())
        text = out.getvalue()
        return json.loads(text) if text.startswith(("{", "[")) else text

    def log(self):
        conn = db.connect(self.db)
        try:
            return timing.load(conn)
        finally:
            conn.close()

    def app(self):
        return self.run_cli("app", "start", "--company", "Acme", "--role", "Engineer")["id"]

    def test_every_step_of_an_application_is_logged_without_the_agent_doing_anything(self):
        app = self.app()
        self.run_cli("answer", "check", "--app", str(app), "--question", "why_company", "--text", "Because it is good work")
        self.run_cli("answer", "record", "--app", str(app), "--question", "why_company", "--label", "Why Acme?",
                     "--text", "Because it is good work", "--force")
        self.run_cli("fact", "list", "--tier", "1")
        rows = self.log()
        self.assertEqual([r["name"] for r in rows], ["app start", "answer check", "answer record", "fact list"])
        self.assertEqual([r["application_id"] for r in rows], [app, app, app, None])
        self.assertTrue(all(r["session"] == "test" and r["seconds"] is not None for r in rows))
        self.assertEqual((rows[1]["detail"]["question"], rows[1]["detail"]["words"]), ("why_company", 5))
        self.assertEqual(rows[2]["detail"]["recorded"], True)

    def test_reading_the_results_is_not_logged(self):
        app = self.app()
        self.run_cli("app", "timing")
        self.run_cli("app", "timing", str(app))
        self.run_cli("app", "stats")
        self.run_cli("app", "list")
        self.assertEqual([r["name"] for r in self.log()], ["app start"])

    def test_a_failed_command_is_logged_with_what_went_wrong(self):
        self.run_cli("fact", "set", "no.such.fact", "1", code=2)
        self.assertIn("no.such.fact", self.log()[-1]["detail"]["error"])

    def test_a_link_that_is_no_job_is_counted_as_stopped_at_the_start(self):
        self.run_cli("apply", "start", "not a link", "--no-network", code=3)
        start = self.log()[0]
        self.assertEqual((start["application_id"], start["detail"]["url"]), (None, "not a link"))
        overview = self.run_cli("app", "timing", "--json")
        self.assertEqual(overview["stopped_at_start"][0]["url"], "not a link")
        self.assertIn("Not a job posting", overview["stopped_at_start"][0]["said"])

    def test_apply_start_belongs_to_the_application_it_creates(self):
        out = self.run_cli("apply", "start", "https://jobs.ashbyhq.com/openai/240d459b-696d-43eb-8497-fab3e56ecd9b",
                           "--no-network", "--company", "OpenAI", "--role", "Engineer")
        self.assertEqual(self.log()[0]["application_id"], out["app_id"])
        self.assertEqual(self.log()[0]["detail"]["handler"], "python")

    def test_giving_up_marks_the_application_and_the_report(self):
        app = self.app()
        out = self.run_cli("app", "giveup", str(app), "--why", "captcha", "--reason", "Cloudflare on every page")
        self.assertEqual((out["status"], out["why"], out["reason"]), ("manual", "captcha", "Cloudflare on every page"))
        self.assertIn("active", out)
        self.assertEqual(self.run_cli("app", "show", str(app))["status"], "manual")
        self.assertIn("Gave up after", Path(out["record"]).read_text(encoding="utf-8"))
        self.assertIsNone(self.run_cli("app", "show", str(app))["notes"])   # notes reach the written answers: not here
        gave_up = self.run_cli("app", "stats")["time"]["gave_up"]
        self.assertEqual([(g["id"], g["company"], g["why"]) for g in gave_up], [(app, "Acme", "captcha")])

    def test_the_report_has_the_time_and_an_old_application_has_none(self):
        app = self.app()
        conn = db.connect(self.db)
        from jobfill.store import Store
        store = Store(conn)
        old = store.start_application("Old Corp", "Engineer")   # before jobfill kept time: no commands logged
        rep, old_rep = report.build(store, app), report.build(store, old)
        conn.close()
        self.assertIn("active", rep["time"])
        self.assertNotIn("time", old_rep)
        self.assertIn("## Time", report.markdown(rep))
        self.assertIn("app timing", report.markdown(rep))
        self.assertNotIn("## Time", report.markdown(old_rep))
        self.assertIn("Not timed", self.run_cli("app", "timing", str(old))["note"])

    def test_the_timing_views_show_each_step_and_every_application(self):
        app = self.app()
        self.run_cli("answer", "record", "--app", str(app), "--question", "why_company", "--text", "Good work", "--force")
        one = self.run_cli("app", "timing", str(app))
        self.assertIn("## Step by step", one)
        self.assertIn("| python | answer record |", one)
        data = self.run_cli("app", "timing", str(app), "--json")
        self.assertEqual(data["timeline"][0]["what"], "app start")
        everything = self.run_cli("app", "timing")
        self.assertIn("| Acme |", everything)
        self.assertIn("## Runs", everything)
        self.assertIn("why_company", everything)


class TestTurnMarks(unittest.TestCase):
    def setUp(self):
        self.db = temp_dir(self) / "jobfill.db"

    def hook(self, *args, payload='{"session_id": "abc"}'):
        return subprocess.run([sys.executable, "-m", "jobfill.timing", *args], input=payload, capture_output=True,
                              text=True, cwd=REPO, env={**os.environ, "JOBFILL_DB": str(self.db)})

    def rows(self):
        conn = db.connect(self.db)
        try:
            return timing.load(conn)
        finally:
            conn.close()

    def command_in_session(self, session):
        conn = db.connect(self.db)
        with mock.patch.dict(os.environ, {"JOBFILL_SESSION": session, "JOBFILL_TIMING": "1"}):
            timing.start(conn, "apply fill", 1)
            timing.finish()
        conn.close()

    def test_a_hook_records_the_turn_and_prints_nothing(self):
        self.command_in_session("abc")
        done = self.hook("pause", "--hook")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))
        self.assertEqual([(r["name"], r["session"]) for r in self.rows()], [("apply fill", "abc"), ("pause", "abc")])

    def test_a_session_that_never_ran_a_command_logs_nothing(self):
        self.command_in_session("other")
        self.assertEqual(self.hook("pause", "--hook").returncode, 0)
        self.assertEqual([r["session"] for r in self.rows()], ["other"])

    def test_no_database_yet_is_fine(self):
        done = self.hook("resume", "--hook")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))
        self.assertFalse(self.db.exists())

    def test_bad_hook_input_is_fine(self):
        self.command_in_session("own")
        done = subprocess.run([sys.executable, "-m", "jobfill.timing", "pause", "--hook"], input="not json",
                              capture_output=True, text=True, cwd=REPO,
                              env={**os.environ, "JOBFILL_DB": str(self.db), "JOBFILL_SESSION": "own"})
        self.assertEqual((done.returncode, done.stdout), (0, ""))
        self.assertEqual(self.rows()[-1]["name"], "pause")

    def test_a_wrong_mark_is_refused(self):
        self.assertEqual(self.hook("nonsense").returncode, 2)

    def test_the_hook_stays_light(self):
        # It runs on every turn: importing the question catalog would add a third of a second each time.
        done = subprocess.run([sys.executable, "-c", "import sys, jobfill.timing; print('jobfill.catalog' in sys.modules)"],
                              capture_output=True, text=True, cwd=REPO)
        self.assertEqual(done.stdout.strip(), "False")

    def test_the_hooks_are_not_shipped_to_everyone_who_clones_the_repo(self):
        tracked = REPO / ".claude" / "settings.json"
        self.assertNotIn("timing.py", tracked.read_text(encoding="utf-8") if tracked.exists() else "")

    def test_the_readme_gives_the_settings_a_checkout_needs(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        blocks = [b.split("```")[0] for b in readme.split("```json\n")[1:]]
        settings = json.loads(next(b for b in blocks if "JOBFILL_TIMING" in b))
        self.assertEqual(settings["env"], {"JOBFILL_TIMING": "1"})
        self.assertIn('timing.py" pause --hook', settings["hooks"]["Stop"][0]["hooks"][0]["command"])
        self.assertIn('timing.py" resume --hook', settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"])

    def test_it_runs_as_a_plain_script_from_anywhere_as_the_hooks_do(self):
        # The script's own folder comes first on the import path: no module of jobfill may hide a standard one.
        self.command_in_session("abc")
        done = subprocess.run([sys.executable, str(REPO / "jobfill" / "timing.py"), "pause", "--hook"],
                              input='{"session_id": "abc"}', capture_output=True, text=True, cwd=self.db.parent,
                              env={**os.environ, "JOBFILL_DB": str(self.db)})
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))
        self.assertEqual(self.rows()[-1]["name"], "pause")

    def test_it_finds_the_same_database_as_jobfill(self):
        self.assertEqual(timing.database(), db.default_db_path())
        with mock.patch.dict(os.environ, {"JOBFILL_DB": "elsewhere.db"}):
            self.assertEqual(timing.database(), db.default_db_path())


class TestTimingOff(unittest.TestCase):
    """What every user gets: no log, no time in their reports, and `app giveup` works all the same."""

    def setUp(self):
        self.db = temp_dir(self) / "jobfill.db"
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("JOBFILL_TIMING", None)

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["--db", str(self.db), *argv]), 0)
        text = out.getvalue()
        return json.loads(text) if text.startswith(("{", "[")) else text

    def test_it_is_off_unless_asked_for(self):
        self.assertFalse(timing.enabled())
        with mock.patch.dict(os.environ, {"JOBFILL_TIMING": "0"}):
            self.assertFalse(timing.enabled())
        with mock.patch.dict(os.environ, {"JOBFILL_TIMING": "1"}):
            self.assertTrue(timing.enabled())

    def test_commands_log_nothing_and_reports_have_no_time(self):
        app = self.run_cli("app", "start", "--company", "Acme", "--role", "Engineer")["id"]
        written = self.run_cli("app", "report", str(app))
        conn = db.connect(self.db)
        rows = timing.load(conn)
        conn.close()
        self.assertEqual(rows, [])
        self.assertNotIn("## Time", Path(written["markdown"]).read_text(encoding="utf-8"))
        self.assertNotIn("time", json.loads(Path(written["json"]).read_text(encoding="utf-8")))
        self.assertNotIn("time", self.run_cli("app", "stats"))
        self.assertIn("JOBFILL_TIMING", self.run_cli("app", "timing"))
        self.assertIn("JOBFILL_TIMING", self.run_cli("app", "timing", str(app))["note"])

    def test_giving_up_works_without_timing(self):
        app = self.run_cli("app", "start", "--company", "Acme", "--role", "Engineer")["id"]
        out = self.run_cli("app", "giveup", str(app), "--why", "captcha", "--reason", "Cloudflare on every page")
        self.assertEqual((out["status"], out["why"], out["reason"]), ("manual", "captcha", "Cloudflare on every page"))
        self.assertNotIn("active", out)
        record = Path(out["record"]).read_text(encoding="utf-8")
        self.assertIn("yours to finish by hand** (captcha): Cloudflare on every page", record)
        shown = self.run_cli("app", "show", str(app))
        self.assertEqual((shown["status"], shown["giveup_why"], shown["notes"]), ("manual", "captcha", None))
        self.assertEqual([(g["id"], g["giveup_why"]) for g in self.run_cli("app", "stats")["given_up"]], [(app, "captcha")])

    def test_a_database_from_before_giveup_gets_the_columns(self):
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE applications (id INTEGER PRIMARY KEY, company TEXT NOT NULL, role TEXT, url TEXT, "
                     "ats TEXT, country TEXT, source TEXT, referrer TEXT, posting TEXT, notes TEXT, "
                     "status TEXT NOT NULL DEFAULT 'in_progress', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.execute("INSERT INTO applications (company, created_at, updated_at) VALUES ('Old Corp', 'x', 'x')")
        conn.commit()
        conn.close()
        self.assertEqual(self.run_cli("app", "giveup", "1", "--why", "other", "--reason", "x")["company"], "Old Corp")


@unittest.skipUnless(HAS_PLAYWRIGHT, "playwright not installed")
class TestFillPhases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def test_a_fill_says_where_its_time_went(self):
        from jobfill.apply.runner import fill_frame
        store = fresh_store(with_profile=True, owner=self)
        app = store.start_application("Globex", "Data Engineer", ats="lever")
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.goto((FIXTURES / "lever_like.html").as_uri())
        lever = adapters.get("lever")
        with mock.patch.dict(os.environ, {"JOBFILL_TIMING": "1"}):
            timing.start(store.conn, "apply fill", app)
        fill_frame(lever.frame(page), store, app, lever, overwrite=True, files=False)
        timing.finish()
        detail = timing.load(store.conn)[0]["detail"]
        for step in ("read_form", "resolve", "type", "other"):
            self.assertIn(step, detail["phases"])
        self.assertGreater(detail["fields_typed"], 5)
        self.assertIn("Inside jobfill", timing.markdown_application(store.application(app),
                                                                    timing.Book(store.conn).application(app, timeline=True)))


if __name__ == "__main__":
    unittest.main()
