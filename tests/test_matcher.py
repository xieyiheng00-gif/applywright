"""Real-world label wordings must reach the right question without AI."""

import unittest

from jobfill.catalog import QUESTIONS
from jobfill.matcher import Matcher
from jobfill.text import normalize

from .helpers import fresh_store

ESSAY = "textarea"

# (label, expected question id, field type, company)
CASES = [
    ("Legal First Name*", "first_name", None, None),
    ("Surname", "last_name", None, None),
    ("E-mail Address", "email", None, None),
    ("Mobile Phone Number", "phone", None, None),
    ("Address Line 1", "address_line1", None, None),
    ("Location (City, State, Country)", "location_full", None, None),
    ("City, region and country", "location_full", None, None),
    ("Where are you located? (City, Country)", "location_full", None, None),
    ("Zip/Postal Code", "postal_code", None, None),
    ("State/Province", "state", None, None),
    ("LinkedIn URL", "linkedin", None, None),
    ("Website / Portfolio", "website", None, None),
    ("Resume/CV", "resume_file", "file", None),
    ("Type to Add Skills", "skills_list", None, None),              # Workday's skills prompt
    ("Are you legally authorized to work in Canada?", "authorized_to_work", None, None),
    ("Are you legally eligible to work in the U.S. for any employer?", "authorized_to_work", None, None),
    ("Are you authorized to work in the U.S. without the need for employer sponsorship now or in the future?",
     "authorized_without_sponsorship", None, None),
    ("Do you now, or will you in the future, require sponsorship for an employment visa?",
     "sponsorship_now_or_future", None, None),
    ("Will you now or in the future require sponsorship for employment visa status (e.g., H-1B visa status)?",
     "sponsorship_now_or_future", None, None),
    ("Will you require Stripe to sponsor your visa now or in the future?", "sponsorship_now_or_future", None, "Stripe"),
    ("Will you require visa sponsorship to start working?", "sponsorship_now", None, None),
    ("Would you need immigration sponsorship in the future to continue employment?", "sponsorship_future", None, None),
    ("What is your current immigration/visa status?", "visa_status", None, None),
    ("Are you a U.S. Person as defined under ITAR?", "us_person", None, None),
    ("Do you identify as transgender?", "eeo_transgender", None, None),
    ("I identify my gender as:", "eeo_gender", None, None),
    ("Please identify your race/ethnicity", "eeo_race", None, None),
    ("Are you Hispanic/Latino?", "eeo_hispanic", None, None),
    ("Are you a protected veteran?", "eeo_veteran", None, None),
    ("Voluntary Self-Identification of Disability", "eeo_disability", None, None),
    ("Are you at least 18 years of age?", "age_18_plus", None, None),
    ("Do you have any relatives currently working at Stripe?", "relatives_at_company", None, "Stripe"),
    ("Have you previously worked at Stripe?", "previously_employed", None, "Stripe"),
    ("Have you ever been employed by Stripe, Inc. or any of its subsidiaries?", "previously_employed", None, "Stripe, Inc."),
    ("Have you applied to Stripe in the past 12 months?", "previously_applied", None, "Stripe"),
    ("How many years of professional software engineering experience do you have?", "years_with_skill", None, None),
    ("How many years of experience do you have in machine learning?", "years_with_skill", None, None),
    ("How many years of Rust experience do you have?", "years_with_skill", "number", None),
    ("Years of experience with C++", "years_with_skill", None, None),
    ("How many years of experience do you have?", "years_experience", None, None),
    ("Do you have experience with distributed systems?", "has_skill", None, None),
    ("Are you proficient in SQL?", "has_skill", None, None),
    ("What is your level of proficiency in English?", "skill_proficiency", None, None),
    ("Do you speak Mandarin?", "speaks_language", None, None),
    ("Do you have a Bachelor's degree in Computer Science or a related field?", "has_degree", None, None),
    ("Have you completed a Master's degree or higher?", "has_degree", None, None),
    ("When is your expected graduation date?", "graduation_date", None, None),
    ("What is your desired annual compensation?", "salary_expectation", None, None),
    ("What are your salary expectations for this role?", "salary_expectation", None, None),
    ("The salary range for this role is $150,000 - $180,000. Is this aligned with your expectations?",
     "salary_range_ok", None, None),
    ("When are you able to start?", "start_date", None, None),
    ("Are you open to relocating to San Francisco?", "willing_to_relocate", None, None),
    ("Are you able to work from our NYC office 4 days a week?", "onsite_ok", None, None),
    ("Are you willing to travel up to 25% of the time?", "willing_to_travel", None, None),
    ("How did you first hear about Stripe?", "how_heard", None, "Stripe"),
    ("Were you referred by a current employee?", "was_referred", None, None),
    ("Name of the person who referred you", "referrer_name", None, None),
    ("Why Stripe?", "why_company", ESSAY, "Stripe"),
    ("Why do you want to join Anthropic?", "why_company", ESSAY, None),
    ("What excites you about working at Anthropic?", "why_company", ESSAY, None),
    ("Why are you interested in this position?", "why_role", ESSAY, None),
    ("What makes you a great fit for this team?", "fit_for_role", ESSAY, None),
    ("Tell us about yourself", "about_you", ESSAY, None),
    ("Tell us about a time you disagreed with a teammate. How did you resolve it?", "bh_generic", ESSAY, None),
    ("What is the most technically challenging project you have worked on?", "technical_challenge", ESSAY, None),
    ("Describe a project you're most proud of.", "relevant_project", ESSAY, None),
    ("Where do you see yourself in 5 years?", "career_goals", ESSAY, None),
    ("What's your greatest weakness?", "weaknesses", ESSAY, None),
    ("Anything else you would like to share?", "anything_else", ESSAY, None),
    ("I have read and agree to the Applicant Privacy Policy", "privacy_ack", "checkbox", None),
    ("By checking this box I certify that my answers are true and accurate", "certify_accuracy", "checkbox", None),
    ("I agree to receive SMS updates about my application", "sms_consent", "checkbox", None),
    ("Please type your full name as your electronic signature", "signature", None, None),
    ("Have you ever been convicted of a criminal offense?", "criminal_conviction", None, None),
    ("Are you willing to undergo a background check, in accordance with local law?", "background_check", None, None),
    ("Current Company", "current_employer", None, None),
    ("Current Title", "current_title", None, None),
    ("Current Title or Role*", "current_title", None, None),
    ("Highest Degree Obtained", "highest_degree", None, None),
    ("Graduation Year", "graduation_year", None, None),
    ("Pronouns (optional)", "pronouns", None, None),
    ("Preferred office location", "preferred_location", None, None),
    ("Are you currently a student?", "currently_student", None, None),
    ("Do you hold an active TS/SCI clearance?", "has_clearance", None, None),
    # Seen on live Greenhouse / Lever forms (2026-10-27).
    ("When is the earliest you would want to start working with us?", "start_date", None, None),
    ("What has been your favorite project or proudest accomplishment? Why?", "bh_accomplishment", ESSAY, None),
    ("Preferred Name | What would you like us to call you?", "preferred_name", None, None),
    ('Which university are you currently attending or did you last attend? Please select "Other (School Not '
     'Listed)" if your school is not listed.', "edu_school", "select", None),
    ("Please tell us how you heard about this opportunity.", "how_heard", "select", None),
    ("Are you a Singapore citizen?", "citizen_of_country", "radio", None),
    ("Are you a Singapore permanent resident?", "permanent_resident_of", "radio", None),
    ("Have you ever interviewed at Anthropic before?", "previously_interviewed", "select", "Anthropic"),
    ("Are you open to working in-person in one of our offices 25% of the time?", "onsite_ok", "select", None),
    ("Language Skill(s) (Check all that apply)", "languages_list", "checkbox", None),
    ("Is your cumulative GPA 3.0 or higher?", "gpa_threshold", "radio", None),
    ("Do you have a 3.5 GPA or above?", "gpa_threshold", "radio", None),
    ("Undergraduate GPA (out of 4.0)", "undergrad_gpa", None, None),
    ("Please select your GPA range", "edu_gpa", "select", None),
    # Open questions common on Greenhouse / Lever / Workday portals (written per application).
    ("What is the best technique you learned recently?", "learned_recently", ESSAY, None),
    ("What's something you've taught yourself in the last six months?", "learned_recently", ESSAY, None),
    ("Explain a complex technical concept to someone non-technical.", "explain_concept", ESSAY, None),
    ("What's your favorite programming language, and why?", "tech_opinion", ESSAY, None),
    ("What's your favorite open-source project and why?", "tech_opinion", ESSAY, None),
    ("How do you use AI tools such as ChatGPT or Copilot in your day-to-day work?", "ai_usage", ESSAY, None),
    ("What AI tools do you use?", "ai_usage", ESSAY, None),
    ("What motivates you?", "motivation_drive", ESSAY, None),
    ("What do you optimize for in life?", "motivation_drive", ESSAY, None),
    ("What are you optimising for in your career right now?", "motivation_drive", ESSAY, None),
    ("How do you approach a complex problem you've never seen before?", "problem_approach", ESSAY, None),
    ("Please describe your experience with distributed systems.", "skill_experience", ESSAY, None),
    ("Tell us about your hands-on experience with Kubernetes", "skill_experience", ESSAY, None),
    ("Describe your prior research experience.", "research_experience", ESSAY, None),
    ("What was your favorite class in college and why?", "favorite_course", ESSAY, None),
    ("Why are you looking to leave your current role?", "leaving_reason", ESSAY, None),
    ("Please explain any gaps in your employment history.", "employment_gap", ESSAY, None),
    ("Which of our values resonates with you the most, and why?", "company_values", ESSAY, None),
    ("Which of Stripe's operating principles resonates with you?", "company_values", ESSAY, "Stripe"),
    ("How would you contribute to diversity, equity and inclusion at Anthropic?", "dei_contribution", ESSAY,
     "Anthropic"),
    ("What would you hope to accomplish in your first 90 days?", "first_90_days", ESSAY, None),
    ("What would you build in your first month?", "first_90_days", ESSAY, None),
    ("What do you hope to learn from this internship?", "learn_from_role", ESSAY, None),
    ("Tell us about a time you used data to make a decision.", "bh_data", ESSAY, None),
    ("Describe a time you came up with a creative solution to a problem.", "bh_creativity", ESSAY, None),
    ("Tell me about a time you faced an ethical dilemma at work.", "bh_ethics", ESSAY, None),
    ("Walk us through your resume.", "about_you", ESSAY, None),
]

