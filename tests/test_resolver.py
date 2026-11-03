import json
import unittest

from jobfill.resolver import Resolver, ai_task_for
from jobfill.text import names_same, normalize, search_queries

from .helpers import EXAMPLES, TODAY, fresh_store


def by_id(result):
    return {f["id"]: f for f in result["fields"]}


class TestSampleForm(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = fresh_store(with_profile=True, owner=cls)
        cls.app = cls.store.start_application("Acme Robotics, Inc.", "ML Infrastructure Engineer", country="US")
        form = json.loads((EXAMPLES / "sample_form.json").read_text(encoding="utf-8"))
        cls.result = Resolver(cls.store, cls.app, today=TODAY).resolve(form["fields"])
        cls.f = by_id(cls.result)

    def value(self, fid):
        self.assertEqual(self.f[fid]["status"], "filled", self.f[fid])
        return self.f[fid]["value"]

    def test_most_fields_need_no_ai(self):
        s = self.result["summary"]
        self.assertGreaterEqual(s["filled"], 38)
        self.assertLessEqual(s.get("needs_ai", 0), 5)

    def test_identity_and_contact(self):
        self.assertEqual(self.value("first_name"), "Jordan")
        self.assertEqual(self.value("preferred"), "Jo")
        self.assertEqual(self.value("location"), "Madison, WI")
        self.assertEqual(self.value("resume"), "documents/resume/jordan_rivera_resume.pdf")

    def test_work_authorization(self):
        self.assertEqual(self.value("auth"), "Yes")
        self.assertEqual(self.value("sponsor"), "Yes")
        self.assertEqual(self.value("auth_nosponsor"), "No")

    def test_eeo_options(self):
        self.assertEqual(self.value("gender"), "Non-binary")
        self.assertEqual(self.value("hispanic"), "Yes")
        self.assertEqual(self.value("race"), "Decline To Self Identify")
        self.assertEqual(self.value("veteran"), "I am not a protected veteran")
        self.assertEqual(self.value("disability"), "I do not want to answer")

    def test_derived_answers(self):
        self.assertEqual(self.value("py_years"), "5+ years")
        self.assertEqual(self.value("k8s"), "Yes")          # skill alias "k8s"
        self.assertEqual(self.value("go"), "No")            # recorded with level none
        self.assertEqual(self.value("worked_before"), "No")
        self.assertEqual(self.value("office"), "Yes")
        self.assertEqual(self.value("office_loc"), "Seattle, WA")
        self.assertEqual(self.value("heard"), "LinkedIn")
        self.assertEqual(self.value("degree"), "Master's Degree")
        self.assertEqual(self.value("highest"), "Master's")
        self.assertEqual(self.value("spanish"), "Yes")
        self.assertEqual(self.value("french"), "No")
        self.assertIs(self.value("certify"), True)

    def test_dates_are_formatted(self):
        self.assertEqual(self.value("start"), "06/01/2027")
        self.assertEqual(self.value("grad"), "05/2027")
        self.assertEqual(self.value("exp0_from"), "06/2026")

    def test_repeating_sections_use_index(self):
        self.assertEqual(self.value("exp0_company"), "Example Analytics")
        self.assertEqual(self.value("edu1_school"), "Universidad Nacional Autonoma de Mexico")
        self.assertIn("feature store", self.value("exp0_desc"))

    def test_unknown_skill_asks_user(self):
        self.assertEqual(self.f["rust_years"]["status"], "needs_user")
        self.assertIn("Rust", self.f["rust_years"]["ask"])
        self.assertEqual(self.f["rust_years"]["store"]["record"], "skill")

    def test_tier3_goes_to_ai_with_small_context(self):
        self.assertEqual(self.f["why"]["status"], "needs_ai")
        self.assertEqual(self.f["why"]["ai_task"]["question_id"], "why_company")
        self.assertEqual(self.f["why"]["ai_task"]["limits"], {"max_chars": 1000})
        # A known open question is written even on an optional field; the candidate may still drop it.
        self.assertEqual((self.f["oss"]["status"], self.f["oss"]["question_id"]), ("needs_ai", "tech_opinion"))
        # Unknown open question, even on an optional field: written by the AI and queued for `learn`.
        unknown = {"id": "q", "label": "If you could teach a class on anything, what would it be?", "type": "textarea"}
        optional = Resolver(self.store, self.app, today=TODAY).resolve([unknown])
        self.assertEqual((optional["fields"][0]["status"], optional["fields"][0]["question_id"]),
                         ("needs_ai", "open_ended_custom"))
        self.assertIn(normalize(unknown["label"]), {p["normalized"] for p in self.store.pending()})
        # "Anything else?" stays blank on an optional field.
        extra = {"id": "x", "label": "Is there anything else you would like us to know?", "type": "textarea"}
        self.assertEqual(Resolver(self.store, self.app, today=TODAY).resolve([extra])["fields"][0]["status"],
                         "skipped")
        self.assertIn("learning", self.f["learn"]["ai_task"]["themes"])
        # Each written answer names its technique; the context carries only the techniques on this form.
        self.assertEqual(self.f["why"]["ai_task"]["playbook"], "why_company")
        writing = self.result["ai_context"]["writing"]
        self.assertEqual(set(writing["playbooks"]),
                         {f["ai_task"]["playbook"] for f in self.f.values() if f["status"] == "needs_ai"})
        self.assertTrue(writing["style"] and writing["checklist"])
        ctx = json.dumps(self.result["ai_context"])
        # Sensitive and irrelevant data never reaches the model.
        for secret in ("non_binary", "veteran", "disability", "608-555", "123 Example St", "f1_opt"):
            self.assertNotIn(secret, ctx)
        self.assertLess(len(ctx), 6000)

    def test_optional_ai_fields_are_skipped(self):
        self.assertEqual(self.f["anything"]["status"], "skipped")
        self.assertEqual(self.f["cover"]["status"], "skipped")

    def test_unknown_short_question_is_queued(self):
        self.assertEqual(self.f["tshirt"]["status"], "unmatched")
        self.assertIn("t shirt size", [p["normalized"] for p in self.store.pending()])


class TestLearningLoop(unittest.TestCase):
    def setUp(self):
        self.store = fresh_store(with_profile=True, owner=self)
        self.app = self.store.start_application("Globex", "Data Engineer")

    def resolve(self, fields, app=None):
        return Resolver(self.store, app or self.app, today=TODAY).resolve(fields)["fields"]

    def test_missing_fact_asked_once_then_answered_by_python(self):
        field = {"id": "dl", "label": "Do you have a valid driver's license?", "type": "radio",
                 "options": ["Yes", "No"], "required": True}
        first = self.resolve([field])[0]
        self.assertEqual((first["status"], first["fact"]), ("needs_user", "legal.drivers_license"))
        self.store.set_fact("legal.drivers_license", "yes")
        self.assertEqual(self.resolve([field])[0]["value"], "Yes")

    def test_new_rephrasing_learned_as_alias(self):
        field = {"id": "x", "label": "Mention the city where you currently reside", "type": "text", "required": True}
        self.assertIn(self.resolve([field])[0]["status"], ("unmatched", "uncertain"))
        pending = next(p for p in self.store.pending() if "reside" in p["normalized"])
        self.store.add_alias("current_location", pending["label"])
        self.store.close_pending(pending["id"], "learned", "alias_of:current_location")
        again = self.resolve([field])[0]
        self.assertEqual((again["status"], again["match"]["how"], again["value"]), ("filled", "exact", "Madison, WI"))

    def test_new_question_with_new_fact(self):
        field = {"id": "t", "label": "T-shirt size", "type": "select", "options": ["S", "M", "L"], "required": True}
        self.assertEqual(self.resolve([field])[0]["status"], "unmatched")
        self.store.define_fact("prefs.tshirt_size", 2, "string", "T-shirt size", "Your T-shirt size (S/M/L/XL)")
        self.store.add_question("tshirt_size", 2, "custom", "T-shirt size", {"fact": "prefs.tshirt_size"})
        self.store.set_fact("prefs.tshirt_size", "M")
        self.assertEqual(self.resolve([field])[0]["value"], "M")

    def test_tier3_answers_are_unique_per_company(self):
        why_co = {"id": "w", "label": "Why do you want to work at Globex?", "type": "textarea", "required": True}
        why_role = {"id": "r", "label": "Why are you interested in this role?", "type": "textarea", "required": True}
        self.assertEqual(self.resolve([why_co])[0]["status"], "needs_ai")
        self.store.record_answer(self.app, "w", why_co["label"], "Globex answer", question_id="why_company", source="ai")
        self.store.record_answer(self.app, "r", why_role["label"], "Role answer", question_id="why_role", source="ai")
        # Same application: reused (e.g. the form is re-opened).
        self.assertEqual(self.resolve([why_co])[0]["value"], "Globex answer")
        # Same company, another role: "why us" is reusable, "why this role" is not.
        second = self.store.start_application("Globex Corporation", "ML Engineer")
        out = self.resolve([why_co, why_role], app=second)
        self.assertEqual(out[0]["value"], "Globex answer")
        self.assertEqual(out[1]["status"], "needs_ai")
        # Different company: always written fresh.
        other = self.store.start_application("Initech", "Data Engineer")
        out = self.resolve([{**why_co, "label": "Why do you want to work at Initech?"}], app=other)
        self.assertEqual(out[0]["status"], "needs_ai")

    def test_previous_application_detected(self):
        second = self.store.start_application("Globex", "Analytics Engineer")
        field = {"id": "p", "label": "Have you previously applied to Globex?", "type": "radio", "options": ["Yes", "No"]}
        self.assertEqual(self.resolve([field], app=second)[0]["value"], "Yes")

    def test_internship_is_not_the_current_company(self):
        # A student whose only ongoing work is an internship.
        self.store.add_record("experience", {"company": "Acme Labs", "title": "ML Intern", "employment_type": "internship",
                                             "start_date": "2026-09", "current": True})
        fields = [
            {"id": "c", "label": "Current company", "type": "text"},
            {"id": "y", "label": "What is your current company?", "type": "text"},   # "your" is not "or"
            {"id": "t", "label": "Current title", "type": "text"},
            {"id": "e", "label": "Are you currently employed?", "type": "radio", "options": ["Yes", "No"]},
            {"id": "r", "label": "Current or most recent employer", "type": "text"},
        ]
        out = {f["id"]: f for f in self.resolve(fields)}
        self.assertEqual([out[k]["status"] for k in "cyt"], ["skipped"] * 3)
        self.assertEqual(out["e"]["value"], "No")
        self.assertEqual(out["r"]["value"], "Acme Labs")     # "most recent" may still name the internship
        # A real job is the current company, ahead of the internship.
        self.store.add_record("experience", {"company": "Globex", "title": "Data Engineer", "employment_type": "full_time",
                                             "start_date": "2025-01", "current": True})
        out = {f["id"]: f for f in self.resolve(fields)}
        self.assertEqual([out[k]["value"] for k in "cyter"], ["Globex", "Globex", "Data Engineer", "Yes", "Globex"])

    def test_finished_job_with_stale_current_flag(self):
        exp = self.store.records("experience")[0]                # internship that ended 2026-08
        self.store.update_record(exp["_id"], {"current": True})   # flag left on by mistake
        out = {f["id"]: f for f in self.resolve([
            {"id": "now", "label": "I currently work here", "type": "checkbox", "section": "experience", "index": 0},
            {"id": "end", "label": "End date", "type": "text", "section": "experience", "index": 0},
            {"id": "e", "label": "Are you currently employed?", "type": "radio", "options": ["Yes", "No"]}])}
        self.assertIs(out["now"]["value"], False)
        self.assertEqual(out["end"]["value"], "08/2026")
        self.assertEqual(out["e"]["value"], "No")

    def test_skill_record_without_years_or_level_means_yes(self):
        # Resume imports store skills by name only; that must not read as "No".
        self.store.add_record("skill", {"name": "Docker"})
        self.store.add_record("skill", {"name": "Scala", "years": 0, "level": "beginner"})
        self.store.add_record("skill", {"name": "Rust", "years": 0})
        self.store.add_record("skill", {"name": "Haskell", "level": "none"})
        answers = {}
        for name in ("Docker", "Scala", "Rust", "Haskell"):
            field = {"id": name, "label": f"Do you have experience with {name}?", "type": "radio",
                     "options": ["Yes", "No"]}
            answers[name] = self.resolve([field])[0]["value"]
        self.assertEqual(answers, {"Docker": "Yes", "Scala": "Yes", "Rust": "No", "Haskell": "No"})
        listed = self.resolve([{"id": "s", "label": "Skills", "type": "text"}])[0]["value"]
        self.assertIn("Docker", listed)
        self.assertNotIn("Rust", listed)
        self.assertNotIn("Haskell", listed)
        years = self.resolve([{"id": "y", "label": "How many years of experience do you have with Haskell?",
                               "type": "number"}])[0]
        self.assertEqual(years["value"], "0")
        unknown = self.resolve([{"id": "d", "label": "How many years of experience do you have with Docker?",
                                 "type": "number", "required": True}])[0]
        self.assertEqual(unknown["status"], "needs_user")

    def test_years_of_experience_never_guessed_from_internships(self):
        field = {"id": "y", "label": "Years of professional experience", "type": "number", "required": True}
        first = self.resolve([field])[0]
        self.assertEqual((first["status"], first["fact"]), ("needs_user", "profile.years_experience_override"))
        self.store.set_fact("profile.years_experience_override", 0.5)
        self.assertEqual(self.resolve([field])[0]["value"], "0.5")

    def test_country_and_state_options(self):
        fields = [{"id": "c", "label": "Country", "type": "select", "options": ["Canada", "USA", "Mexico"]},
                  {"id": "s", "label": "State", "type": "select", "options": ["Washington", "Wisconsin", "Wyoming"]}]
        out = self.resolve(fields)
        self.assertEqual([o["value"] for o in out], ["USA", "Wisconsin"])

    def test_context_command_for_single_question(self):
        task = ai_task_for(self.store, "bh_conflict", self.app)
        self.assertEqual(task["status"], "needs_ai")
        self.assertIn("stories", task["ai_context"]["candidate"])
        self.assertNotIn("projects", task["ai_context"]["candidate"])
        self.assertEqual(task["ai_task"]["playbook"], "star")
        self.assertEqual(list(task["ai_context"]["writing"]["playbooks"]), ["star"])


class TestGPA(unittest.TestCase):
    """GPA asked as an exact number, as a range, or as a threshold; scales are never mixed up."""

    YN = ["Yes", "No"]

    def setUp(self):
        self.store = fresh_store(with_profile=True, owner=self)   # M.S. 3.8/4.0 (index 0), B.S. 9.1/10 (index 1)
        self.app = self.store.start_application("Acme", "Engineer")

    def resolve(self, fields):
        return {f["id"]: f for f in Resolver(self.store, self.app, today=TODAY).resolve(fields)["fields"]}

    def test_exact_and_range(self):
        out = self.resolve([
            {"id": "text", "label": "GPA", "type": "text"},
            {"id": "num", "label": "Cumulative GPA", "type": "number"},
            {"id": "range", "label": "GPA", "type": "select",
             "options": ["3.5 - 4.0", "3.0 - 3.49", "2.5 - 2.99", "Below 2.5", "Prefer not to say"]},
            {"id": "plus", "label": "What is your cumulative GPA?", "type": "select",
             "options": ["3.75 and above", "3.50 - 3.74", "3.25 - 3.49", "Below 3.25"]},
            {"id": "exact", "label": "GPA", "type": "select", "options": ["4.0", "3.9", "3.8", "3.7"]}])
        self.assertEqual({k: v["value"] for k, v in out.items()},
                         {"text": "3.8", "num": "3.8", "range": "3.5 - 4.0", "plus": "3.75 and above", "exact": "3.8"})

    def test_value_between_one_decimal_ranges_rounds_down(self):
        rid = self.store.records("education")[0]["_id"]
        self.store.update_record(rid, {"gpa": "3.45"})
        out = self.resolve([{"id": "g", "label": "GPA", "type": "select", "options": ["3.5 - 4.0", "3.0 - 3.4", "Below 3.0"]}])
        self.assertEqual(out["g"]["value"], "3.0 - 3.4")   # never rounded up into 3.5 - 4.0

    def test_threshold_questions(self):
        out = self.resolve([
            {"id": "a", "label": "Is your cumulative GPA 3.0 or higher?", "type": "radio", "options": self.YN},
            {"id": "b", "label": "Do you have a minimum GPA of 3.9?", "type": "radio", "options": self.YN},
            {"id": "c", "label": "Do you have a 3.5 GPA or higher?", "type": "radio", "options": self.YN},
            {"id": "d", "label": "Is your GPA above 3.8?", "type": "radio", "options": self.YN}])
        self.assertEqual({k: (v["question_id"], v["value"]) for k, v in out.items()},
                         {"a": ("gpa_threshold", "Yes"), "b": ("gpa_threshold", "No"),
                          "c": ("gpa_threshold", "Yes"), "d": ("gpa_threshold", "No")})   # "above 3.8" is strict

    def test_other_scales_are_never_passed_off_as_4_0(self):
        out = self.resolve([
            {"id": "text", "label": "Undergraduate GPA", "type": "text"},
            {"id": "row", "label": "GPA", "type": "text", "section": "education", "index": 1},
            {"id": "four", "label": "Undergraduate GPA (out of 4.0)", "type": "text", "required": True}])
        self.assertEqual((out["text"]["value"], out["row"]["value"]), ("9.1/10", "9.1/10"))
        self.assertEqual((out["four"]["status"], out["four"]["store"]["field"]), ("needs_user", "gpa_4"))
        # The candidate answers once; every 4.0-scale form uses it from then on.
        self.store.update_record(out["four"]["store"]["id"], {"gpa_4": "3.6"})
        again = self.resolve([{"id": "four", "label": "Undergraduate GPA (out of 4.0)", "type": "text"},
                              {"id": "rng", "label": "Undergraduate GPA", "type": "select",
                               "options": ["3.5 - 4.0", "3.0 - 3.49", "Below 3.0"]}])
        self.assertEqual((again["four"]["value"], again["rng"]["value"]), ("3.6", "3.5 - 4.0"))


class TestHowHeard(unittest.TestCase):
    """'How did you hear about us?' with sub-options: combined labels, categories, follow-ups."""

    def setUp(self):
        self.store = fresh_store(with_profile=True, owner=self)   # default source: LinkedIn
        self.app = self.store.start_application("Acme", "Engineer")

    def value(self, label, options, app=None):
        out = Resolver(self.store, app or self.app, today=TODAY).resolve(
            [{"id": "h", "label": label, "type": "select", "options": options}])["fields"][0]
        return out.get("value"), out.get("question_id")

    def test_combined_labels_prefer_the_likely_category(self):
        self.assertEqual(self.value("How did you hear about us?",
                                    ["Job Board: Indeed", "Social Media: LinkedIn", "Job Board: LinkedIn", "Other"]),
                         ("Job Board: LinkedIn", "how_heard"))
        self.assertEqual(self.value("Source", ["LinkedIn", "Indeed", "Company Website"])[0], "LinkedIn")

    def test_two_step_category_then_detail(self):
        self.assertEqual(self.value("How did you find this job?", ["Company Website", "Job Board", "Referral", "Other"]),
                         ("Job Board", "how_heard"))
        self.assertEqual(self.value("Which job board?", ["Indeed", "LinkedIn", "Glassdoor"]),
                         ("LinkedIn", "how_heard_detail"))

    def test_categories_for_other_sources(self):
        options = ["Job Boards / Websites", "Social Media", "Employee Referral", "University / Career Fair",
                   "Company Careers Page", "Recruiter"]
        cases = {"Indeed": "Job Boards / Websites", "Twitter": "Social Media", "Employee referral": "Employee Referral",
                 "Career fair": "University / Career Fair", "Company website": "Company Careers Page",
                 "Recruiter": "Recruiter"}
        for source, want in cases.items():
            app = self.store.start_application("Acme", "Engineer", source=source)
            self.assertEqual(self.value("How did you hear about us?", options, app)[0], want, source)

    def test_postal_code_is_required_and_matched(self):
        self.assertIn("address.postal_code", fresh_store(owner=self).status()["missing_required"])
        out = Resolver(self.store, self.app, today=TODAY).resolve(
            [{"id": "z", "label": "ZIP / Postal code (5 digits)", "type": "text"},
             {"id": "p", "label": "Post Code", "type": "text"}])["fields"]
        self.assertEqual([f.get("value") for f in out], ["53703", "53703"])


class TestPronouns(unittest.TestCase):
    """Pronoun dropdowns: one stored choice, matched to whatever wording the form uses."""

    GREENHOUSE = ["He/Him", "She/Her", "They/Them", "Xe/Xem", "Ze/Hir", "Use my name",
                  "I prefer to self-describe", "I don't wish to answer"]

    def setUp(self):
        self.store = fresh_store(with_profile=True, owner=self)   # sample profile: "they/them"
        self.app = self.store.start_application("Acme", "Engineer")

    def resolve(self, field):
        return Resolver(self.store, self.app, today=TODAY).resolve([{"id": "p", **field}])["fields"][0]

    def test_dropdown_wordings(self):
        self.assertEqual(self.store.get_fact("identity.pronouns"), ["they_them"])
        self.assertEqual(self.resolve({"label": "Pronouns", "type": "select", "options": self.GREENHOUSE})["value"],
                         "They/Them")
        for stored, options, want in (
                ("she/her", ["She / Her / Hers", "He / Him / His", "They / Them / Theirs"], "She / Her / Hers"),
                ("he/they", ["He/Him", "He/They", "They/Them"], "He/They"),
                ("decline", self.GREENHOUSE, "I don't wish to answer"),
                ("use my name", self.GREENHOUSE, "Use my name")):
            self.store.set_fact("identity.pronouns", stored)
            self.assertEqual(self.resolve({"label": "What are your pronouns?", "type": "select", "options": options})
                             ["value"], want, stored)

    def test_text_box_and_checkboxes(self):
        self.assertEqual(self.resolve({"label": "Pronouns (optional)", "type": "text"})["value"], "they/them")
        self.store.set_fact("identity.pronouns", "she/her, they/them")
        out = self.resolve({"label": "Pronouns (select all that apply)", "type": "checkbox",
                            "options": ["He/Him", "She/Her", "They/Them"]})
        self.assertEqual(out["value"], ["She/Her", "They/Them"])

    def test_asked_once_when_a_form_has_it(self):
        self.store.unset_fact("identity.pronouns")
        out = self.resolve({"label": "Pronouns", "type": "select", "options": self.GREENHOUSE})
        self.assertEqual((out["status"], out["fact"], out["sensitive"]), ("needs_user", "identity.pronouns", True))
        self.assertIn("decline", out["choices"])

    def test_pronunciation_is_not_pronouns(self):
        out = self.resolve({"label": "Name Pronunciation | How do you pronounce your name?", "type": "text"})
        self.assertNotEqual(out.get("question_id"), "pronouns")

    def test_old_free_text_value_is_converted(self):
        from jobfill import db
        self.store.conn.execute("UPDATE facts SET value='\"She/Her\"' WHERE key='identity.pronouns'")
        db.seed(self.store.conn)
        self.assertEqual(self.store.get_fact("identity.pronouns"), ["she_her"])


class TestNoExactOption(unittest.TestCase):
    """A stored name (school, field of study) with no exact option is asked, never matched to a look-alike."""

    def setUp(self):
        self.store = fresh_store(with_profile=True, owner=self)   # school 0: University of Wisconsin-Madison
        self.app = self.store.start_application("Acme", "Engineer")
        self.school = {"id": "s", "label": "School or University", "type": "select", "section": "education",
                       "options": ["University of Wisconsin", "University of Wisconsin-Milwaukee", "Other"]}

    def resolve(self, field):
        return Resolver(self.store, self.app, today=TODAY).resolve([field])["fields"][0]

    def test_look_alike_is_asked_with_the_closest_options(self):
        r = self.resolve(self.school)
        self.assertEqual((r["status"], r["no_exact"]), ("needs_user", True))
        self.assertEqual(r["choices"][0], "University of Wisconsin")
        self.assertEqual(r["raw"], "University of Wisconsin-Madison")

    def test_choice_picked_before_is_reused_where_offered(self):
        self.store.remember_choice("edu_school", "University of Wisconsin-Madison", "Other", "Globex")
        r = self.resolve(self.school)
        self.assertEqual((r["status"], r["value"]), ("filled", "Other"))
        # Not offered on this form: asked again.
        r = self.resolve({**self.school, "options": ["University of Wisconsin", "Stanford University"]})
        self.assertEqual(r["status"], "needs_user")

    def test_exact_and_qualified_names_still_fill(self):
        r = self.resolve({**self.school, "options": ["Stanford University", "University of Wisconsin-Madison"]})
        self.assertEqual(r["value"], "University of Wisconsin-Madison")
        field = {"id": "f", "label": "Field of Study", "type": "select", "section": "education",
                 "options": ["Computer Science (CS)", "Mathematics"]}
        self.assertEqual(self.resolve(field)["value"], "Computer Science (CS)")


class TestListSearch(unittest.TestCase):
    """A long list is searched by the name, then its distinctive words; the pick stays the exact name."""

    def test_queries_go_from_the_whole_name_to_its_distinctive_words(self):
        self.assertEqual(search_queries("University of Wisconsin-Madison"),
                         ["University of Wisconsin-Madison", "wisconsin madison", "wisconsin", "madison"])
        self.assertEqual(search_queries("Economics"), ["Economics"])

    def test_only_the_same_name_counts(self):
        self.assertTrue(names_same("University of Wisconsin, Madison", "University of Wisconsin-Madison"))
        for other in ("University of Wisconsin-Milwaukee", "James Madison University", "University of Wisconsin"):
            self.assertFalse(names_same(other, "University of Wisconsin-Madison"), other)


class TestFieldInstructions(unittest.TestCase):
    """The words under a field ("If it doesn't apply to you, please enter 'None'") shape the answer."""

    NONE = "Please enter the company you are currently employed at. If it doesn't apply to you, please enter \"None\"."

    def setUp(self):
        self.store = fresh_store(with_profile=True, owner=self)   # one internship, no current employer
        self.app = self.store.start_application("Acme", "Engineer")

    def resolve(self, **field):
        return Resolver(self.store, self.app, today=TODAY).resolve([{"id": "f", "required": True, **field}])["fields"][0]

    def test_not_applicable_answer_from_the_form(self):
        out = self.resolve(label="Current Company*", type="text", description=self.NONE)
        self.assertEqual((out["status"], out["value"], out["source"]), ("filled", "None", "form:instructions"))
        self.assertEqual(self.resolve(label="Current Company*", type="text")["status"], "needs_user")   # no instruction

    def test_a_list_never_gets_a_typed_none(self):
        out = self.resolve(label="Current Company*", type="select", options=["Acme", "Globex"], description=self.NONE)
        self.assertEqual(out["status"], "needs_user")
        self.assertEqual(out["instructions"], self.NONE)

    def test_instructions_reach_the_candidate_and_the_writer(self):
        why = self.resolve(label="Why Acme?", type="textarea", description="Under 150 words, please.")
        self.assertEqual(why["ai_task"]["instructions"], "Under 150 words, please.")
        odd = self.resolve(label="Favourite spaceship class", type="text", description="One word.")
        self.assertEqual(odd["instructions"], "One word.")


if __name__ == "__main__":
    unittest.main()