# Labels that must NOT be answered by a static rule.
NEGATIVE = [
    ("What are your salary expectations for this role?", "why_role"),
    ("What's your favorite open-source project and why?", "relevant_project"),
    ("Are you authorized to work in the US without sponsorship?", "authorized_to_work"),
    ("Do you require sponsorship?", "authorized_to_work"),
    # Live-form regressions: these were once answered by the wrong rule.
    ("When is the earliest you would want to start working with us?", "us_person"),
    ('What is the address from which you plan on working? If you would need to relocate, please type "relocating".',
     "willing_to_relocate"),
    ("As part of our interview process, we may use AI notetakers to transcribe conversations for accuracy and "
     "efficiency. Please see our candidate privacy policy for more information on how we process your data.",
     "privacy_ack"),
    ("What programming languages are you proficient in?", "languages_list"),
    # Open-question patterns must not swallow consent notices, EEO prompts or skill checks.
    ("As part of our interview process, we may use AI notetakers to transcribe conversations for accuracy and "
     "efficiency. Please see our candidate privacy policy for more information on how we process your data.",
     "ai_usage"),
    ("Please describe how you identify (gender, race or ethnicity) for our diversity reporting", "dei_contribution"),
    ("How many years of experience do you have with Python?", "skill_experience"),
    ("Tell us about a time you learned something quickly", "learned_recently"),
    ("Tell us about something you taught yourself", "bh_mentoring"),
]


class TestMatcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = Matcher.from_store(fresh_store(owner=cls))

    def test_real_world_labels(self):
        misses = []
        for label, want, ftype, company in CASES:
            got = self.m.match(label, field_type=ftype or "text", company=company)
            if got.question_id != want:
                misses.append(f"{label!r}: want {want}, got {got.question_id} ({got.status}, {got.candidates[:2]})")
        self.assertEqual(misses, [])

    def test_negative_cases(self):
        for label, wrong in NEGATIVE:
            self.assertNotEqual(self.m.match(label, field_type="textarea").question_id, wrong, label)

    def test_section_disambiguates_shared_labels(self):
        cases = [("Start date", None, "start_date"), ("Start date", "experience", "exp_start"),
                 ("Start date", "education", "edu_start"), ("Location", None, "current_location"),
                 ("Location", "experience", "exp_location"), ("Company", "experience", "exp_company"),
                 ("Company", "reference", "ref_company"), ("Name", "reference", "ref_name"), ("Name", None, "full_name"),
                 ("School", None, "edu_school"),
                 ("To (Actual or Expected) Year", "education", "edu_end")]   # Workday's education block
        for label, section, want in cases:
            self.assertEqual(self.m.match(label, section=section).question_id, want, (label, section))

    def test_unsectioned_ambiguous_label_is_uncertain(self):
        r = self.m.match("From")
        self.assertEqual(r.status, "uncertain")
        self.assertTrue({"edu_start", "exp_start"} <= {c for c, _ in r.candidates})

    def test_field_type_picks_file_or_text_question(self):
        self.assertEqual(self.m.match("Cover Letter", field_type="file").question_id, "cover_letter_file")
        self.assertEqual(self.m.match("Cover Letter", field_type="textarea").question_id, "cover_letter_text")
        self.assertEqual(self.m.match("Portfolio", field_type="file").question_id, "portfolio_file")
        self.assertEqual(self.m.match("Portfolio", field_type="url").question_id, "website")

    def test_essay_questions_never_answer_dropdowns(self):
        r = self.m.match("Why do you want to work here?", field_type="select")
        self.assertNotEqual(r.question_id, "why_company")

    def test_pattern_captures_original_case(self):
        self.assertEqual(self.m.match("How many years of experience do you have with PyTorch?").params,
                         {"skill": "PyTorch"})

    def test_company_name_becomes_placeholder(self):
        self.assertEqual(normalize("Why do you want to work at Acme Robotics?", company="Acme Robotics, Inc."),
                         "why do you want to work at {company}")


class TestCatalogIntegrity(unittest.TestCase):
    def test_ids_unique_and_tier3_is_ai(self):
        ids = [q["id"] for q in QUESTIONS]
        self.assertEqual(len(ids), len(set(ids)))
        for q in QUESTIONS:
            self.assertEqual(q["tier"] == 3, "ai" in q["answer"], q["id"])

    def test_no_alias_collisions_between_unsectioned_questions(self):
        """Two unsectioned questions sharing a phrasing (and field kind) would be ambiguous forever."""
        seen = {}
        clashes = []
        for q in QUESTIONS:
            if q["section"]:
                continue
            kind = "file" if q["kinds"] == ["file"] else "text"
            for a in [q["text"], *q["aliases"]]:
                key = (normalize(a), kind)
                if key in seen and seen[key] != q["id"]:
                    clashes.append((a, seen[key], q["id"]))
                seen[key] = q["id"]
        self.assertEqual(clashes, [])

    def test_fact_references_exist(self):
        from jobfill.catalog import FACTS
        keys = {f["key"] for f in FACTS}
        for q in QUESTIONS:
            if "fact" in q["answer"]:
                self.assertIn(q["answer"]["fact"], keys, q["id"])

    def test_derive_rules_exist(self):
        from jobfill.derive import RULES
        for q in QUESTIONS:
            if "derive" in q["answer"]:
                self.assertIn(q["answer"]["derive"], RULES, q["id"])


if __name__ == "__main__":
    unittest.main()
